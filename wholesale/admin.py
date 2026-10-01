import uuid
from django.utils import timezone
from datetime import timedelta, datetime, date, time
from django.http import JsonResponse, HttpResponseRedirect, HttpResponse
from django.core.exceptions import ValidationError
from urllib.parse import quote
from django.utils.html import format_html
from django.contrib import admin, messages
from django.utils.timezone import localtime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from django.shortcuts import redirect, get_object_or_404
from django.urls import reverse, path
from django.db import transaction
from django.db.models import Case, When, Value, BooleanField, Sum, Q
from django.core.cache import cache

from django.template.response import TemplateResponse


from unfold.admin import ModelAdmin, TabularInline
from django.utils.translation import gettext_lazy as _

from currency.models import Currency, CartItem
from wholesale.services.order_service import OrderService

from .models import (
    Shift,
    CashNode,
    StaffProfile,
    WholesaleOrder,
    CashBalance,
    CashMovement
)


from django.contrib.admin import SimpleListFilter


class DummyDateFilter(SimpleListFilter):
    title = "Дата"
    parameter_name = "date"

    def lookups(self, request, model_admin):
        # Возвращаем пустой кортеж, чтобы фильтр визуально скрылся из боковой панели Unfold
        return ()

    def queryset(self, request, queryset):
        # Ничего не делаем с queryset. Вся логика фильтрации по дате
        # уже написана вами вручную внутри метода get_queryset!
        return queryset

# ─── Request-level cache helpers ─────────────────────────────────────────────


_STAFF_CACHE_KEY = "_cached_staff"
_SHIFT_CACHE_KEY = "_cached_shift"
_MISSING = object()

admin.site.site_header = "ExPrivat Admin"  # Заголовок вверху
admin.site.site_title = "Админка"            # Заголовок во вкладке браузера
admin.site.index_title = "Добро пожаловать"


def get_shift_report(shift):
    """
    Собирает отчет о смене: операции, балансы и прибыль.

    Прибыль рассчитывается как разница между эквивалентом кассы 
    в конце дня и начале дня в гривнах.
    Курсы берутся из API (поле sell - по какому курсу мы можем продать).
    """

    orders = WholesaleOrder.objects.filter(
        shift=shift).select_related('currency')

    # Суммируем по типам операций
    totals = orders.aggregate(
        total_buy=Sum("amount_currency", filter=Q(order_type="buy")),
        total_sell=Sum("amount_currency", filter=Q(order_type="sell")),
        total_in=Sum("amount_currency", filter=Q(order_type="in")),
        total_out=Sum("amount_currency", filter=Q(order_type="out")),
    )
    # Агрегация по каждой валюте отдельно для таблицы combined_balances
    currency_stats = orders.values('currency__code').annotate(
        buy=Sum("amount_currency", filter=Q(order_type="buy")),
        sell=Sum("amount_currency", filter=Q(order_type="sell"))
    )
    # Превращаем в словарь { 'usd': {'buy': 100, 'sell': 50}, ... }
    stats_map = {item['currency__code'].lower(
    ): item for item in currency_stats}

    currency_rates = CartItem.objects.filter(
        exchanger_id=1).values('currency__code', 'sell')

    # Строим словарь курсов, конвертируя строки в Decimal
    rates_dict = {}
    for item in currency_rates:
        try:
            rate = Decimal(item['sell'])
        except (InvalidOperation, TypeError):
            rate = Decimal('0.0')
        rates_dict[item['currency__code'].lower()] = rate

    # Гривна всегда имеет курс 1.0
    rates_dict['uah'] = Decimal('1.0')

    rates_dict['usdold'] = rates_dict.get('usd', Decimal('0.0'))

    def get_total_uah_equivalent_from_dict(balances_dict):
        """
        Конвертирует балансы из JSON словаря в эквивалент UAH.
        balances_dict формат: {"USD": {"amount": "100.00", ...}, ...}
        """
        total_in_uah = Decimal('0.0')
        for currency_code, balance_data in balances_dict.items():
            currency_code_lower = currency_code.lower()
            try:
                amount = Decimal(str(balance_data.get('amount', 0)))
            except (InvalidOperation, TypeError):
                amount = Decimal('0.0')

            rate = rates_dict.get(currency_code_lower, Decimal('0.0'))
            total_in_uah += amount * rate

        return total_in_uah

    def get_total_uah_equivalent_from_queryset(balances_queryset):
        """
        Конвертирует балансы из QuerySet в эквивалент UAH.
        """
        total_in_uah = Decimal('0.0')
        for b in balances_queryset:
            currency_code = b.currency.code.lower()
            rate = rates_dict.get(currency_code, Decimal('0.0'))
            total_in_uah += b.balance * rate

        return total_in_uah

    # Получаем текущие балансы кассы
    current_balances = CashBalance.objects.filter(
        node=shift.node).select_related('currency')

    # Считаем утренний эквивалент в гривне из сохраненного слепка
    if shift.morning_balances:
        morning_total_uah = get_total_uah_equivalent_from_dict(
            shift.morning_balances)
    else:
        morning_total_uah = Decimal('0.0')

    # Считаем вечерний эквивалент в гривне из текущих балансов
    evening_total_uah = get_total_uah_equivalent_from_queryset(
        current_balances)

    # Вычитаем эффект коллекций, чтобы они не влияли на прибыль
    # ⚠️ Используем дату из shift.opened_at, чтобы поймать все операции смены
    shift_date = timezone.localdate(
        shift.opened_at) if shift.opened_at else timezone.localdate()

    # 2. Расчет прибыли (Profit)
    # Прибыль = (Вечерний баланс + Инкассации) - (Утренний баланс + Подкрепления)
    # Это математически точнее: баланс к концу дня должен включать всё.
    shift_date = timezone.localdate(
        shift.opened_at) if shift.opened_at else timezone.localdate()
    movements = CashMovement.objects.filter(
        node=shift.node, created_at__date=shift_date)

    add_sum = Decimal('0')
    collect_sum = Decimal('0')
    for cm in movements:
        rate = rates_dict.get((cm.currency.code or '').lower(), Decimal('0'))
        if cm.movement_type == 'add':
            add_sum += Decimal(cm.amount or 0) * rate
        elif cm.movement_type == 'collect':
            collect_sum += Decimal(cm.amount or 0) * rate

    profit = (evening_total_uah + collect_sum) - (morning_total_uah + add_sum)

    # Сортируем morning_balances по Currency.sort_order
    sorted_morning_balances = sort_balances_dict(
        shift.morning_balances) if shift.morning_balances else {}

    # Собираем объединённый список балансов: утро + конец смены
    combined_balances = []
    # morning map: code(lower) -> Decimal(amount)
    morning_map = {}
    if shift.morning_balances:
        for code, data in sorted_morning_balances.items():
            try:
                morning_map[code.lower()] = Decimal(str(data.get('amount', 0)))
            except (InvalidOperation, TypeError):
                morning_map[code.lower()] = Decimal('0')

    # end map from current_balances queryset
    end_map = {}
    currency_names = {}
    for b in current_balances:
        c = getattr(b, 'currency', None)
        if c and c.code:
            code = c.code.lower()
            end_map[code] = getattr(b, 'balance', Decimal('0')) or Decimal('0')
            currency_names[code] = getattr(c, 'name', c.code.upper())

    # union of codes, отсортированный по sort_order валют
    order_map = get_currency_order_map()

    # 3. Формирование combined_balances
    combined_balances = []
    all_codes = sorted(set(list(morning_map.keys()) +
                       list(end_map.keys())), key=lambda c: order_map.get(c, 999))

    for code in all_codes:
        stats = stats_map.get(code, {'buy': 0, 'sell': 0})
        combined_balances.append({
            "currency_code": code.upper(),
            "currency_name": currency_names.get(code, code.upper()),
            "morning": morning_map.get(code, Decimal('0')),
            "end": end_map.get(code, Decimal('0')),
            'buy': stats.get('buy') or Decimal('0'),
            'sell': stats.get('sell') or Decimal('0'),
        })
    # приводим combined_balances к порядку Currency.sort_order (страховка)
    combined_balances = sort_combined_balances_list(combined_balances)

    return {
        "cashier": shift.staff.user.first_name or shift.staff.user.username,
        "staffprofile": shift.staff,
        "opened_at": localtime(shift.opened_at),
        "closed_at": localtime(shift.closed_at) if shift.closed_at else None,
        "totals": {
            "buy": totals.get("total_buy") or Decimal("0"),
            "sell": totals.get("total_sell") or Decimal("0"),
            "in": totals.get("total_in") or Decimal("0"),
            "out": totals.get("total_out") or Decimal("0"),
        },
        "morning_balances": sorted_morning_balances,
        "morning_total_uah": morning_total_uah,
        "evening_total_uah": evening_total_uah,
        "profit": profit,
        "balances": list(current_balances.select_related('currency').values(
            "currency__code", "currency__name", "balance"
        )),
        "combined_balances": combined_balances,
        "orders": orders.values(
            "id", "order_type", "currency__code", "amount_currency",
            "rate", "amount_base", "comment", "created_at"
        ).order_by("-created_at"),
        "shift": shift,
    }


def get_or_create_shift(staff, node):

    now = timezone.now()

    today_start = now.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )

    today_end = today_start + timedelta(days=1)

    # 1. Открытая смена сегодня
    open_shift = (
        Shift.objects
        .filter(
            staff=staff,
            node=node,
            is_open=True,
            opened_at__gte=today_start,
            opened_at__lt=today_end,
        )
        .first()
    )

    if open_shift:
        return open_shift

    # 2. Закрытая смена сегодня
    closed_shift = (
        Shift.objects
        .filter(
            staff=staff,
            node=node,
            is_open=False,
            opened_at__gte=today_start,
            opened_at__lt=today_end,
        )
        .order_by("-opened_at")
        .first()
    )

    if closed_shift:
        closed_shift.is_open = True
        closed_shift.closed_at = None
        closed_shift.save(update_fields=[
            "is_open",
            "closed_at",
        ])
        return closed_shift

    # 3. Закрываем ВСЕ старые открытые
    (
        Shift.objects
        .filter(
            staff=staff,
            node=node,
            is_open=True,
        )
        .update(
            is_open=False,
            closed_at=now,
        )
    )

    # 4. Создаём новую
    try:
        shift = Shift.objects.create(
            staff=staff,
            node=node,
            is_open=True,
        )
    except Exception as e:
        print("SHIFT CREATE ERROR", e)
        raise
    return shift


def get_staff(request):
    """Кэшируем staffprofile на весь request, чтобы не ходить в БД повторно."""
    if not hasattr(request, _STAFF_CACHE_KEY):
        setattr(request, _STAFF_CACHE_KEY, getattr(
            request.user, "staffprofile", None))
    return getattr(request, _STAFF_CACHE_KEY)


def get_open_shift(staff, request=None):
    """
    Возвращает открытую смену для сотрудника.

    Поддерживает множественные открытые смены у одного сотрудника на разных
    кассах — если в `request` есть `shift__node__id` (GET) или
    `selected_node_id` (session), возвращаем смену именно для этой кассы.

    Кэшируем результаты на уровне запроса по ключу, зависящему от node_id,
    чтобы разные node-выборы не перезаписывали друг друга.
    """
    node_id = None
    if request is not None:
        # prefer explicit URL param, fallback to session-stored selection
        node_id = request.GET.get(
            "shift__node__id") or request.session.get("selected_node_id")

        # prepare cache map
        cache_map = getattr(request, _SHIFT_CACHE_KEY, None)
        if cache_map is None:
            cache_map = {}
            setattr(request, _SHIFT_CACHE_KEY, cache_map)

        cache_key = f"node:{node_id}" if node_id is not None else "node:any"
        if cache_key in cache_map:
            return cache_map[cache_key]

    result = _find_open_shift(staff, node_id=node_id)

    if request is not None:
        getattr(request, _SHIFT_CACHE_KEY)[cache_key] = result

    return result


def _get_today_range():
    """Возвращает диапазон начала и конца текущего дня в локальном часовом поясе."""
    tz = timezone.get_current_timezone()
    today = timezone.localdate()
    today_start = timezone.make_aware(datetime.combine(today, time.min), tz)
    tomorrow_start = timezone.make_aware(
        datetime.combine(today + timedelta(days=1), time.min), tz)
    return today_start, tomorrow_start


def _find_open_shift(staff, node_id=None):
    """Только ищет открытую смену на сегодня. Не создаёт.

    Если указан `node_id`, возвращает открытую смену конкретно для этой кассы.
    """
    if not staff or not staff.is_active:
        return None

    today_start, tomorrow_start = _get_today_range()

    qs = Shift.objects.filter(
        staff=staff,
        is_open=True,
        opened_at__gte=today_start,
        opened_at__lt=tomorrow_start,
    )

    if node_id:
        try:
            node_pk = int(node_id)
            qs = qs.filter(node_id=node_pk)
        except (TypeError, ValueError):
            # ignore invalid node_id and fall back to any open shift
            pass

    return qs.select_related("node", "node__exchange_point").first()


def _invalidate_shift_cache(request):
    """Сбрасывает кэш смены на request после создания/закрытия."""
    if hasattr(request, _SHIFT_CACHE_KEY):
        delattr(request, _SHIFT_CACHE_KEY)


# ----------------- Currency ordering helpers -----------------
def get_currency_order_map():
    """Возвращает словарь code.lower() -> sort_order для всех валют."""
    return {c.code.lower(): c.sort_order for c in Currency.objects.all()}


def sort_balances_dict(balances_dict):
    """Сортирует словарь балансов валют по порядку `Currency.sort_order`.

    Ожидает mappings вида {"USD": {...}, ...} и возвращает новый Ordered dict.
    """
    if not balances_dict:
        return {}
    order = get_currency_order_map()
    return dict(sorted(
        balances_dict.items(),
        key=lambda item: order.get(item[0].lower(), 999)
    ))


def sort_combined_balances_list(combined_list):
    """Сортирует список combined_balances по Currency.sort_order."""
    if not combined_list:
        return []
    order = get_currency_order_map()
    return sorted(
        combined_list,
        key=lambda b: order.get((b.get('currency_code') or '').lower(), 999)
    )


def build_operation_table(node, selected_date, morning_balances):
    """Build the running-balance table shared by cashiers and seniors."""
    if not node:
        return [], []

    def get_operation_currency_code(currency):
        code = (
            currency
            if isinstance(currency, str)
            else getattr(currency, "code", "")
        ) or ""
        code = code.lower()
        if "-" not in code:
            return code
        parts = code.split("-", 1)
        return parts[1] if parts[0] == "usd" else parts[0]

    orders = list(
        WholesaleOrder.objects
        .filter(
            shift__node=node,
            created_at__date=selected_date,
            is_reversed=False,
        )
        .annotate(
            can_reverse=Case(
                When(
                    created_at__gte=timezone.now() - timedelta(hours=8),
                    is_reversed=False,
                    then=Value(True),
                ),
                default=Value(False),
                output_field=BooleanField(),
            )
        )
        .select_related("currency", "base_currency")
        .order_by("created_at", "id")
    )
    codes = {"uah", "usd", "usdnew"}
    codes.update(
        code.lower()
        for code in CashBalance.objects.filter(node=node)
        .values_list("currency__code", flat=True)
        if code
    )
    codes.update(
        get_operation_currency_code(order.currency)
        for order in orders
        if order.currency and order.currency.code
    )
    codes.update(
        order.base_currency.code.lower()
        for order in orders
        if order.base_currency and order.base_currency.code
    )
    ordered_codes = sorted(
        codes,
        key=lambda code: get_currency_order_map().get(code, 999),
    )
    other_codes = [
        code for code in ordered_codes
        if code not in {"usd", "usdnew", "uah"}
    ]
    balances = {code: Decimal("0") for code in ordered_codes}

    for code, data in (morning_balances or {}).items():
        code = str(code).lower()
        if code in balances:
            try:
                balances[code] = Decimal(str(data.get("amount") or 0))
            except (InvalidOperation, TypeError):
                balances[code] = Decimal("0")

    opening_balances = balances.copy()

    def make_row(order=None):
        return {
            "obj": order,
            "total_usd": balances.get("usd", 0) + balances.get("usdnew", 0),
            "usdnew": balances.get("usdnew", 0),
            "uah": balances.get("uah", 0),
            "other_balances": [
                {"code": code, "value": balances.get(code, 0)}
                for code in other_codes
            ],
        }

    rows = [{"is_morning": True, **make_row()}]
    for order in orders:
        amount = Decimal(order.amount_currency or 0)
        code = get_operation_currency_code(order.currency)
        if code in balances:
            if order.order_type in ("buy", "in", "add"):
                balances[code] += amount
            elif order.order_type in ("sell", "out", "collect"):
                balances[code] -= amount

        if order.order_type in ("buy", "sell"):
            base_code = (
                order.base_currency.code.lower()
                if order.base_currency and order.base_currency.code
                else "uah"
            )
            if base_code in balances:
                amount_base = Decimal(order.amount_base or 0)
                balances[base_code] += (
                    -amount_base if order.order_type == "buy" else amount_base
                )
        rows.append({"is_morning": False, **make_row(order)})

    visible_codes = {
        code for code in other_codes
        if opening_balances.get(code, Decimal("0")) != 0
        or balances.get(code, Decimal("0")) != 0
    }
    for row in rows:
        row["other_balances"] = [
            balance for balance in row["other_balances"]
            if balance["code"] in visible_codes
        ]

    columns = [
        {"code": code, "title": code.upper()}
        for code in other_codes
        if code in visible_codes
    ]
    return rows, columns

# -------------------------------------------------------------


class CashBalanceInline(TabularInline):
    model = CashBalance
    extra = 1
    classes = ["balance-tab"]

    # def get_readonly_fields(self, request, obj=None):
    #     readonly = list(super().get_readonly_fields(request, obj))

    #     # Superusers могут редактировать всё
    #     if request.user.is_superuser:
    #         return readonly

    #     # Проверяем есть ли у пользователя открытая смена
    #     staff = get_staff(request)
    #     if not staff:
    #         return list(self.model._meta.get_fields())

    #     # Получаем открытую смену кассира
    #     open_shift = get_open_shift(staff, request)

    #     # Если смены нет или эта касса не та, которая открыта - всё readonly
    #     if not open_shift:
    #         return list(self.model._meta.get_fields())

    #     # Разрешаем редактировать баланс только для той кассы, где открыта смена
    #     if obj and obj.node_id != open_shift.node_id:
    #         return list(self.model._meta.get_fields())

    #     # Для USD валют только открёвший смену кассир может редактировать
    #     if obj and obj.currency and 'usd' in obj.currency.code.lower():
    #         readonly.append('balance')

    #     return readonly

    # def has_add_permission(self, request, obj=None):
    #     # Запрещаем добавлять новые балансы через inline
    #     return False

    # def has_delete_permission(self, request, obj=None):
    #     # Запрещаем удалять балансы через inline
    #     return False


@admin.register(CashNode)
class CashNodeAdmin(ModelAdmin):
    tabs = True
    inlines = [CashBalanceInline]

    list_display = ("node_type", "name", "exchange_point", "is_active")
    list_display_links = ["node_type", "name", "exchange_point"]
    fieldsets = (
        (
            _("Касса"),
            {
                "classes": ["tab"],
                "fields": [
                    "node_type",
                    "name",
                    "exchange_point",
                    "is_active",
                ],
            },
        ),
        (
            _("Баланс"),
            {
                "classes": ["balance-tab"],
                "fields": (),
            },
        ),
    )

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)

        if not request.user.is_superuser:
            # Возвращаем всё, кроме вкладки "Касса"
            return tuple(fs for fs in fieldsets if fs[0] != _("Касса"))

        return fieldsets

    def get_queryset(self, request):
        qs = super().get_queryset(request)

        # Superusers видят всё
        if request.user.is_superuser:
            return qs

        staff = get_staff(request)
        if not staff:
            return qs.none()

        # Показываем все кассы, привязанные к сотруднику (даже is_active=False)
        allowed_nodes = staff.nodes.all().values_list("id", flat=True)
        return qs.filter(id__in=allowed_nodes)


@admin.register(Shift)
class WholesaleShift(ModelAdmin):
    readonly_fields = ('opened_at', 'closed_at')
    fields = ("staff", "node", "opened_at", "is_open", "morning_balances")
    search_fields = ["staff"]
    list_display = ("staff", "node", "opened_at", "is_open")
    list_editable = ["is_open"]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("staff", "node")

    def save_model(self, request, obj, form, change):
        staff = get_staff(request)
        obj.staff = staff
        obj.is_open = True
        super().save_model(request, obj, form, change)


@admin.register(StaffProfile)
class WholesaleStaffProfile(ModelAdmin):
    list_editable = ["role", "is_active"]
    fields = ("role", "nodes", "user", "telephone", "is_active")
    list_display = ("get_first_name",
                    "get_last_name", "telephone", "is_active", "role")

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        formfield = super().formfield_for_manytomany(db_field, request, **kwargs)

        # Перехватываем именно поле nodes
        if db_field.name == "nodes":
            # Получаем существующие классы от Unfold (если они есть)
            existing_classes = formfield.widget.attrs.get('class', '')
            # Добавляем скролл и ограничиваем высоту
            formfield.widget.attrs['class'] = f"{existing_classes} overflow-y-auto max-h-48".strip()

        return formfield

    @admin.display(description="Имя")
    def get_first_name(self, obj):
        return obj.user.first_name

    @admin.display(description="Фамилия")
    def get_last_name(self, obj):
        return obj.user.last_name


@admin.register(WholesaleOrder)
class WholesaleOrderAdmin(ModelAdmin):
    show_full_result_count = False

    list_filter = (DummyDateFilter,)

    list_select_related = (
        "currency",
        "shift",
        "shift__node",
        "shift__node__exchange_point",
    )
    change_list_template = "admin/wholesale/order_change_list.html"

    list_display = (
        "id_and_time",
        "currency",
        "order_type_badge",
        "amount_currency",
        "rate",
        "amount_base",
        "comment",
        # "profit",
        "reverse_action",
    )
    readonly_fields = ("created_at",)

    def id_and_time(self, obj):
        local_time = timezone.localtime(
            obj.created_at) if obj.created_at else None

        time_str = local_time.strftime("%H:%M") if local_time else "--:--"

        return format_html(
            '<div class="flex">'
            '<span class="font-bold text-gray-900 dark:text-white">{}</span> / '
            '<span class="text-gray-500">{}</span>'
            '</div>',
            obj.id, time_str
        )

    id_and_time.short_description = "№ / Время"  # Заголовок колонки
    id_and_time.admin_order_field = "id"         # По какому полю сортировать

    # ── permissions ──────────────────────────────────────────────────────────

    def has_add_permission(self, request):
        """
        Добавлять операции можно только при открытой смене.
        Но просмотр истории должен работать всегда.
        """
        staff = get_staff(request)

        # senior / superuser всегда могут видеть кнопку
        if request.user.is_superuser or (staff and staff.role == "senior"):
            return True

        # Только для страницы создания операции
        if request.path.endswith("/add/"):
            return get_open_shift(staff, request) is not None

        return True

    def has_view_permission(self, request, obj=None):
        return True

    # def has_add_permission(self, request):
    #     return get_open_shift(get_staff(request), request) is not None

    # ── queryset ─────────────────────────────────────────────────────────────

    def get_queryset(self, request):
        # ПОЛУЧАЕМ ДАТУ ИЗ GET-ПАРАМЕТРА
        date_str = request.GET.get("date")
        try:
            selected_date = date.fromisoformat(
                date_str) if date_str else timezone.localdate()
        except (ValueError, TypeError):
            selected_date = timezone.localdate()

        # ВЫЧИСЛЯЕМ НАЧАЛО И КОНЕЦ ДНЯ
        tz = timezone.get_current_timezone()
        day_start = timezone.make_aware(
            # ← ИСПОЛЬЗУЕМ ИМПОРТИРОВАННЫЙ time
            datetime.combine(selected_date, time.min), tz
        )
        day_end = day_start + timedelta(days=1)

        # БАЗОВЫЙ QUERYSET
        qs = (
            super()
            .get_queryset(request)
            .select_related("currency", "shift", "shift__node")
        )

        qs = qs.annotate(
            can_reverse=Case(
                When(
                    created_at__gte=timezone.now() - timedelta(hours=8),
                    is_reversed=False,
                    then=Value(True)
                ),
                default=Value(False),
                output_field=BooleanField()
            )
        )

        # ЕСЛИ node_id ПЕРЕДАН В GET
        node_id = request.GET.get("shift__node__id")
        node_id_int = None
        if node_id and str(node_id).isdigit():
            node_id_int = int(node_id)

        if node_id_int is not None:
            qs = qs.filter(
                shift__node_id=node_id_int,
                created_at__gte=day_start,
                created_at__lt=day_end,
            )

            if request.user.is_superuser:
                return qs

            staff = get_staff(request)
            if not staff:
                return qs.none()

            # Seniors can view any node's orders for the selected date without opening a shift
            if staff.role == "senior":
                return qs

            allowed_node_ids = list(staff.nodes.values_list("id", flat=True))
            open_shift = get_open_shift(staff, request)
            if open_shift and open_shift.node_id == node_id_int:
                return qs
            return qs if node_id_int in allowed_node_ids else qs.none()

        # SUPERUSER БЕЗ node_id
        if request.user.is_superuser:
            return qs.filter(
                created_at__gte=day_start,
                created_at__lt=day_end,
            )

        staff = get_staff(request)
        if not staff:
            return qs.none()

        # SENIOR: allow viewing across all nodes for the selected date
        if staff.role == "senior":
            # if specific node requested, filter by it
            if node_id_int is not None:
                return qs.filter(
                    shift__node_id=node_id_int,
                    created_at__gte=day_start,
                    created_at__lt=day_end,
                )
            # no node specified — show all nodes for the selected date (including past days)
            return qs.filter(
                created_at__gte=day_start,
                created_at__lt=day_end,
            )

        # ОБЫЧНЫЙ КАССИР
        shift = get_open_shift(staff, request)
        node_id = request.GET.get(
            "shift__node__id") or request.session.get("selected_node_id")

        if node_id:
            return qs.filter(
                shift__node_id=node_id,
                created_at__gte=day_start,
                created_at__lt=day_end,
            )

        if not shift:
            return qs.none()

        return qs.filter(
            shift=shift,
            created_at__gte=day_start,
            created_at__lt=day_end,
        )

    # ── custom URLs ───────────────────────────────────────────────────────────

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "exchange/",
                self.admin_site.admin_view(self.exchange_view),
                name="wholesale_exchange",
            ),
            path(
                "get-balance/",
                self.admin_site.admin_view(self.get_balance_view),
                name="wholesale_get_balance",
            ),
            path(
                "select-node/",
                self.admin_site.admin_view(self.select_node_view),
                name="wholesale_select_node",
            ),
            path(
                "reverse/<int:order_id>/",
                self.admin_site.admin_view(self.reverse_order_view),
                name="reverse_order",
            ),
            path(
                "close-shift/",
                self.admin_site.admin_view(self.close_shift_view),
                name="close_shift",
            ),
            path(
                "shift-report-xls/",
                self.admin_site.admin_view(self.shift_report_xls_view),
                name="shift_report_xls",
            )
            # path(
            #     "shift-report/",
            #     self.admin_site.admin_view(self.shift_report_view),
            #     name="shift_report",
            # )
        ]
        return custom + urls

    # ── select node & open shift ──────────────────────────────────────────────

    def select_node_view(self, request):
        """
        POST: получаем node_id и открываем смену на выбранную кассу.
        Поддерживает просмотр истории заказов за выбранный день.

        Параметры:
        - node_id (обязательный): ID кассы
        - date (опциональный): дата для просмотра истории (Y-m-d формат)

        Возвращает JSON с shift_id, node_name и redirect_url
        """
        if request.method != "POST":
            return JsonResponse({"success": False, "error": "Метод не поддерживается"}, status=405)

        staff = get_staff(request)
        if not staff or not staff.is_active:
            return JsonResponse({"success": False, "error": "Профиль не найден"}, status=403)

        node_id = request.POST.get("node_id")
        if not node_id:
            return JsonResponse({"success": False, "error": "Касса не выбрана"}, status=400)

        # Проверяем что касса принадлежит этому сотруднику и активна
        node = staff.nodes.filter(pk=node_id, is_active=True).first()
        if not node:
            return JsonResponse(
                {"success": False, "error": "Касса недоступна"},
                status=403
            )

        # Проверяем есть ли уже открытая смена для этой кассы
        existing_shift = Shift.objects.filter(
            staff=staff,
            node=node,
            is_open=True,
        ).first()

        # Если уже есть открытая смена — используем её
        if existing_shift:
            request.session["selected_node_id"] = node.pk
            request.session.modified = True
            _invalidate_shift_cache(request)

            # Если передана дата, перенаправляем с параметром ?date=
            date_param = request.POST.get("date")

            redirect_url = f"?shift__node__id={node.pk}"
            if date_param:
                redirect_url += f"&date={date_param}"

            return JsonResponse({
                "success": True,
                "shift_id": existing_shift.pk,
                "node_name": node.name,
                "redirect_url": redirect_url,
            })

        shift = get_or_create_shift(staff, node)

        request.session["selected_node_id"] = node.pk
        request.session.modified = True
        _invalidate_shift_cache(request)

        # Если передана дата, перенаправляем с параметром ?date=
        date_param = request.POST.get("date")
        redirect_url = f"?shift__node__id={node.pk}"
        if date_param:
            redirect_url += f"&date={date_param}"

        return JsonResponse({
            "success": True,
            "shift_id": shift.pk,
            "node_name": node.name,
            "redirect_url": redirect_url,
        })

    def lookup_allowed(self, lookup, value):

        if lookup == 'shift__node__id':
            return True
        return super().lookup_allowed(lookup, value)

    # ── changelist ────────────────────────────────────────────────────────────

    def changelist_view(self, request, extra_context=None):
        staff = get_staff(request)

        shift = None
        balances = None
        available_nodes = []
        needs_node_selection = False
        all_nodes = []
        selected_node = None
        usd_total = 0

        node_id = request.GET.get("shift__node__id")

        is_senior = (
            request.user.is_superuser or
            (staff and staff.role == "senior")
        )
        selected_date = request.GET.get("date")
        today = timezone.localdate()
        if selected_date:
            try:
                selected_date = date.fromisoformat(selected_date)
            except ValueError:
                selected_date = today
        else:
            selected_date = today

        prev_date = selected_date - timedelta(days=1)
        next_date = selected_date + timedelta(days=1)

        extra_context = extra_context or {}

        # ─── 1. РЕДИРЕКТ ЕСЛИ ЕСТЬ РОВНО 1 ОТКРЫТАЯ СМЕНА ──────────────
        if selected_date == today and staff and not is_senior and not node_id:
            open_shifts = Shift.objects.filter(staff=staff, is_open=True)

            if open_shifts.count() == 1:
                active_node_id = open_shifts.first().node_id
                messages.success(
                    request, "Автоматический возврат к открытой смене.")
                return HttpResponseRedirect(f"?shift__node__id={active_node_id}")

        # Если пользователь смотрит историю за вчера, мы НЕ должны открывать новую смену!
        if selected_date == today and node_id and request.user.is_authenticated:
            try:
                if staff:
                    # Ищем кассу среди доступных сотруднику
                    node = staff.nodes.filter(
                        pk=node_id, is_active=True).first()

                    if node:
                        # 1. Проверяем, установлена ли эта касса в сессии как активная
                        # Если нет — обновляем сессию
                        if str(request.session.get("selected_node_id")) != str(node_id):
                            request.session["selected_node_id"] = node.pk
                            request.session.modified = True
                            _invalidate_shift_cache(request)

                        # 2. Проверяем открытую смену на СЕГОДНЯ
                        now = timezone.now()
                        today_start = now.replace(
                            hour=0, minute=0, second=0, microsecond=0)

                        has_open_shift = Shift.objects.filter(
                            staff=staff,
                            node=node,
                            is_open=True,
                            opened_at__gte=today_start
                        ).exists()

                        # 3. Если смены нет — вызываем вашу логику создания/закрытия старых
                        if not has_open_shift:
                            get_or_create_shift(staff, node)
                            messages.success(
                                request, f"Автоматически открыта новая смена для: {node.name}")

            except Exception as e:
                # Логируем ошибку, чтобы админка не упала при сбое смены
                messages.error(
                    request, f"Ошибка при автоматической обработке смены: {e}")

        if staff and staff.is_active:
            # shift = get_open_shift(staff, request)
            available_nodes = list(
                staff.nodes.filter(is_active=True).select_related(
                    "exchange_point")
            )

            if node_id and not is_senior:
                try:
                    nid = int(node_id) if str(node_id).isdigit() else None
                except Exception:
                    nid = None

                # if nid and staff.nodes.filter(pk=nid).exists():
                #     current_shift = get_open_shift(staff, request)
                #     node = CashNode.objects.filter(
                #         pk=nid, is_active=True).first()

                #     if current_shift:
                #         # Update the node on the existing open shift instead of creating a new one
                #         if current_shift.node_id != nid and node:
                #             current_shift.node = node
                #             current_shift.save(update_fields=["node"])
                #             _invalidate_shift_cache(request)
                #         else:
                #             shift = current_shift
                #     else:
                #         # No open shift — create a new one (fallback)
                #         if node:
                #             shift = Shift.objects.create(
                #                 staff=staff,
                #                 node=node,
                #                 is_open=True,
                #                 opened_at=timezone.now(),
                #             )
                #             _invalidate_shift_cache(request)

            if not shift:
                shift = get_open_shift(staff, request)

            if not is_senior:
                if shift:
                    balances = (
                        CashBalance.objects
                        .filter(node=shift.node)
                        .select_related("currency")
                    )

                    # 💰 USD total
                    usd_total = (
                        balances
                        .filter(currency__code__icontains="usd")
                        .aggregate(total=Sum("balance"))
                        .get("total") or 0
                    )

                else:
                    needs_node_selection = len(available_nodes) > 0

        if is_senior:

            nodes_with_open_shift = set(
                Shift.objects
                # .filter(is_open=True, opened_at__date=selected_date)
                .filter(is_open=False, opened_at__date=selected_date)
                .values_list("node_id", flat=True)
            )

            all_nodes = list(
                CashNode.objects
                .filter(pk__in=nodes_with_open_shift, is_active=True)
                .select_related("exchange_point")
                .order_by("pk")
            )

            # If there were no nodes with shifts that day, fall back to all active nodes
            if not all_nodes:
                all_nodes = list(
                    CashNode.objects.filter(is_active=True).select_related(
                        "exchange_point").order_by("pk")
                )

            # Persist senior's selection in session so it stays across pages (optional)
            if node_id:
                try:
                    request.session["selected_node_id"] = int(node_id)
                except Exception:
                    request.session["selected_node_id"] = None

            node_id = request.session.get("selected_node_id")
            if node_id:
                try:
                    node_id = int(node_id)
                    selected_node = next(
                        (n for n in all_nodes if n.pk == node_id),
                        None
                    )
                except ValueError:
                    selected_node = None

            # Do NOT auto-default or redirect senior to a single node — allow viewing across all nodes

            balances = (
                CashBalance.objects
                .filter(node=selected_node)
                .select_related("currency")
            ) if selected_node else None
            # Безопасный подсчет USD
            usd_total = 0
            if balances is not None:
                try:
                    usd_total = (
                        balances
                        .filter(currency__code__icontains="usd")
                        .aggregate(total=Sum("balance"))
                        .get("total") or 0
                    )
                except Exception as e:
                    # В случае ошибки просто оставляем usd_total = 0,
                    # и НЕ делаем redirect(".."), чтобы страница загрузилась!
                    usd_total = 0

        # ── Получение утреннего баланса ──
        morning_balances = None

        if is_senior and selected_node:
            shift_with_morning = (
                Shift.objects
                .filter(node=selected_node, opened_at__date=selected_date)
                .select_related("node")
                .first()
            )

            if shift_with_morning and shift_with_morning.morning_balances:
                morning_balances = sort_balances_dict(
                    shift_with_morning.morning_balances
                )

        elif shift:
            if shift.morning_balances:
                morning_balances = sort_balances_dict(
                    shift.morning_balances
                )
        if not is_senior and shift:
            selected_node = getattr(shift, 'node', None)

        if not is_senior and node_id:
            selected_node = CashNode.objects.filter(
                pk=node_id,
                is_active=True,
            ).first()

        if selected_node:
            selected_shift = (
                Shift.objects
                .filter(node=selected_node, opened_at__date=selected_date)
                .order_by("-opened_at")
                .first()
            )
            if selected_shift and selected_shift.morning_balances:
                morning_balances = sort_balances_dict(
                    selected_shift.morning_balances
                )

        operation_rows, operation_columns = build_operation_table(
            selected_node,
            selected_date,
            morning_balances,
        )

        # selected_date = request.GET.get("date")
        # print("SELECTED DATE", selected_date)
        # if selected_date:
        #     try:
        #         selected_date = date.fromisoformat(selected_date)
        #     except ValueError:
        #         selected_date = timezone.localdate()
        # else:
        #     selected_date = timezone.localdate()

        # today = timezone.localdate()
        # prev_date = selected_date - timedelta(days=1)
        # next_date = selected_date + timedelta(days=1)

        extra_context["selected_date"] = selected_date
        extra_context["today"] = today
        extra_context["prev_date"] = prev_date
        extra_context["next_date"] = next_date

        extra_context["current_shift"] = shift
        extra_context["current_balances"] = balances
        extra_context["morning_balances"] = morning_balances
        extra_context["available_nodes"] = available_nodes
        extra_context["needs_node_selection"] = needs_node_selection
        extra_context["auto_select_node"] = (
            needs_node_selection and len(available_nodes) == 1
        )
        extra_context["all_nodes"] = all_nodes
        extra_context["selected_node"] = selected_node
        # ← Ключевое: если нет ?node= передаём pk первой кассы
        # чтобы Django правильно рендерил активный класс без JS
        extra_context["selected_node_id"] = selected_node.pk if selected_node else None
        extra_context["selected_node_type"] = selected_node.node_type if selected_node else None
        extra_context["is_senior"] = is_senior
        extra_context["usd_total"] = usd_total
        extra_context["operation_rows"] = operation_rows
        extra_context["operation_columns"] = operation_columns

        return super().changelist_view(request, extra_context=extra_context)

    # def changelist_view(self, request, extra_context=None):
    #     staff = get_staff(request)

    #     shift = None
    #     balances = None
    #     available_nodes = []
    #     needs_node_selection = False
    #     all_nodes = []
    #     selected_node = None
    #     usd_total = 0

    #     node_id = request.GET.get("shift__node__id")
    #     is_senior = (
    #         request.user.is_superuser or
    #         (staff and staff.role == "senior")
    #     )

    #     # ─── ИСПРАВЛЕНИЕ №1: ПАРСИНГ ДАТЫ В САМОМ НАЧАЛЕ ──────────────────
    #     selected_date = request.GET.get("date")
    #     today = timezone.localdate()
    #     if selected_date:
    #         try:
    #             selected_date = date.fromisoformat(selected_date)
    #         except ValueError:
    #             selected_date = today
    #     else:
    #         selected_date = today

    #     prev_date = selected_date - timedelta(days=1)
    #     next_date = selected_date + timedelta(days=1)

    #     extra_context = extra_context or {}

    #     # ─── ИСПРАВЛЕНИЕ №2: ОГРАНИЧЕНИЕ АВТОРЕДИРЕКТА ТОЛЬКО ДЛЯ СЕГОДНЯ ───
    #     if selected_date == today and staff and not is_senior and not node_id:
    #         open_shifts = Shift.objects.filter(staff=staff, is_open=True)

    #         if open_shifts.count() == 1:
    #             active_node_id = open_shifts.first().node_id
    #             messages.success(
    #                 request, "Автоматический возврат к открытой смене.")
    #             # Передаем и дату, и узел
    #             return HttpResponseRedirect(f"?shift__node__id={active_node_id}&date={selected_date}")

    #     # ─── ИСПРАВЛЕНИЕ №3: АВТО-ОТКРЫТИЕ СМЕНЫ ТОЛЬКО ДЛЯ СЕГОДНЯ ────────
    #     # Если пользователь смотрит историю за вчера, мы НЕ должны открывать новую смену!
    #     if selected_date == today and node_id and request.user.is_authenticated:
    #         try:
    #             if staff:
    #                 node = staff.nodes.filter(
    #                     pk=node_id, is_active=True).first()

    #                 if node:
    #                     if str(request.session.get("selected_node_id")) != str(node_id):
    #                         request.session["selected_node_id"] = node.pk
    #                         request.session.modified = True
    #                         _invalidate_shift_cache(request)

    #                     now = timezone.now()
    #                     today_start = now.replace(
    #                         hour=0, minute=0, second=0, microsecond=0)

    #                     has_open_shift = Shift.objects.filter(
    #                         staff=staff,
    #                         node=node,
    #                         is_open=True,
    #                         opened_at__gte=today_start
    #                     ).exists()

    #                     if not has_open_shift:
    #                         get_or_create_shift(staff, node)
    #                         messages.success(
    #                             request, f"Автоматически открыта новая смена для: {node.name}")

    #         except Exception as e:
    #             messages.error(
    #                 request, f"Ошибка при автоматической обработке смены: {e}")

    #     if staff and staff.is_active:
    #         available_nodes = list(
    #             staff.nodes.filter(is_active=True).select_related(
    #                 "exchange_point")
    #         )

    #         # ─── ИСПРАВЛЕНИЕ №4: ПОИСК ИСТОРИЧЕСКОЙ СМЕНЫ ДЛЯ КАССИРА ───────
    #         if not is_senior:
    #             # Ищем смену конкретно за выбранный день (неважно, открыта она или уже закрыта)
    #             shift = Shift.objects.filter(
    #                 staff=staff,
    #                 opened_at__date=selected_date
    #             ).first()

    #             # Если смены за этот день нет, но мы смотрим "Сегодня", берем текущую открытую смену
    #             if not shift and selected_date == today:
    #                 shift = get_open_shift(staff, request)

    #         if not is_senior:
    #             if shift:
    #                 balances = (
    #                     CashBalance.objects
    #                     .filter(node=shift.node)
    #                     .select_related("currency")
    #                 )
    #                 usd_total = (
    #                     balances
    #                     .filter(currency__code__icontains="usd")
    #                     .aggregate(total=Sum("balance"))
    #                     .get("total") or 0
    #                 )
    #             else:
    #                 # Окно выбора кассы показываем, только если это сегодняшний день
    #                 needs_node_selection = len(
    #                     available_nodes) > 0 and selected_date == today

    #     if is_senior:
    #         # Для старшего кассира отображаем узлы, у которых были смены в выбранный день
    #         nodes_with_shift = set(
    #             Shift.objects
    #             .filter(opened_at__date=selected_date)
    #             .values_list("node_id", flat=True)
    #         )

    #         all_nodes = list(
    #             CashNode.objects
    #             .filter(pk__in=nodes_with_shift, is_active=True)
    #             .select_related("exchange_point")
    #             .order_by("pk")
    #         )

    #         # Если в этот день никто не работал, показываем просто все активные кассы
    #         if not all_nodes:
    #             all_nodes = list(
    #                 CashNode.objects.filter(is_active=True).select_related(
    #                     "exchange_point").order_by("pk")
    #             )

    #         if node_id:
    #             try:
    #                 request.session["selected_node_id"] = int(node_id)
    #             except Exception:
    #                 request.session["selected_node_id"] = None

    #         node_id = request.session.get("selected_node_id")
    #         if node_id:
    #             try:
    #                 node_id = int(node_id)
    #                 selected_node = next(
    #                     (n for n in all_nodes if n.pk == node_id), None)
    #             except ValueError:
    #                 selected_node = None

    #         if not selected_node and all_nodes:
    #             selected_node = all_nodes[0]

    #         if not request.GET.get("shift__node__id") and selected_node:
    #             return HttpResponseRedirect(f"?shift__node__id={selected_node.pk}&date={selected_date}")

    #         balances = (
    #             CashBalance.objects
    #             .filter(node=selected_node)
    #             .select_related("currency")
    #         ) if selected_node else None

    #         usd_total = (
    #             balances
    #             .filter(currency__code__icontains="usd")
    #             .aggregate(total=Sum("balance"))
    #             .get("total") or 0
    #         ) if balances else 0

    #     # ─── ИСПРАВЛЕНИЕ №5: ФИЛЬТР УТРЕННЕГО БАЛАНСА ПО SELECTED_DATE ────
    #     morning_balances = None

    #     if is_senior and selected_node:
    #         shift_with_morning = (
    #             Shift.objects
    #             # Поменяли today на selected_date!
    #             .filter(node=selected_node, opened_at__date=selected_date)
    #             .select_related("node")
    #             .first()
    #         )
    #         if shift_with_morning and shift_with_morning.morning_balances:
    #             morning_balances = sort_balances_dict(
    #                 shift_with_morning.morning_balances)

    #     elif shift:
    #         if shift.morning_balances:
    #             morning_balances = sort_balances_dict(shift.morning_balances)

    #     if not is_senior and shift:
    #         selected_node = getattr(shift, 'node', None)

    #     # (Старый блок парсинга даты отсюда удален!)

    #     # Заполняем контекст для шаблона
    #     extra_context["selected_date"] = selected_date
    #     extra_context["today"] = today
    #     extra_context["prev_date"] = prev_date
    #     extra_context["next_date"] = next_date

    #     extra_context["current_shift"] = shift
    #     extra_context["current_balances"] = balances
    #     extra_context["morning_balances"] = morning_balances
    #     extra_context["available_nodes"] = available_nodes
    #     extra_context["needs_node_selection"] = needs_node_selection
    #     extra_context["auto_select_node"] = (
    #         needs_node_selection and len(available_nodes) == 1)
    #     extra_context["all_nodes"] = all_nodes
    #     extra_context["selected_node"] = selected_node
    #     extra_context["selected_node_id"] = selected_node.pk if selected_node else None
    #     extra_context["is_senior"] = is_senior
    #     extra_context["usd_total"] = usd_total

    #     return super().changelist_view(request, extra_context=extra_context)

    @transaction.atomic
    def reverse_order_view(self, request, order_id):
        order = get_object_or_404(
            WholesaleOrder.objects.select_for_update(),
            pk=order_id
        )

        if order.is_reversed:
            messages.error(request, "Операция уже сторнирована")
            return redirect("admin:wholesale_wholesaleorder_changelist")

        if order.created_at < timezone.now() - timedelta(hours=8):
            messages.error(request, "Прошло более 8 часов")
            return redirect("admin:wholesale_wholesaleorder_changelist")

        try:
            OrderService.reverse_order(order)
            messages.success(request, f"Сторно #{order.id} выполнено")
        except (ValueError, ValidationError) as e:
            messages.error(request, str(e))

        return redirect("admin:wholesale_wholesaleorder_changelist")

    # ── form helpers ──────────────────────────────────────────────────────────

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        order_type = request.GET.get("order_type")
        shift_id = request.GET.get("shift")
        if order_type:
            initial["order_type"] = order_type
        if shift_id:
            initial["shift"] = shift_id
        return initial

    def get_fieldsets(self, request, obj=None):
        if obj is not None:
            return ((None, {"fields": ("comment",)}),)

        if request.GET.get("order_type") in ["in", "out", "add", "collect"]:
            return (
                (None, {"fields": ("shift", "currency",
                 "order_type", "amount_currency", "comment")}),
            )
        return (
            (None, {"fields": (
                "shift", "currency", "order_type",
                "amount_currency", "rate", "amount_base",
                "is_reversed", "comment",
            )}),
        )

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if "shift" in form.base_fields:
            form.base_fields["shift"].disabled = True
        return form

    # ── display helpers ──────────────────────────────────────────────────────

    def created_time(self, obj):
        if obj.created_at:
            return localtime(obj.created_at).strftime("%H:%M:%S")
        return "-"

    created_time.short_description = "Час"
    created_time.admin_order_field = "created_at"

    def order_type_badge(self, obj):
        # Для сторно показываем отдельный красный бейдж, чтобы не путать с обычными типами
        if obj.is_reversed:
            return format_html(
                '<span class="px-2 py-1 rounded text-xs font-semibold bg-red-100 text-red-700">Сторно</span>'
            )

        colors = {
            "buy": "bg-green-100 text-green-700",
            "sell": "bg-blue-100 text-blue-700",
            "in": "bg-emerald-100 text-emerald-700",
            "out": "bg-red-100 text-red-700",
            "add": "bg-emerald-50 text-emerald-800",
            "collect": "bg-sky-50 text-sky-700",
        }
        labels = dict(obj.ORDER_TYPES)
        return format_html(
            '<span class="px-2 py-1 rounded text-xs font-semibold {}">{}</span>',
            colors.get(obj.order_type, "bg-gray-100"),
            labels.get(obj.order_type),
        )

    order_type_badge.short_description = "Тип"

    def reverse_action(self, obj):
        is_reversal = obj.comment and obj.comment.startswith("Сторно #")

        if obj.can_reverse and not is_reversal:
            url = reverse("admin:reverse_order", args=[obj.id])
            return format_html(
                '<a href="{}" onclick="return confirm(\'Отменить заказ?\')" '
                'class="px-2 py-1 rounded text-xs font-semibold bg-red-100 text-red-700 hover:text-green-700" title="Отменить заказ">✖︎</a>',
                url
            )
        if obj.is_reversed:
            return "✓"
        else:
            return ""

    reverse_action.short_description = ""

    # ── API views ─────────────────────────────────────────────────────────────

    def get_balance_view(self, request):
        staff = get_staff(request)
        shift = get_open_shift(staff, request)

        if not shift:
            return JsonResponse({"balance": 0})

        currency_id = request.GET.get("currency")
        currency_code = request.GET.get("currency_code")
        order_type = request.GET.get("order_type", "buy")

        if not currency_id:
            return JsonResponse({"balance": 0})

        # Если это кроссовый курс, получаем баланс нужной валюты
        if currency_code and '-' in currency_code:
            parts = currency_code.split('-')
            base_code = parts[0]
            quote_code = parts[1]

            try:
                if order_type == "sell":
                    # При продаже: показываем баланс quote валюты (EUR)
                    target_currency = Currency.objects.get(code=quote_code)
                    currency_id = target_currency.id
                else:
                    base_currency_id = request.GET.get("base_currency_id")

                    if base_currency_id:
                        target_currency = Currency.objects.get(
                            id=base_currency_id)
                    else:
                        target_currency = Currency.objects.filter(
                            code=base_code).first()

                currency_id = target_currency.id
            except Currency.DoesNotExist:
                return JsonResponse({"balance": 0})

        balance = (
            CashBalance.objects
            .filter(node=shift.node, currency_id=currency_id)
            .values_list("balance", flat=True)
            .first()
        )
        return JsonResponse({"balance": float(balance or 0)})

    def exchange_view(self, request):
        staff = get_staff(request)
        # Determine node context: prefer GET, then POST, then session
        node_id = (
            request.GET.get("shift__node__id")
            or request.POST.get("shift__node__id")
            or request.session.get("selected_node_id")
        )

        shift = get_open_shift(staff, request)

        if not shift:
            messages.error(request, "Смена не открыта")
            return redirect("..")

        order_type = request.GET.get("type", "buy")
        # Allow collection types but fallback to `buy` if unknown
        if order_type not in {"buy", "sell", "in", "out", "add", "collect"}:
            order_type = "buy"

        if request.method == "POST":
            try:
                currency = Currency.objects.get(
                    id=request.POST.get("currency"))
                amount = Decimal(request.POST.get("amount"))
            except (Currency.DoesNotExist, InvalidOperation, TypeError):
                messages.error(request, "Ошибка данных")
                # preserve node selection on error
                if node_id:
                    return redirect(f"{request.path}?shift__node__id={node_id}")
                return redirect(request.path)

            rate = None
            amount_base = None
            base_currency = None

            # Определяем тип курса по коду валюты
            is_cross_rate = currency.code and '-' in currency.code

            if is_cross_rate:
                # Получаем выбранную версию базовой валюты (USD, USD-new и т.д.)
                base_currency_id = request.POST.get("base_currency")
                if base_currency_id:
                    try:
                        base_currency = Currency.objects.get(
                            id=base_currency_id)
                    except Currency.DoesNotExist:
                        messages.error(request, "Версія валюти не знайдена")
                        return redirect(request.path)

                try:
                    rate = Decimal(request.POST.get("rate"))
                    amount_base = amount * \
                        rate.quantize(Decimal("1"), rounding=ROUND_HALF_UP)

                except (InvalidOperation, TypeError):
                    messages.error(
                        request, "Некорректна сума у базовій валюті")
                    return redirect(request.path)
            else:
                # Обычный курс
                if order_type in {"buy", "sell"}:
                    try:
                        rate = Decimal(request.POST.get("rate"))
                        amount_base = amount * rate
                    except (InvalidOperation, TypeError):
                        messages.error(request, "Некорректный курс")
                        return redirect(request.path)
                else:
                    # Для "in"/"out" - amount_base = amount
                    amount_base = amount
            request_id = request.POST.get("request_id")

            if cache.get(f"order:{request_id}"):
                messages.error(request, "Операция уже выполнена")
                return redirect("..")

            cache.set(
                f"order:{request_id}",
                True,
                timeout=300
            )
            try:
                # Если это крос-курс, передаем выбранную базовую валюту
                if base_currency and is_cross_rate:
                    OrderService.create_order(
                        shift=shift,
                        currency=currency,
                        order_type=order_type,
                        amount_currency=amount,
                        rate=rate,
                        amount_base=amount_base,
                        comment=request.POST.get("comment", ""),
                        base_currency=base_currency,
                    )
                else:
                    OrderService.create_order(
                        shift=shift,
                        currency=currency,
                        order_type=order_type,
                        amount_currency=amount,
                        rate=rate,
                        amount_base=amount_base,
                        comment=request.POST.get("comment", ""),
                    )

                    messages.success(request, "Операция проведена")
                    # persist selected node in session to keep UI consistent
                    try:
                        if node_id:
                            request.session["selected_node_id"] = int(node_id)
                            request.session.modified = True
                    except Exception:
                        pass
            except Exception as e:
                messages.error(request, str(e))

            # Build redirect preserving node context
            url = reverse("admin:wholesale_wholesaleorder_changelist")
            if node_id:
                url += f"?shift__node__id={node_id}"
            return redirect(url)

        # ── GET: строим queryset валют одним запросом
        currencies = Currency.objects.all()

        if order_type in {"buy", "sell", "out", "collect"}:

            if order_type in {"buy", "sell"}:
                currencies = currencies.exclude(code="uah")

            # Получаем доступные балансы на кассе
            cashbalances = (
                CashBalance.objects
                .filter(node=shift.node)
                .select_related("currency")
                .only("currency_id", "currency__code")
            )
            available_ids = {cb.currency_id for cb in cashbalances}
            available_codes = {
                cb.currency.code for cb in cashbalances if cb.currency and cb.currency.code}

            if order_type == "out":
                # Расход — только то что есть на кассе (включая UAH)
                currencies = currencies.filter(id__in=available_ids)

            # Проверяем крос-курсы
            for curr in Currency.objects.filter(code__contains='-'):
                parts = curr.code.split('-')
                if len(parts) != 2:
                    continue

                if parts[0] == "usd":
                    base_code = parts[0]
                    quote_code = parts[1]
                elif parts[1] == "usd":
                    base_code = parts[1]
                    quote_code = parts[0]
                else:
                    continue

                has_quote = quote_code in available_codes
                has_base = any(
                    code == base_code or code.startswith(base_code + '-')
                    for code in available_codes
                )

                if order_type == "buy" and has_base:
                    available_ids.add(curr.id)
                elif order_type == "sell" and has_quote:
                    available_ids.add(curr.id)

        if order_type == "sell":
            currencies = currencies.filter(id__in=available_ids)

        context = {
            "request_id": str(uuid.uuid4()),
            **self.admin_site.each_context(request),
            "currencies": currencies,
            "order_type": order_type,
            # treat add/collect as movements in the form
            "is_movement": order_type in {"in", "out", "add", "collect"},
            "current_shift": shift,
        }

        return TemplateResponse(
            request,
            "admin/wholesale/exchange_form.html",
            context,
        )

    def close_shift_view(self, request):
        """
        Закрывает смену для текущего кассира и показывает отчет.
        """
        staff = getattr(request.user, 'staffprofile', None)

        # 1. Единая и понятная проверка профиля
        if not staff or not staff.is_active:
            self.message_user(
                request,
                "Профиль сотрудника не найден или неактивен!",
                level=messages.ERROR
            )
            # Лучше возвращать на конкретную страницу, а не просто '..'
            return redirect("admin:wholesale")

        # 2. Получаем смену
        shift = get_open_shift(staff, request)

        if not shift:
            self.message_user(
                request,
                "У вас нет открытой смены.",
                level=messages.WARNING
            )
            return redirect("admin:wholesale_wholesaleorder_changelist")

        # 3. Закрываем смену
        shift.is_open = False
        shift.closed_at = timezone.now()
        shift.save(update_fields=["is_open", "closed_at"])

        _invalidate_shift_cache(request)

        # 4. Формируем отчет
        report = get_shift_report(shift)

        self.message_user(
            request,
            f"Смена успешно закрыта в {localtime(shift.closed_at).strftime('%H:%M')}",
            level=messages.SUCCESS
        )

        return TemplateResponse(
            request,
            "admin/shift_report.html",
            {
                **self.admin_site.each_context(request),
                "report": report,
            }
        )

    def shift_report_xls_view(self, request):
        """
        Экспортирует отчет о смене в .xls (Excel 97-2003)
        Ожидает GET параметр `shift_id`.
        """
        try:
            import xlwt
        except Exception:
            self.message_user(
                request,
                "xlwt не установлен. Установите: pip install xlwt",
                level=messages.ERROR,
            )
            return redirect("admin:wholesale_wholesaleorder_changelist")

        shift_id = request.GET.get("shift_id")

        if shift_id:
            # Удаляем неразрывные пробелы (%C2%A0) и обычные пробелы
            shift_id = str(shift_id).replace('\xa0', '').replace(' ', '')

            try:
                shift_id = int(shift_id)
            except ValueError:
                # Обработка случая, если передали совсем не число (например, буквы)
                shift_id = None

        if not shift_id:
            self.message_user(request, "Не указана смена",
                              level=messages.ERROR)
            return redirect("admin:wholesale_wholesaleorder_changelist")

        try:
            shift = Shift.objects.get(pk=shift_id)
        except Shift.DoesNotExist:
            self.message_user(request, "Смена не найдена",
                              level=messages.ERROR)
            return redirect("admin:wholesale_wholesaleorder_changelist")

        report = get_shift_report(shift)

        # Создаем workbook
        from io import BytesIO

        wb = xlwt.Workbook()
        ws = wb.add_sheet("Shift Report")

        row = 0

        def write(label, value=""):
            nonlocal row
            ws.write(row, 0, label)
            ws.write(row, 1, str(value))
            row += 1

        write("Кассир", report.get("cashier"))
        write("Касса", getattr(report.get("shift"),
              "node").name if report.get("shift") else "")
        # Форматируем времена в строки, чтобы избежать проблем с timezone-aware datetimes
        opened = report.get("opened_at")
        closed = report.get("closed_at")
        try:
            opened_str = opened.strftime("%d.%m.%Y %H:%M") if opened else ""
        except Exception:
            opened_str = str(opened) if opened else ""
        try:
            closed_str = closed.strftime("%d.%m.%Y %H:%M") if closed else ""
        except Exception:
            closed_str = str(closed) if closed else ""

        write("Открыта", opened_str)
        write("Закрыта", closed_str)
        row += 1

        write("Прибыль (UAH)", report.get("profit"))
        write("Куплено", report.get("totals", {}).get("buy"))
        write("Продано", report.get("totals", {}).get("sell"))
        write("Приход", report.get("totals", {}).get("in"))
        write("Расход", report.get("totals", {}).get("out"))
        row += 1

        # Объединённая таблица балансов: Утро / Конец смены
        row += 1
        ws.write(row, 0, "Балансы (Утро / Конец смены)")
        row += 1
        ws.write(row, 0, "Валюта")
        ws.write(row, 1, "Утро")
        ws.write(row, 2, "Конец смены")
        row += 1
        for b in report.get("combined_balances", []):
            ws.write(row, 0, b.get("currency_name") or b.get("currency_code"))
            ws.write(row, 1, str(b.get("morning")))
            ws.write(row, 2, str(b.get("end")))
            row += 1

        row += 1
        ws.write(row, 0, "Операции смены")
        row += 1
        headers = ["Время", "Тип", "Валюта",
                   "Сумма", "Курс", "UAH", "Комментарий"]
        for c_idx, h in enumerate(headers):
            ws.write(row, c_idx, h)
        row += 1

        for order in report.get("orders"):
            # created_at may be timezone-aware; convert to string
            created = order.get("created_at")
            try:
                created_str = created.strftime(
                    "%Y-%m-%d %H:%M:%S") if created else ""
            except Exception:
                created_str = str(created)

            ws.write(row, 0, created_str)
            ws.write(row, 1, order.get("order_type"))
            ws.write(row, 2, (order.get("currency__code") or "").upper())
            ws.write(row, 3, str(order.get("amount_currency")))
            ws.write(row, 4, str(order.get("rate")))
            ws.write(row, 5, str(order.get("amount_base")))
            ws.write(row, 6, order.get("comment") or "")
            row += 1

        output = BytesIO()
        wb.save(output)
        output.seek(0)

        # Формируем имя файла: shift_report_{date}_{node}.xls

        # ts = localtime(shift.opened_at).strftime(
        #     "%Y%m%d_%H%M") if shift.opened_at else timezone.now().strftime("%Y%m%d_%H%M")
        # node_name = (
        #     shift.node.name if shift.node and shift.node.name else "node").replace(" ", "_")
        # filename = f"shift_report_{ts}_{node_name}.xls"

        # response = HttpResponse(
        #     output.getvalue(), content_type="application/vnd.ms-excel")
        # response["Content-Disposition"] = f'attachment; filename="{filename}"'

        ts = localtime(shift.opened_at).strftime(
            "%Y%m%d_%H%M") if shift.opened_at else timezone.now().strftime("%Y%m%d_%H%M")

        node_name = (
            shift.node.name if shift.node and shift.node.name else "node").replace(" ", "_")
        filename = f"shift_report_{ts}_{node_name}.xls"

        response = HttpResponse(
            output.getvalue(), content_type="application/vnd.ms-excel")

        # Кодируем имя файла для HTTP-заголовка
        encoded_filename = quote(filename)

        # Используем правильный синтаксис filename*=utf-8''
        response["Content-Disposition"] = f"attachment; filename*=utf-8''{encoded_filename}"

        return response


# ─── CashBalance ──────────────────────────────────────────────────────────────

@admin.register(CashBalance)
class CashBalanceAdmin(ModelAdmin):
    list_display = ("node", "currency", "balance")
    list_editable = ('balance',)

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("node", "currency")

        # Superusers can see all balances.
        if request.user.is_superuser:
            return qs

        staff = get_staff(request)
        if not staff:
            return qs.none()

        open_shift = get_open_shift(staff, request)

        if open_shift:
            # Показываем только балансы той точки (node), на которой открыта смена
            return qs.filter(node_id=open_shift.node_id)
            allowed_nodes = staff.nodes.all().values_list("id", flat=True)

        allowed_nodes = staff.nodes.filter(
            is_active=True).values_list("id", flat=True)
        return qs.filter(node_id__in=allowed_nodes)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        # Ограничиваем выбор касс только теми, которые есть у сотрудника
        if db_field.name == "node" and not request.user.is_superuser:
            staff = get_staff(request)
            if staff:
                # kwargs["queryset"] = staff.nodes.filter(is_active=True)
                kwargs["queryset"] = staff.nodes.all()

        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))

        # Superusers могут редактировать всё
        if request.user.is_superuser:
            return readonly

        # Если редактируем существующий объект
        if obj:
            staff = get_staff(request)
            if not staff:
                return ["node", "currency", "balance"]

            # Получаем открытую смену кассира
            # open_shift = get_open_shift(staff, request)

            # # Если смены нет - запрещаем редактировать баланс
            # if not open_shift:
            #     readonly.append("balance")
            #     return readonly

            # # Если это другая касса - запрещаем редактировать
            # if obj.node_id != open_shift.node_id:
            #     readonly.append("balance")
            #     return readonly

            # Для USD валют - запрещаем редактировать баланс
            # if obj.currency and 'usd' in obj.currency.code.lower():
            #     readonly.append("balance")

        return readonly

    # def has_change_permission(self, request, obj=None):
    #     # Если это USD валюта - запрещаем изменение
    #     if obj and obj.currency and 'usd' in obj.currency.code.lower():
    #         return request.user.is_superuser

    #     return super().has_change_permission(request, obj)

    # def has_add_permission(self, request):
    #     # Запрещаем добавлять новые балансы через админ
    #     return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        # Запрещаем удалять балансы
        return request.user.is_superuser

    def has_module_permission(self, request):
        # Возвращаем False, чтобы скрыть модель из бокового меню для всех
        return False


# ─── CashMovement ─────────────────────────────────────────────────────────────

@admin.register(CashMovement)
class CashMovementAdmin(ModelAdmin):
    list_display = ("node", "currency", "movement_badge",
                    "amount", "comment", "created_at")

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("node", "currency")

    def movement_badge(self, obj):
        # Показываем направление движения для данной валюты.
        if obj.movement_type == "in":
            return format_html('<span style="color:#16a34a">⬇ Приход</span>')
        elif obj.movement_type == "add":
            return format_html('<span style="color:#16a34a">⬇ Подкрепление</span>')
        elif obj.movement_type == "buy":
            return format_html('<span class="px-2 py-1 rounded text-xs bg-green-100 text-green-700 dark:bg-green-500/20 dark:text-green-400">⬇ Покупка</span>')
        elif obj.movement_type == "reversal":
            return format_html('<span style="color:#dc2626">Сторно</span>')
        elif obj.movement_type == "sell":
            return format_html('<span class="px-2 py-1 rounded text-xs bg-blue-100 text-blue-700">⬆ Продажа</span>')
        elif obj.movement_type == "collect":
            return format_html('<span style="color:#0284c7">⬆ Инкассация</span>')
        return format_html('<span style="color:#dc2626">⬆ Расход</span>')
