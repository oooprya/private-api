from tastypie.resources import ModelResource, ALL, ALL_WITH_RELATIONS
from currency.models import Exchanger, CartItem, Currency, Orders
from tastypie import fields
from tastypie.authorization import Authorization
from .authentication import CustomAuthentication


class CurrencyResource(ModelResource):
    class Meta:
        queryset = Currency.objects.all()
        resource_name = 'currency'
        allowed_methods = ['get']
        authentication = CustomAuthentication()
        authorization = Authorization()

        filtering = {
            'name': ALL,
        }


class ExchangerResource(ModelResource):

    class Meta:
        queryset = Exchanger.objects.all()
        resource_name = 'exchangers'
        allowed_methods = ['get', 'patch', 'post', 'delete']
        authentication = CustomAuthentication()
        authorization = Authorization()

    # def get_array(self, bundle):
    #     data_curr = CartItem.objects.all().select_related('exchanger', 'currency').prefetch_related('exchanger', 'currency').order_by('id')
    #     for curr in data_curr:
    #         bundle.data.currency = {str(curr.currency):{"buy": curr.buy, "sell": curr.sell, "sum": curr.sum} }
    #     return bundle



    # def hydrate(self, bundle):
    #     bundle.obj.id = bundle.data['id']
    #     bundle.obj.address = bundle.data['address']
    #     return bundle

    # def get_data_curr(self, bundle):
    #     data_curr = CartItem.objects.all().prefetch_related('exchanger', 'currency')
    #     bundle.data = {str(curr.currency):{"buy": curr.buy, "sell": curr.sell, "sum": curr.sum} for curr in data_curr if bundle.obj.id == curr.exchanger.id}
    #     return bundle



    # def dehydrate(self, bundle):
    #     data_curr = CartItem.objects.all().select_related('exchanger', 'currency').prefetch_related('exchanger', 'currency').order_by('id')
    #     bundle.data['currency'] =  {str(curr.currency):{"buy": curr.buy, "sell": curr.sell, "sum": curr.sum} for curr in data_curr if bundle.obj.id == curr.exchanger.id}
    #     return bundle


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


class CartItemResource(ModelResource):
    exchanger = ExchangerResource()
    currency = fields.ForeignKey(CurrencyResource, 'currency')

    class Meta:
        queryset = CartItem.objects.all().select_related(
            'exchanger', 'currency').prefetch_related('exchanger', 'currency').order_by('id')
        # queryset = CartItem.objects.all()
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

    def hydrate(self, bundle):
        bundle.obj.currency_id = bundle.data['currency_id']
        return bundle

    def dehydrate(self, bundle):
        bundle.data['currency_id'] = bundle.obj.currency
        return bundle
