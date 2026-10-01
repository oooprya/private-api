from datetime import date, timedelta
from decimal import Decimal
from django.db.models import Sum, Count
from django.utils import timezone
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.core.exceptions import ValidationError
from django.db import transaction
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required

from wholesale.services.balance_service import BalanceService

from currency.models import Currency, CartItem


from .models import (
    CashNode,
    CashBalance,
    WholesaleOrder,
    Shift,
)


@require_POST
@login_required
def switch_active_node_view(request):
    """Set an active CashNode in the session.

    Expected POST params:
    - node_id: integer

    Returns JSON: {"success": True, "selected_node_id": <id>} or error payload.
    """
    node_id = request.POST.get("node_id")
    if not node_id:
        return JsonResponse({"success": False, "error": "node_id is required"}, status=400)

    try:
        node_pk = int(node_id)
    except (TypeError, ValueError):
        return JsonResponse({"success": False, "error": "invalid node_id"}, status=400)

    staff = getattr(request.user, "staffprofile", None)
    if not staff:
        return JsonResponse({"success": False, "error": "staff profile not found"}, status=403)

    # Verify that requested node is allowed for this staff
    allowed = staff.nodes.filter(pk=node_pk).exists()
    if not allowed and not request.user.is_superuser:
        return JsonResponse({"success": False, "error": "node not permitted"}, status=403)

    node = get_object_or_404(CashNode, pk=node_pk, is_active=True)

    # Persist selection in session
    request.session["selected_node_id"] = node.pk

    return JsonResponse({"success": True, "selected_node_id": node.pk, "node_name": str(node)})


@transaction.atomic
def reverse_order(request, order_id):

    order = get_object_or_404(WholesaleOrder, id=order_id)

    # проверка 45 минут
    if timezone.now() - order.created_at > timedelta(minutes=45):
        raise ValidationError("Время сторно истекло")

    # только последний
    last_order = WholesaleOrder.objects.order_by("-created_at").first()
    if order.id != last_order.id:
        raise ValidationError("Можно сторнировать только последнюю операцию")

    reverse_type = {
        "buy": "sell",
        "sell": "buy",
        "in": "out",
        "out": "in"
    }[order.order_type]

    reverse_order = WholesaleOrder.objects.create(
        shift=order.shift,
        currency=order.currency,
        order_type=reverse_type,
        amount_currency=order.amount_currency,
        amount_base=order.amount_base,
        rate=order.rate
    )

    # Пополняем поле profit для сторно-ордера: применяем ту же логику (sell +, buy -)
    if reverse_type == 'sell':
        reverse_order.profit = reverse_order.amount_base or Decimal('0')
    elif reverse_type == 'buy':
        reverse_order.profit = -(reverse_order.amount_base or Decimal('0'))
    else:
        reverse_order.profit = Decimal('0')
    reverse_order.save(update_fields=['profit'])

    # Отмечаем оригинал как сторнированный
    order.is_reversed = True
    order.save(update_fields=['is_reversed'])

    BalanceService.apply_order(reverse_order)

    return redirect(request.META.get("HTTP_REFERER", "/admin/"))


@login_required
def dashboard_balances_api(request):
    """
    Используется только для первоначальной загрузки,
    realtime работает через WebSocket.
    """

    balances = (
        CashBalance.objects
        .values(
            "node_id",
            "currency__code"
        )
        .annotate(total=Sum("balance"))
    )

    # include global totals for initial render
    try:
        date_str = request.GET.get("date")
        selected_date = None
        if date_str:
            try:
                selected_date = date.fromisoformat(date_str)
            except Exception:
                selected_date = None

        global_totals = BalanceService.compute_global_totals(selected_date)
    except Exception:
        global_totals = {"uah": 0, "usd": 0, "profit": 0}

    return JsonResponse({"balances": list(balances), "global_totals": global_totals})


def build_unfold_table(balances, selected_date=None):

    # ─────────────────────────────
    # 📊 1. Собираем все валюты + totals
    # ─────────────────────────────
    currency_totals = {}

    for node in balances:
        for b in node["balances"]:
            code = b["currency"].lower()
            amount = b["amount"] or 0

            currency_totals[code] = currency_totals.get(code, 0) + amount

    # ─────────────────────────────
    # 📌 2. Сортировка валют по sort_order
    # ─────────────────────────────
    currencies_qs = Currency.objects.all().order_by("sort_order")

    currencies = []
    display_names = {}

    for c in currencies_qs:

        code = c.code.lower()

        # Определяем, что выводить в заголовок (short_name или код)
        name_to_show = c.short_name if c.short_name else c.code

        # ❌ убираем валюты которых нет
        if currency_totals.get(code, 0) == 0:
            continue

        currencies.append(code)
        display_names[code] = name_to_show

    # ─────────────────────────────
    # 💰 3. Вставляем USD total после UAH
    # ─────────────────────────────
    final_columns = []

    for code in currencies:
        final_columns.append(code)

        if code == "uah":
            final_columns.append("usd_total")

    # ─────────────────────────────
    # 🧾 4. HEADERS
    # ─────────────────────────────
    headers = ["Касса"]

    for col in final_columns:
        if col == "usd_total":
            headers.append("Общий $")
        else:
            headers.append(display_names.get(col, col.upper()))

    # Добавляем колонки Сделки и Прибыль после всех валют
    headers.append("Сделки")
    headers.append("Прибыль")

    # Подготовим агрегацию по ордерам для выбранной даты (если передана)
    orders_aggs = {}
    if selected_date is not None:
        qs = (
            WholesaleOrder.objects
            .filter(created_at__date=selected_date)
            # .exclude(order_type='out')
            .values('shift__node_id')
            .annotate(deals_count=Count('id'), profit_sum=Sum('profit'))
        )
        for r in qs:
            node_id = r.get('shift__node_id')
            if node_id is None:
                continue
            orders_aggs[node_id] = {
                'deals_count': r.get('deals_count') or 0,
                'profit_sum': r.get('profit_sum') or 0,
            }

        # Prepare currency sell rates to convert snapshots to UAH (like admin)
        try:
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
        except Exception:
            rates = None

    # ─────────────────────────────
    # 📄 5. ROWS
    # ─────────────────────────────
    balances = sorted(balances, key=lambda x: x["exchange_point"].id)

    rows = []

    for node in balances:

        # 👉 делаем быстрый dict валют
        currency_map = {
            b["currency"].lower(): b["amount"]
            for b in node["balances"]
        }

        first_cell = node["node_name"]

        row = [first_cell]

        for col in final_columns:

            if col == "usd_total":
                val = node.get("usd", {}).get("total", 0)
            else:
                val = currency_map.get(col, 0)

            if val:

                fl_val = f'{val:,.0f}'
                content = fl_val.replace(",", " ")
            else:
                content = "—"

            row.append(content)

        # Добавляем сделки и прибыль для узла
        node_id = node.get('node_id')
        deals_val = orders_aggs.get(node_id, {}).get('deals_count', 0)

        # Try to compute profit from morning snapshot if available for the node
        profit_val = None
        if selected_date is not None and rates is not None:
            try:
                shift = Shift.objects.filter(
                    node_id=node_id, opened_at__date=selected_date).first()
                if shift and shift.morning_balances:
                    # morning equivalent
                    morning_total = Decimal('0')
                    for code, info in (shift.morning_balances or {}).items():
                        try:
                            amount = Decimal(str(info.get('amount') or 0))
                        except Exception:
                            amount = Decimal('0')
                        morning_total += amount * \
                            rates.get(code.lower(), Decimal('0'))

                    # evening/current equivalent from DB
                    bal_qs = CashBalance.objects.filter(
                        node_id=node_id).select_related('currency')
                    evening_total = Decimal('0')
                    for b in bal_qs:
                        code = (b.currency.code or '').lower()
                        evening_total += Decimal(b.balance or 0) * \
                            rates.get(code, Decimal('0'))

                    # subtract collection movements effect so they don't affect profit
                    from wholesale.models import CashMovement
                    collection_movements = CashMovement.objects.filter(
                        node_id=node_id,
                        created_at__date=selected_date,
                        movement_type__in=["add", "collect"]
                    ).select_related('currency')

                    collection_effect = Decimal('0')
                    for cm in collection_movements:
                        code = (cm.currency.code or '').lower()
                        rate = rates.get(code, Decimal('0'))
                        # add увеличивает баланс → вычитаем из прибыли
                        # collect уменьшает баланс → не вычитаем
                        if cm.movement_type == 'add':
                            collection_effect += Decimal(cm.amount or 0) * rate
                        else:  # collect
                            collection_effect -= Decimal(cm.amount or 0) * rate

                    profit_val = evening_total - morning_total - collection_effect
            except Exception:
                profit_val = None

        if profit_val is None:
            profit_val = orders_aggs.get(node_id, {}).get('profit_sum', 0)

        row.append(str(deals_val))
        # Форматируем прибыль как целое с разделением пробелом
        row.append(f"{float(profit_val):,.0f}".replace(",", " "))

        rows.append({
            "node_id": node["node_id"],
            "cells": row,
        })

    totals_row = ["ИТОГО"]

    for col in final_columns:
        if col == "usd_total":
            total = sum(n.get("usd", {}).get("total", 0) for n in balances)
        else:
            total = sum(
                next((b["amount"]
                      for b in n["balances"] if b["currency"] == col), 0)
                for n in balances
            )

        totals_row.append(f"{total:,.0f}".replace(",", " "))

    # # Итого для новых колонок
    # deals_total = sum(orders_aggs.get(n.get('node_id'), {}).get(
    #     'deals_count', 0) for n in balances) if orders_aggs else 0
    # profit_total = sum(orders_aggs.get(n.get('node_id'), {}).get(
    #     'profit_sum', 0) for n in balances) if orders_aggs else 0

    # totals_row.append(str(deals_total))
    # totals_row.append(f"{float(profit_total):,.0f}".replace(",", " "))

    rows.append({
        "node_id": None,
        "cells": totals_row,
    })

    return {
        "headers": headers,
        "rows": rows,
        "columns": final_columns,
        "striped": True,
        "hoverable": True,
    }


def dashboard_callback(request, context):

    user = request.user
    staff = getattr(user, "staffprofile", None)

    is_senior = (
        request.user.is_superuser or
        (staff and staff.role == "senior")
    )

    selected_date = request.GET.get("date")
    if selected_date:
        try:
            selected_date = date.fromisoformat(selected_date)
        except ValueError:
            selected_date = timezone.localdate()
    else:
        selected_date = timezone.localdate()

    today = timezone.localdate()
    prev_date = selected_date - timedelta(days=1)
    next_date = selected_date + timedelta(days=1)

    dashboard = BalanceService.get_dashboard_balances(selected_date)
    context["selected_date"] = selected_date
    context["today"] = today
    context["prev_date"] = prev_date
    context["next_date"] = next_date

    if user.is_superuser or is_senior:
        table = build_unfold_table(dashboard, selected_date)
        context["dashboard_balances"] = table
        # Global aggregated totals for senior cards
        try:
            context["global_totals"] = BalanceService.compute_global_totals(
                selected_date)
        except Exception:
            context["global_totals"] = {"uah": 0, "usd": 0, "profit": 0}
        context["is_senior"] = is_senior
        return context

    if staff and staff.role == "cashier":
        # Показываем только балансы по тем кассам, к которым у кассира есть доступ.
        allowed_node_ids = set(staff.nodes.filter(
            is_active=True).values_list("id", flat=True))
        context["dashboard_balances"] = [
            node for node in dashboard if node.get("node_id") in allowed_node_ids
        ]

    return context
