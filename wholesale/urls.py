from django.urls import path
from .views import dashboard_balances_api

urlpatterns = [
    path("dashboard/api/balances/", dashboard_balances_api,
         name="dashboard_balances_api")
]
