from django.contrib import admin
from .models import Exchanger, Currency, CartItem, Orders, Users

class CurrencyInline(admin.TabularInline):
    model = CartItem
    extra = 1

class ExchangerInline(admin.TabularInline):
    model = Exchanger
    extra = 1

class CartItemAdmin(admin.ModelAdmin):
    list_display = ("id", "exchanger", "exchanger_id", "currency", 'buy', 'sell', 'sum')
    list_editable = ('buy', 'sell', 'sum')
    list_display_links = ["exchanger"]

class CurrencyAdmin(admin.ModelAdmin):
    list_display = ["name", "code"]

class ExchangerAdmin(admin.ModelAdmin):
    inlines = [CurrencyInline]
    list_display = ["id","address"]
    list_display_links = ["address"]

class OrdersAdmin(admin.ModelAdmin):
    list_display = ("id", 'currency_name', 'buy_or_sell', 'order_sum','exchange_rate', "status", "address_exchanger")
    list_editable = ['status']


admin.site.register(CartItem, CartItemAdmin)
admin.site.register(Currency, CurrencyAdmin)
admin.site.register(Orders, OrdersAdmin)
admin.site.register(Users)
admin.site.register(Exchanger, ExchangerAdmin)
