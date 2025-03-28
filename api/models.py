from django.conf import settings
from tastypie.resources import ModelResource, ALL, ALL_WITH_RELATIONS
from currency.models import Exchanger, CartItem, Currency, Orders
from tastypie import fields
from tastypie.authorization import Authorization
from .authentication import CustomAuthentication
from django.core.cache import cache

class CurrencyResource(ModelResource):
    class Meta:
        queryset = Currency.objects.all().order_by('id')
        resource_name = 'currency'
        allowed_methods = ['get', 'post']
        authentication = CustomAuthentication()
        authorization = Authorization()

        filtering = {
            'name': ALL,
        }


class ExchangerResource(ModelResource):

    class Meta:
        queryset = Exchanger.objects.all().select_related()
        resource_name = 'exchangers'
        allowed_methods = ['get', 'patch', 'post', 'delete']
        authentication = CustomAuthentication()
        authorization = Authorization()



class OrdersResource(ModelResource):

    class Meta:
        queryset = Orders.objects.all()
        resource_name = 'orders'
        ordering = ['status']
        filtering = {
            'status': ALL,
        }
        allowed_methods = ['get', 'patch', 'post']
        authentication = CustomAuthentication()
        authorization = Authorization()
        always_return_data = True



class CartItemResource(ModelResource):
    exchanger = fields.ToOneField(ExchangerResource, 'exchanger')
    currency = fields.ToOneField(CurrencyResource, 'currency')

    class Meta:
        queryset = CartItem.objects.all().select_related(
            'exchanger', 'currency').prefetch_related('exchanger', 'currency').order_by('id')

        resource_name = 'currencys'
        ordering = ['currency']
        filtering = {
            'exchanger': ALL,
            'currency': ALL_WITH_RELATIONS,
            'sum': ['exact', 'gt', 'lt', 'gte', 'lte'],
        }
        allowed_methods = ['get', 'patch', 'post']
        authentication = CustomAuthentication()
        authorization = Authorization()


    def dehydrate(self, bundle):
        float(str(bundle.obj.buy).replace(',', '.'))
        float(str(bundle.obj.sell).replace(',', '.'))
        bundle.data['address'] = bundle.obj.exchanger.address
        bundle.data['address_map'] = bundle.obj.exchanger.address_map
        bundle.data['working_hours'] = bundle.obj.exchanger.working_hours
        bundle.data['currency_name'] = bundle.obj.currency.name
        bundle.data['code'] = bundle.obj.currency.code
        return bundle

    # def get_list(self, request, **kwargs):
    #     currencys_cache = cache.get(settings.CACHE_ALL_CURRENCYS)

    #     if currencys_cache:
    #         new_currencys_cache = currencys_cache
    #     else:
    #         new_currencys_cache = super().get_list(request, **kwargs)
    #         cache.set(settings.CACHE_ALL_CURRENCYS, new_currencys_cache, 60)

    #     return new_currencys_cache
