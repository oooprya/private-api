from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline
from .models import Exchanger, Currency, CartItem, Orders, Users

from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.models import User, Group

from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm
from unfold.admin import ModelAdmin


admin.site.unregister(User)
admin.site.unregister(Group)


@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm


@admin.register(Group)
class GroupAdmin(BaseGroupAdmin, ModelAdmin):
    pass


class CurrencyInline(TabularInline):
    model = CartItem
    extra = 1


class ExchangerInline(TabularInline):
    model = Exchanger
    extra = 1


class CartItemAdmin(ModelAdmin):
    list_display = ("id", "exchanger", "exchanger_id",
                    "currency", 'buy', 'sell', 'sum')
    list_editable = ('buy', 'sell', 'sum')
    list_display_links = ["exchanger"]


class CurrencyAdmin(ModelAdmin):
    list_display = ('name', 'code', 'is_visible', 'sort_order')
    list_editable = ('is_visible', 'sort_order')
    list_filter = ('is_visible',)
    search_fields = ('name', 'code')
    ordering = ('sort_order',)


class ExchangerAdmin(ModelAdmin):
    inlines = [CurrencyInline]
    list_display = ["id", "address"]
    list_display_links = ["address"]


class OrdersAdmin(ModelAdmin):
    list_display = ("id", 'currency_name', 'buy_or_sell',
                    'order_sum', 'exchange_rate', "status", "address_exchanger")
    list_display_links = ["currency_name"]
    list_editable = ['status']


class UsersAdmin(ModelAdmin):
    list_display = ("id", 'chat_id', 'chat_id_name',
                    'clients_telephone', 'role')
    list_display_links = ["chat_id_name"]
    list_editable = ['clients_telephone', 'role']


admin.site.register(CartItem, CartItemAdmin)
admin.site.register(Currency, CurrencyAdmin)
admin.site.register(Orders, OrdersAdmin)
admin.site.register(Users, UsersAdmin)
admin.site.register(Exchanger, ExchangerAdmin)
