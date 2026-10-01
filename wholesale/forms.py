from django import forms
from decimal import Decimal
from .models import WholesaleOrder
from currency.models import CartItem


class WholesaleOrderForm(forms.ModelForm):
    class Meta:
        model = WholesaleOrder
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()

        exchanger = cleaned.get("exchanger")
        currency = cleaned.get("currency")
        order_type = cleaned.get("order_type")
        rate = cleaned.get("rate")
        amount = cleaned.get("amount")

        # Авто курс если не введён
        if not rate and exchanger and currency:

            cart = CartItem.objects.filter(
                exchanger=exchanger,
                currency=currency
            ).first()

            if cart:
                if order_type == WholesaleOrder.BUY:
                    cleaned["rate"] = Decimal(cart.buy)
                else:
                    cleaned["rate"] = Decimal(cart.sell)

                cleaned["rate_source"] = "system"

        # Авто сумма
        if cleaned.get("rate") and amount:
            cleaned["uah_total"] = cleaned["rate"] * amount

        return cleaned


class DepositUAHForm(forms.Form):
    amount = forms.DecimalField(
        max_digits=18,
        decimal_places=2,
        label="Сумма (UAH)"
    )
