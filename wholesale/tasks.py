from celery import shared_task
from django.utils import timezone


@shared_task
def close_shifts_at_end_of_day():
    """Закрывает все открытые смены. Запускается автоматически в 20:00 через Celery Beat."""
    from .models import Shift
    updated = (
        Shift.objects
        .filter(is_open=True)
        .update(is_open=False, closed_at=timezone.now())
    )
    return f"Закрыто смен: {updated}"
