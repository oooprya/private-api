from django.shortcuts import redirect
from django.urls import reverse
from .models import CashierShift


class ShiftRequiredMiddleware:

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):

        if not request.user.is_authenticated:
            return self.get_response(request)

        if request.path.startswith("/admin/logout"):
            return self.get_response(request)

        cashier = getattr(request.user, "StaffProfile", None)

        if cashier:
            has_shift = CashierShift.objects.filter(
                cashier=cashier,
                is_open=True
            ).exists()

            if not has_shift and not request.path.startswith(
                reverse("admin:wholesale_cashiershift_add")
            ):
                return redirect("admin:wholesale_cashiershift_add")

        return self.get_response(request)
