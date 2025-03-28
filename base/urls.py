from django.contrib import admin
from django.urls import path, include
from api.models import ExchangerResource, CartItemResource, CurrencyResource, OrdersResource
from tastypie.api import Api
from .views import sitemap


app_name = 'Home'
api = Api(api_name='v1')

orders_resource = OrdersResource()
currency_resource = CurrencyResource()
cartitem_resource = CartItemResource()
exchanger_resource = ExchangerResource()

api.register(orders_resource)
api.register(currency_resource)
api.register(cartitem_resource)
api.register(exchanger_resource)

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include(api.urls)),
    path("sitemap.xml", sitemap, name="sitemap"),
]

