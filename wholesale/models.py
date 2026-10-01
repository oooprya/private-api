
from django.db import models
from django.utils import timezone
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError


from currency.models import Currency, Exchanger


class CashNode(models.Model):
    """
    Универсальный денежный узел:
    - Касса
    - Машина инкассации
    """

    NODE_TYPE = [
        ("desk", "Касса"),
        ("safe", "Сейф"),
        ("car", "Машина инкассации"),
    ]

    name = models.CharField(max_length=100)

    exchange_point = models.ForeignKey(
        Exchanger,
        on_delete=models.CASCADE,
        related_name="nodes"
    )

    node_type = models.CharField(max_length=10, choices=NODE_TYPE)

    is_active = models.BooleanField(verbose_name="Активная",  default=True)

    class Meta:
        ordering = ['exchange_point']
        verbose_name = "Кассу или машину"
        verbose_name_plural = "Балансы"

    def __str__(self):
        return f"{self.name} — {self.exchange_point.address}"


# Баланс по валютам
class CashBalance(models.Model):

    """
    Баланс денег в месте хранения:
    - касса
    - машина инкассации
    """

    node = models.ForeignKey(
        CashNode,
        verbose_name="Касса",
        related_name="balances",
        on_delete=models.CASCADE
    )

    currency = models.ForeignKey(
        Currency,
        verbose_name="Валюта",
        related_name="balances",
        on_delete=models.CASCADE
    )

    balance = models.DecimalField(
        verbose_name="Остаток",
        max_digits=18,
        decimal_places=2,
        default=0
    )

    class Meta:
        ordering = ['currency']
        unique_together = ("node", "currency")
        verbose_name = "Баланс кассы"
        verbose_name_plural = "Балансы касс"

    def __str__(self):
        return f"{self.node} — {self.currency} — {self.balance}"


class CashMovement(models.Model):

    TYPE_CHOICES = (
        ('in', 'Приход'),
        ('out', 'Расход'),
        ('buy', '⬇ Покупка'),
        ('sell', '⬆ Продажа'),
        ("collect", "Инкассация"),
        ("add", "Подкрепление"),
        ("reversal", "Сторно"),
        # ('shift_close', 'Закрытие смены'),
    )

    node = models.ForeignKey(
        CashNode,
        verbose_name="Касса",
        on_delete=models.CASCADE,
        related_name="movements",
        null=True,
        blank=True
    )

    currency = models.ForeignKey(
        Currency,
        verbose_name="Валюта",
        on_delete=models.CASCADE
    )

    movement_type = models.CharField(
        max_length=20,
        choices=TYPE_CHOICES
    )

    amount = models.DecimalField(
        max_digits=18,
        decimal_places=2
    )

    comment = models.TextField(blank=True)

    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ('-created_at',)
        verbose_name = "Движение средств"
        verbose_name_plural = "История движений"


class StaffProfile(models.Model):

    ROLE_CHOICES = (
        ("cashier", "Кассир"),
        ("collector", "Инкассатор"),
        ("senior", "Старший"),
    )

    nodes = models.ManyToManyField(
        CashNode,
        verbose_name="Доступные кассы",
        blank=True
    )

    user = models.OneToOneField(User, on_delete=models.CASCADE)

    telephone = models.CharField(
        max_length=16, verbose_name="Номер телефона", blank=True)

    role = models.CharField(
        max_length=20, verbose_name="Роль", choices=ROLE_CHOICES)

    is_active = models.BooleanField(verbose_name="Активний", default=True)

    class Meta:
        verbose_name = "Сотрудник"
        verbose_name_plural = "Сотрудники"

    def __str__(self):
        return f"{self.user} ({self.role})"


class Shift(models.Model):

    staff = models.ForeignKey(
        StaffProfile,
        on_delete=models.CASCADE,
        verbose_name="Сотрудник",
        related_name="shifts"
    )

    node = models.ForeignKey(
        CashNode,
        on_delete=models.CASCADE,
        verbose_name="Касса",
        related_name="shifts",
        null=True,
        blank=True
    )

    opened_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    is_open = models.BooleanField(
        default=True, verbose_name="Открыть смену")

    # Новое поле для слепка баланса
    morning_balances = models.JSONField(
        verbose_name="Утренний баланс",
        null=True,
        blank=True
    )

    def save(self, *args, **kwargs):

        if not self.pk and self.node:
            balances = CashBalance.objects.filter(
                node=self.node
            ).select_related('currency')

            self.morning_balances = {
                b.currency.code: {
                    "name": b.currency.name,
                    # Decimal не сериализуется в JSON напрямую
                    "amount": str(b.balance),
                    "sort_order": b.currency.sort_order
                } for b in balances
            }

        # self.full_clean()
        super().save(*args, **kwargs)

    class Meta:
        verbose_name = "Смена"
        verbose_name_plural = "Смены"
        constraints = [
            models.UniqueConstraint(
                fields=["staff", "node"],
                condition=models.Q(is_open=True),
                name="unique_open_shift_per_staff_and_node"
            )
        ]


class WholesaleOrder(models.Model):

    ORDER_TYPES = (
        ("buy", "Покупка"),
        ("sell", "Продажа"),
        ('in', 'Приход'),
        ('out', 'Расход'),
        ("collect", "Инкассация"),
        ("add", "Подкрепление"),
    )

    shift = models.ForeignKey(
        Shift, null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="orders"
    )

    currency = models.ForeignKey(
        Currency, verbose_name="Валюта", on_delete=models.CASCADE)

    base_currency = models.ForeignKey(
        Currency,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="base_orders"
    )

    order_type = models.CharField(max_length=10, choices=ORDER_TYPES)

    amount_currency = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        verbose_name="Сума",
        null=True,
        blank=True
    )

    rate = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        verbose_name="Курс",
        null=True,
        blank=True
    )
    is_reversed = models.BooleanField(
        verbose_name="Сторно",
        default=False
    )
    amount_base = models.DecimalField(
        max_digits=18, null=True,
        verbose_name="Еквівалент",
        blank=True, decimal_places=2)

    profit = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        verbose_name="Прибуток",
        null=True,
        blank=True,
        default=0
    )

    comment = models.TextField(verbose_name="Комментарий", blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Обмен валют"
        verbose_name_plural = "Обмен валют"
