from django.conf import settings

from .models import CashNode


def selected_node(request):
    """Context processor that exposes `selected_node_id` and `selected_node`.

    - `selected_node_id`: int or None
    - `selected_node`: `CashNode` instance or None

    Note: you must add 'wholesale.context_processors.selected_node' to
    `TEMPLATES[...]['OPTIONS']['context_processors']` in your settings.
    """
    selected_id = request.session.get("selected_node_id")
    node = None

    if selected_id:
        try:
            node = CashNode.objects.filter(
                pk=selected_id, is_active=True).select_related('exchange_point').first()
        except Exception:
            node = None

    # If user doesn't have access to the node, we do not remove it here automatically
    # — the view that sets the session must ensure permissions.
    return {
        "selected_node_id": selected_id,
        "selected_node": node,
    }
