from decimal import Decimal
from datetime import date
from currency.models import Currency, CartItem
from django.utils import timezone
from django.utils.timezone import localtime
from asgiref.sync import async_to_sync
from django.db.models import Sum, Q, Count
from channels.layers import get_channel_layer
from django.core.exceptions import ValidationError
from django.core.cache import cache
from wholesale.models import CashBalance, CashMovement, Shift
from django.db.models.functions import TruncMinute
from wholesale.models import WholesaleOrder

CACHE_KEY_DASHBOARD_BALANCES = "wholesale_dashboard_balances"
CACHE_TIMEOUT_DASHBOARD_BALANCES = 5  # seconds


def get_node_balances(node_id, staff=None, opened_at=None):

    balances = (
        CashBalance.objects
        .filter(node_id=node_id)
        .select_related("currency")
    )

    result = {}
    usd_total = Decimal("0")
    opened_at_str = None
    cashier = None
    phone = None

    if staff:
        user = staff.user
        cashier = user.get_full_name() or user.username
        phone = staff.telephone

        if opened_at:
            opened_at_str = localtime(opened_at).strftime("%H:%M")

    for b in balances:
        result[b.currency.code] = float(b.balance)
        if b.currency.code and b.currency.code.lower().startswith("usd"):
            usd_total += b.balance

    # ✅ Добавляем данные по сделкам и прибыли для данного узла
    today = timezone.localdate()
    node_stats = compute_node_stats(node_id, today)

    return {
        "node_id": node_id,
        "cashier": cashier,
        "phone": phone,
        "opened_at": opened_at_str,
        "balances": result,
        "usd_total": float(usd_total),
        "deals_count": node_stats.get('deals_count', 0),
        "profit": node_stats.get('profit', 0),
    }


def compute_node_stats(node_id, selected_date=None):
    """
    Вычисляет статистику (сделки, прибыль) для конкретного узла на указанную дату.
    ⚠️ Исключает операции типа 'out' из подсчёта прибыли.
    """
    if selected_date is None:
        selected_date = timezone.localdate()

    # Считаем только операции buy/sell — коллекции не учитываем в сделках/прибыли
    orders_qs = (
        WholesaleOrder.objects
        .filter(
            shift__node_id=node_id,
            created_at__date=selected_date,
            order_type__in=['buy', 'sell']
        )
    )

    aggs = orders_qs.aggregate(
        deals_count=Count('id'),
        profit_sum=Sum('profit')
    )

    deals_count = aggs.get('deals_count') or 0
    profit_sum = aggs.get('profit_sum') or Decimal('0')

    # Попытка рассчитать прибыль через снимки (как в build_unfold_table)
    try:
        shift = Shift.objects.filter(
            node_id=node_id,
            opened_at__date=selected_date
        ).first()

        if shift and shift.morning_balances:
            # Получаем курсы
            rates_qs = CartItem.objects.filter(
                exchanger_id=1).values('currency__code', 'sell')
            rates = {}
            from decimal import InvalidOperation
            for it in rates_qs:
                code = (it.get('currency__code') or '').lower()
                try:
                    rates[code] = Decimal(it.get('sell') or 0)
                except (InvalidOperation, TypeError):
                    rates[code] = Decimal('0')

            rates['uah'] = Decimal('1.0')
            rates['usdold'] = rates.get('usd', Decimal('0.0'))

            # Утренний баланс в UAH эквиваленте
            morning_total = Decimal('0')
            for code, info in (shift.morning_balances or {}).items():
                try:
                    amount = Decimal(str(info.get('amount') or 0))
                except Exception:
                    amount = Decimal('0')
                morning_total += amount * rates.get(code.lower(), Decimal('0'))

            # Текущий баланс в UAH эквиваленте
            bal_qs = CashBalance.objects.filter(
                node_id=node_id).select_related('currency')
            evening_total = Decimal('0')
            for b in bal_qs:
                code = (b.currency.code or '').lower()
                evening_total += Decimal(b.balance or 0) * \
                    rates.get(code, Decimal('0'))

            profit_sum = evening_total - morning_total

            # Вычитаем эффект коллекций (add/collect), чтобы
            # подкрепление/инкассация не влияли на прибыль
            collection_movements = CashMovement.objects.filter(
                node_id=node_id,
                created_at__date=selected_date,
                movement_type__in=["add", "collect"]
            ).select_related('currency')

            collection_effect = Decimal('0')
            for cm in collection_movements:
                code = (cm.currency.code or '').lower()
                rate = rates.get(code, Decimal('0'))
                # add increases balance -> positive effect
                # collect decreases balance -> negative effect
                sign = Decimal(
                    '1') if cm.movement_type == 'add' else Decimal('-1')
                collection_effect += Decimal(cm.amount or 0) * rate * sign

            profit_sum -= collection_effect
    except Exception:
        # Fallback на сумму из ордеров
        pass

    return {
        'deals_count': int(deals_count),
        'profit': float(profit_sum),
    }


def broadcast_node(node_id):

    channel_layer = get_channel_layer()

    async_to_sync(channel_layer.group_send)(
        f"dashboard_node_{node_id}",
        {
            "type": "balance_update",
            "data": get_node_balances(node_id)
        }
    )


def broadcast_balance(data):

    channel_layer = get_channel_layer()

    # Augment node-level payload with global totals when possible
    try:
        if not data.get("global_totals"):
            data["global_totals"] = BalanceService.compute_global_totals()
    except Exception:
        # don't let totals computation break broadcasting
        pass

    async_to_sync(channel_layer.group_send)(
        "dashboard_global",
        {
            "type": "balance_update",
            "data": data
        }
    )


class BalanceService:

    @staticmethod
    def compute_global_totals(selected_date=None):
        """Return aggregated totals: uah, usd, profit as floats.

        If selected_date is provided, profit is summed for that date.
        """

        today = timezone.localdate()

        # UAH total
        uah_total = (
            CashBalance.objects
            .filter(currency__code__iexact="uah")
            .aggregate(total=Sum('balance'))
        ).get('total') or 0

        # USD total (any code starting with usd)
        usd_total = (
            CashBalance.objects
            .filter(currency__code__istartswith="usd")
            .aggregate(total=Sum('balance'))
        ).get('total') or 0

        # Profit, counts and sums for orders on selected_date
        if selected_date is None:
            selected_date = today

        # consider only buy/sell orders for global profit/deals counters
        orders_qs = WholesaleOrder.objects.filter(
            created_at__date=selected_date,
            order_type__in=['buy', 'sell']
        )

        aggs = orders_qs.aggregate(
            profit_total=Sum('profit'),
            deals_count=Count('id'),
            total_bought=Sum('amount_base', filter=Q(order_type='buy')),
            total_sold=Sum('amount_base', filter=Q(order_type='sell')),
        )

        # by default use orders aggregation
        profit_total = aggs.get('profit_total') or Decimal('0')
        deals_count = aggs.get('deals_count') or 0
        total_bought = aggs.get('total_bought') or Decimal('0')
        total_sold = aggs.get('total_sold') or Decimal('0')

        # Try to compute profit as difference between current UAH-equivalent
        # balances and morning snapshot (Shift.morning_balances), matching
        # admin.get_shift_report behaviour. If no snapshots exist for the
        # selected date, fallback to aggregated orders profit.
        try:
            # build rates dict (sell rates)
            rates_qs = CartItem.objects.filter(
                exchanger_id=1).values('currency__code', 'sell')
            rates = {}
            from decimal import InvalidOperation
            for it in rates_qs:
                code = (it.get('currency__code') or '').lower()
                try:
                    rates[code] = Decimal(it.get('sell') or 0)
                except (InvalidOperation, TypeError):
                    rates[code] = Decimal('0')

            rates['uah'] = Decimal('1.0')
            # ensure usdold maps to usd
            rates['usdold'] = rates.get('usd', Decimal('0.0'))

            # collect shifts for the date
            shifts = (
                Shift.objects
                .filter(opened_at__date=selected_date)
                .select_related('node')
            )

            profit_from_snapshots = Decimal('0')
            any_snapshot = False

            for shift in shifts:
                if not shift.morning_balances or not shift.node:
                    continue
                any_snapshot = True

                # morning equivalent
                morning_total = Decimal('0')
                for code, info in (shift.morning_balances or {}).items():
                    try:
                        amount = Decimal(str(info.get('amount') or 0))
                    except (InvalidOperation, TypeError):
                        amount = Decimal('0')
                    rate = rates.get(code.lower(), Decimal('0'))
                    morning_total += amount * rate

                # evening/current equivalent from DB
                bal_qs = CashBalance.objects.filter(
                    node=shift.node).select_related('currency')
                evening_total = Decimal('0')
                for b in bal_qs:
                    code = (b.currency.code or '').lower()
                    rate = rates.get(code, Decimal('0'))
                    evening_total += Decimal(b.balance or 0) * rate

                # subtract collection movements effect so they don't affect profit
                collection_movements = CashMovement.objects.filter(
                    node=shift.node,
                    created_at__date=selected_date,
                    movement_type__in=["add", "collect"]
                ).select_related('currency')

                collection_effect = Decimal('0')
                for cm in collection_movements:
                    code = (cm.currency.code or '').lower()
                    rate = rates.get(code, Decimal('0'))
                    sign = Decimal(
                        '1') if cm.movement_type == 'add' else Decimal('-1')
                    collection_effect += Decimal(cm.amount or 0) * rate * sign

                profit_from_snapshots += (evening_total -
                                          morning_total - collection_effect)

            if any_snapshot:
                profit_total = profit_from_snapshots

        except Exception:
            # keep fallback profit_total from orders
            pass

        return {
            'uah': float(uah_total),
            'usd': float(usd_total),
            'profit': float(profit_total),
            'deals_count': int(deals_count),
            'total_bought': float(total_bought),
            'total_sold': float(total_sold),
        }

    @staticmethod
    def get_dashboard_balances(selected_date=None):

        today = timezone.localdate()

        if selected_date is None or selected_date == today:
            cached = cache.get(CACHE_KEY_DASHBOARD_BALANCES)
            if cached is not None:
                return cached

            balances_qs = (
                CashBalance.objects
                .select_related("currency", "node", "node__exchange_point")
                .filter(balance__isnull=False)
                .exclude(balance=0)
            )

            usd_map = {
                row["node_id"]: {
                    "total": float(row["total"] or 0),
                    "new": float(row["new"] or 0),
                    "old": float(row["old"] or 0),
                }
                for row in (
                    balances_qs
                    .filter(currency__code__startswith="usd")
                    .values("node_id")
                    .annotate(
                        total=Sum("balance"),
                        new=Sum("balance", filter=Q(currency__code="usdnew")),
                        old=Sum("balance", filter=Q(currency__code="usdold")),
                    )
                )
            }

            nodes_data = {}

            for b in balances_qs:
                node = b.node
                if not node:
                    continue

                node_id = node.id

                if node_id not in nodes_data:
                    nodes_data[node_id] = {
                        "node_id": node_id,
                        "node_name": node.name,
                        "exchange_point": node.exchange_point,
                        "balances": [],
                        "usd": usd_map.get(node_id, {"total": 0, "new": 0, "old": 0}),
                        "cashier": None,
                        "phone": None,
                        "opened_at": None,
                    }

                nodes_data[node_id]["balances"].append({
                    "currency": b.currency.code,
                    "currency_name": b.currency.name,
                    "amount": float(b.balance),
                })

            # ─────────────────────────────────────────────
            # 👤 Смены (1 запрос)
            # ─────────────────────────────────────────────
            shifts = (
                Shift.objects
                .filter(is_open=True)
                .select_related("node", "staff__user")
            )

            for shift in shifts:
                node = shift.node
                if not node:
                    continue

                node_data = nodes_data.get(node.id)
                if not node_data:
                    continue

                user = shift.staff.user

                node_data.update({
                    "cashier": user.get_full_name() or user.username,
                    "phone": shift.staff.telephone,
                    "opened_at": (
                        localtime(shift.opened_at).strftime("%H:%M")
                        if shift.opened_at else None
                    )
                })

            result = list(nodes_data.values())

            cache.set(
                CACHE_KEY_DASHBOARD_BALANCES,
                result,
                CACHE_TIMEOUT_DASHBOARD_BALANCES
            )

            return result

        return BalanceService._get_dashboard_balances_for_date(selected_date)

    @staticmethod
    def _get_dashboard_balances_for_date(selected_date):

        if isinstance(selected_date, str):
            selected_date = date.fromisoformat(selected_date)

        shifts = (
            Shift.objects
            .filter(opened_at__date=selected_date)
            .select_related("node", "staff__user")
        )

        nodes_data = {}

        for shift in shifts:
            if not shift.node or not shift.morning_balances:
                continue

            usd_total = 0
            new_balance = 0
            old_balance = 0
            balances = []

            for code, info in shift.morning_balances.items():
                amount = float(info.get("amount") or 0)
                balances.append({
                    "currency": code,
                    "currency_name": info.get("name") or code.upper(),
                    "amount": amount,
                })

                if code.lower().startswith("usd"):
                    usd_total += amount
                    if code.lower() == "usdnew":
                        new_balance += amount
                    elif code.lower() == "usdold":
                        old_balance += amount

            nodes_data[shift.node.id] = {
                "node_id": shift.node.id,
                "node_name": shift.node.name,
                "exchange_point": shift.node.exchange_point,
                "balances": balances,
                "usd": {
                    "total": usd_total,
                    "new": new_balance,
                    "old": old_balance,
                },
                "cashier": shift.staff.user.get_full_name() or shift.staff.user.username,
                "phone": shift.staff.telephone,
                "opened_at": (
                    localtime(shift.opened_at).strftime("%H:%M")
                    if shift.opened_at else None
                ),
            }

        return list(nodes_data.values())

    @staticmethod
    def apply_order(order, base_currency=None):
        """
        Обычные курсы:
            buy  – касса получает валюту, отдаёт UAH
            sell – касса отдаёт валюту, получает UAH
            in   – приход в кассу
            out  – расход из кассы

        Кроссовые курсы (USD-EUR):
            buy  – касса получает первую валюту (EUR), отдаёт вторую (USD/USD-new)
            sell – касса отдаёт первую валюту (EUR), получает вторую (USD/USD-new)
        """

        node = order.shift.node
        currency = order.currency
        amount = order.amount_currency
        amount_base = order.amount_base or Decimal("0")

        # Комментарий для записей в истории движений
        movement_comment = order.comment or (
            f"{order.currency} {order.base_currency} {order.rate} {order.amount_base}" if order.base_currency else ""
        )

        is_cross_rate = '-' in (currency.code or '')

        if is_cross_rate:
            parts = currency.code.split('-')

            if parts[0] == "usd":
                base_code = parts[0]
                quote_code = parts[1]
            elif parts[1] == "usd":
                base_code = parts[1]
                quote_code = parts[0]

            try:
                # Используем передаваемую версию базовой валюты (USD, USD-new и т.д.)
                if not base_currency:
                    base_currency = Currency.objects.get(code=base_code)
                quote_currency = Currency.objects.get(code=quote_code)
            except Currency.DoesNotExist:
                raise ValidationError(
                    f"Валюта {base_code} или {quote_code} не найдена")

            # Получаем балансы обеих валют
            base_balance, _ = CashBalance.objects.select_for_update().get_or_create(
                node=node,
                currency=base_currency,
                defaults={"balance": Decimal("0")}
            )

            quote_balance, _ = CashBalance.objects.select_for_update().get_or_create(
                node=node,
                currency=quote_currency,
                defaults={"balance": Decimal("0")}
            )

            if order.order_type == "buy":
                # Касса КУПИЛА quote (EUR): +amount, ОТДАЛА base (USD): -amount_base
                if base_balance.balance < amount_base:
                    raise ValidationError(
                        f"Недостаточно {base_currency.code} в кассе")

                quote_balance.balance += amount
                base_balance.balance -= amount_base

            elif order.order_type == "sell":
                # Касса ПРОДАЛА quote (EUR): -amount, ПОЛУЧИЛА base (USD): +amount_base
                if quote_balance.balance < amount:
                    raise ValidationError(f"Недостаточно {quote_code} в кассе")

                quote_balance.balance -= amount
                base_balance.balance += amount_base

            quote_balance.save()
            base_balance.save()

            # Комментарий к движению (используем существующий комментарий заказа, если есть)
            movement_comment = order.comment or (
                f" {order.currency} → {order.amount_base} {order.rate}" if order.base_currency else ""
            )

            # Логирование обеих операций
            CashMovement.objects.create(
                node=node,
                currency=quote_currency,
                movement_type=order.order_type,
                amount=amount,
                comment=movement_comment,
            )

            # Для кросс-курсов вторая валюта отражает противоположное движение:
            # - при покупке (buy) мы забираем базовую валюту → отображаем как sell
            # - при продаже (sell) мы выдаём базовую валюту → отображаем как buy
            base_movement_type = (
                "sell" if order.order_type == "buy" else
                "buy" if order.order_type == "sell" else
                order.order_type
            )

            CashMovement.objects.create(
                node=node,
                currency=base_currency,
                movement_type=base_movement_type,
                amount=amount_base,
                comment=movement_comment,
            )

        else:
            # ==========================================
            # ОБЫЧНЫЙ КУРС (с UAH)
            # ==========================================
            uah = Currency.objects.get(code="uah")

            # Блокируем строки
            currency_balance, _ = CashBalance.objects.select_for_update().get_or_create(
                node=node,
                currency=currency,
                defaults={"balance": Decimal("0")}
            )

            # Для buy/sell нужен UAH
            if order.order_type in ["buy", "sell"]:
                uah_balance, _ = CashBalance.objects.select_for_update().get_or_create(
                    node=node,
                    currency=uah,
                    defaults={"balance": Decimal("0")}
                )

            # =========================
            # BUY
            # =========================
            if order.order_type == "buy":

                if uah_balance.balance < amount_base:
                    raise ValidationError("Недостаточно гривны в кассе")

                currency_balance.balance += amount
                uah_balance.balance -= amount_base

                uah_balance.save()

            # =========================
            # SELL
            # =========================
            elif order.order_type == "sell":

                if currency_balance.balance < amount:
                    raise ValidationError("Недостаточно валюты в кассе")

                currency_balance.balance -= amount
                uah_balance.balance += amount_base

                uah_balance.save()

            # IN → приход
            elif order.order_type == "in":

                currency_balance.balance += amount

            # =========================
            # OUT
            # =========================
            elif order.order_type == "out":

                if currency_balance.balance < amount:
                    raise ValidationError("Недостаточно средств в кассе")

                currency_balance.balance -= amount

            # =========================
            # COLLECTIONS: подкрепление / инкассация
            # =========================
            elif order.order_type == "add":
                # Подкрепление — увеличиваем баланс
                currency_balance.balance += amount

            elif order.order_type == "collect":
                # Инкассация — уменьшаем баланс
                currency_balance.balance -= amount

            currency_balance.save()

            # Лог движения
            CashMovement.objects.create(
                node=node,
                currency=currency,
                movement_type=order.order_type,
                amount=amount,
                comment=movement_comment,
            )

        # Сбрасываем кэш дашборда при изменении баланса
        cache.delete(CACHE_KEY_DASHBOARD_BALANCES)

    @staticmethod
    def reverse_order(order):
        """
        Откатывает баланс по ордеру без создания нового WholesaleOrder.
        Создаёт одну или две записи CashMovement с типом 'reversal'.
        """
        node = order.shift.node
        currency = order.currency
        amount = order.amount_currency
        amount_base = order.amount_base or Decimal("0")
        comment = f"Сторно #{order.id} {currency} | {amount} → {amount_base}"

        is_cross_rate = '-' in (currency.code or '')

        if is_cross_rate:
            parts = currency.code.split('-')
            base_code = parts[0] if parts[0] == "usd" else parts[1]
            quote_code = parts[1] if parts[0] == "usd" else parts[0]

            base_currency = order.base_currency or Currency.objects.get(
                code=base_code)
            quote_currency = Currency.objects.get(code=quote_code)

            base_balance = CashBalance.objects.select_for_update().get(
                node=node, currency=base_currency
            )
            quote_balance = CashBalance.objects.select_for_update().get(
                node=node, currency=quote_currency
            )

            if order.order_type == "buy":
                # Оригинал: +quote, -base → откат: -quote, +base
                # if quote_balance.balance < amount:
                #     raise ValidationError(
                #         f"Недостаточно {quote_code} для сторно")
                quote_balance.balance -= amount
                base_balance.balance += amount_base
            elif order.order_type == "sell":
                # Оригинал: -quote, +base → откат: +quote, -base
                # if base_balance.balance < amount_base:
                #     raise ValidationError(
                #         f"Недостаточно {base_currency.code} для сторно")
                quote_balance.balance += amount
                base_balance.balance -= amount_base

            quote_balance.save()
            base_balance.save()

            CashMovement.objects.create(
                node=node, currency=quote_currency,
                movement_type="reversal", amount=amount, comment=comment
            )
            CashMovement.objects.create(
                node=node, currency=base_currency,
                movement_type="reversal", amount=amount_base, comment=comment
            )

        else:
            uah = Currency.objects.get(code="uah")

            currency_balance = CashBalance.objects.select_for_update().get(
                node=node, currency=currency
            )

            if order.order_type in ["buy", "sell"]:
                uah_balance = CashBalance.objects.select_for_update().get(
                    node=node, currency=uah
                )

            if order.order_type == "buy":
                # Оригинал: +валюта, -UAH → откат: -валюта, +UAH
                # if currency_balance.balance < amount:
                #     raise ValidationError("Недостаточно валюты для сторно")
                currency_balance.balance -= amount
                uah_balance.balance += amount_base
                uah_balance.save()

            elif order.order_type == "sell":
                # Оригинал: -валюта, +UAH → откат: +валюта, -UAH
                # if uah_balance.balance < amount_base:
                #     raise ValidationError("Недостаточно UAH для сторно")
                currency_balance.balance += amount
                uah_balance.balance -= amount_base
                uah_balance.save()

            elif order.order_type == "in":
                # if currency_balance.balance < amount:
                #     raise ValidationError("Недостаточно средств для сторно")
                currency_balance.balance -= amount

            elif order.order_type == "out":
                currency_balance.balance += amount

            elif order.order_type == "add":
                # Оригинал: подкрепление +amount -> откат: -amount
                currency_balance.balance -= amount

            elif order.order_type == "collect":
                # Оригинал: инкассация -amount -> откат: +amount
                currency_balance.balance += amount

            currency_balance.save()

            CashMovement.objects.create(
                node=node, currency=currency,
                movement_type="reversal", amount=amount, comment=comment
            )

        # Сбрасываем кэш дашборда при сторно
        cache.delete(CACHE_KEY_DASHBOARD_BALANCES)

    @staticmethod
    def get_shift_chart(shift, currency_code="uah"):

        if not shift or not shift.node:
            return []

        node = shift.node

        # фильтруем движения
        movements = (
            CashMovement.objects
            .filter(
                node=node,
                currency__code=currency_code,
                created_at__gte=shift.opened_at
            )
            .annotate(time=TruncMinute("created_at"))
            .values("time")
            .annotate(total=Sum("amount"))
            .order_by("time")
        )

        # накопление
        result = []
        total = 0

        for m in movements:
            total += float(m["total"] or 0)

            result.append({
                "time": m["time"].strftime("%H:%M"),
                "value": total
            })

        return result


def compute_node_stats(node_id, selected_date=None):
    """
    Вычисляет статистику (сделки, прибыль) для конкретного узла на указанную дату.
    ⚠️ Исключает операции типа 'out' из подсчёта прибыли.
    """
    if selected_date is None:
        selected_date = timezone.localdate()

    # Считаем только операции buy/sell — коллекции не учитываем в сделках/прибыли
    orders_qs = (
        WholesaleOrder.objects
        .filter(
            shift__node_id=node_id,
            created_at__date=selected_date,
            order_type__in=['buy', 'sell']
        )
    )

    aggs = orders_qs.aggregate(
        deals_count=Count('id'),
        profit_sum=Sum('profit')
    )

    deals_count = aggs.get('deals_count') or 0
    profit_sum = aggs.get('profit_sum') or Decimal('0')

    # Попытка рассчитать прибыль через снимки (как в build_unfold_table)
    try:
        shift = Shift.objects.filter(
            node_id=node_id,
            opened_at__date=selected_date
        ).first()

        if shift and shift.morning_balances:
            # Получаем курсы
            rates_qs = CartItem.objects.filter(
                exchanger_id=1).values('currency__code', 'sell')
            rates = {}
            from decimal import InvalidOperation
            for it in rates_qs:
                code = (it.get('currency__code') or '').lower()
                try:
                    rates[code] = Decimal(it.get('sell') or 0)
                except (InvalidOperation, TypeError):
                    rates[code] = Decimal('0')

            rates['uah'] = Decimal('1.0')
            rates['usdold'] = rates.get('usd', Decimal('0.0'))

            # Утренний баланс в UAH эквиваленте
            morning_total = Decimal('0')
            for code, info in (shift.morning_balances or {}).items():
                try:
                    amount = Decimal(str(info.get('amount') or 0))
                except Exception:
                    amount = Decimal('0')
                morning_total += amount * rates.get(code.lower(), Decimal('0'))

            # Текущий баланс в UAH эквиваленте
            bal_qs = CashBalance.objects.filter(
                node_id=node_id).select_related('currency')
            evening_total = Decimal('0')
            for b in bal_qs:
                code = (b.currency.code or '').lower()
                evening_total += Decimal(b.balance or 0) * \
                    rates.get(code, Decimal('0'))

            profit_sum = evening_total - morning_total

    except Exception:
        # Fallback на сумму из ордеров
        pass

    return {
        'deals_count': int(deals_count),
        'profit': float(profit_sum),
    }
