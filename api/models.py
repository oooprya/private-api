from tastypie.resources import ModelResource, ALL, ALL_WITH_RELATIONS
from currency.models import Exchanger, CartItem, Currency, Orders
from tastypie import fields
from tastypie.authorization import Authorization
from tastypie.http import HttpBadRequest, HttpCreated
from .authentication import CustomAuthentication


class CurrencyResource(ModelResource):
    class Meta:
        queryset = Currency.objects.all().order_by('id')
        resource_name = 'currency'
        allowed_methods = ['get']
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

        def obj_create(self, bundle, **kwargs):
            try:
                bundle = super().obj_create(bundle, **kwargs)  # Создаем объект
                return self.create_response(bundle.request, {"message": "Продукт успешно создан!"}, response_class=HttpCreated)
            except Exception as e:
                return self.create_response(bundle.request, {"error": str(e)}, response_class=HttpBadRequest)


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
        bundle.data['address'] = bundle.obj.exchanger.address
        bundle.data['address_map'] = bundle.obj.exchanger.address_map
        bundle.data['working_hours'] = bundle.obj.exchanger.working_hours
        bundle.data['currency_name'] = bundle.obj.currency.name
        bundle.data['code'] = bundle.obj.currency.code
        return bundle
