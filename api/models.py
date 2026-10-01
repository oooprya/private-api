from django.conf import settings
from django.db.models import Sum
from tastypie.resources import ModelResource, ALL, ALL_WITH_RELATIONS
from currency.models import Exchanger, CartItem, Currency, Orders
from tastypie import fields
from tastypie.authorization import Authorization
from .authentication import CustomAuthentication
from django.core.cache import cache
from wholesale.models import CashBalance
import hashlib
import urllib


def get_cache_key(request, base_key):
    query_string = urllib.parse.urlencode(request.GET, doseq=True)
    hashed_query = hashlib.md5(query_string.encode()).hexdigest()
    return f"{base_key}_{hashed_query}"

def get_cached_list(resource, request, cache_key_base, timeout=30, **kwargs):
    cache_key = get_cache_key(request, cache_key_base)

    cached_data = cache.get(cache_key)
    if cached_data:
        return cached_data

    response = super(resource.__class__, resource).get_list(request, **kwargs)
    cache.set(cache_key, response, timeout)
    return response

class CurrencyResource(ModelResource):
    class Meta:
        queryset = Currency.objects.filter(is_visible=True).order_by('sort_order')
        resource_name = 'currency'
        allowed_methods = ['get', 'post']
        authentication = CustomAuthentication()
        authorization = Authorization()

        filtering = {
            'name': ALL,
        }

    def get_list(self, request, **kwargs):
        return get_cached_list(
            self,
            request,
            cache_key_base=settings.CACHE_ALL_CURRENCY,
            timeout=90,
            **kwargs
        )


class ExchangerResource(ModelResource):

    class Meta:
        queryset = Exchanger.objects.all().select_related()
        resource_name = 'exchangers'
        allowed_methods = ['get', 'patch', 'post', 'delete']
        authentication = CustomAuthentication()
        authorization = Authorization()

        filtering = {
            'slug': ['exact'],
            "address": ['icontains'],
        }


    def get_list(self, request, **kwargs):
        return get_cached_list(
            self,
            request,
            cache_key_base=settings.CACHE_ALL_EXCHANGERS,
            timeout=90,
            **kwargs
        )





class OrdersResource(ModelResource):

    class Meta:
        queryset = Orders.objects.all()
        resource_name = 'orders'
        ordering = ['status']
        filtering = {
            'status': ALL,
        }
        allowed_methods = ['patch', 'post']
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

    def get_object_list(self, request):
        """
        Возвращаем только записи, у которых валюта видима (is_visible=True),
        и сортируем по полю sort_order валюты.
        """
        base = super().get_object_list(request)
        return base.filter(currency__is_visible=True).order_by('currency__sort_order','id')


    def dehydrate(self, bundle):
        float(str(bundle.obj.buy).replace(',', '.'))
        float(str(bundle.obj.sell).replace(',', '.'))

        balance_total = (
            CashBalance.objects
            .filter(
                currency=bundle.obj.currency,
                node__exchange_point=bundle.obj.exchanger,
            )
            .aggregate(total=Sum('balance'))['total'] or 0
        )

        total_uah = (
            CashBalance.objects
            .filter(
                currency__code__iexact='uah',
                node__exchange_point=bundle.obj.exchanger,
            )
            .aggregate(total=Sum('balance'))['total'] or 0
        )

        bundle.data['address'] = bundle.obj.exchanger.address
        bundle.data['address_map'] = bundle.obj.exchanger.address_map
        bundle.data['working_hours'] = bundle.obj.exchanger.working_hours
        bundle.data['currency_name'] = bundle.obj.currency.name
        bundle.data['code'] = bundle.obj.currency.code
        bundle.data['balance'] = str(balance_total)
        bundle.data['total_uah'] = str(total_uah)
    
        return bundle

    def get_list(self, request, **kwargs):
        return get_cached_list(
            self,
            request,
            cache_key_base=settings.CACHE_ALL_CURRENCYS,
            timeout=60,
            **kwargs
        )
