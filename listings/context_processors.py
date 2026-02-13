# listings/context_processors.py
from __future__ import annotations

from typing import Any, Dict

from .models import Notification


def notifications_context(request) -> Dict[str, Any]:
    """
    Navbar notifications:
    - nav_notifications: latest 8 notifications
    - nav_notifications_unread: unread count
    """
    user = getattr(request, "user", None)
    if not user or not getattr(user, "is_authenticated", False):
        return {
            "nav_notifications": [],
            "nav_notifications_unread": 0,
        }

    qs = (
        Notification.objects.filter(recipient=user)
        .select_related("actor")
        .only("id", "message", "level", "url", "is_read", "created_at", "actor_id")
        .order_by("-created_at")
    )

    return {
        "nav_notifications": list(qs[:8]),
        "nav_notifications_unread": qs.filter(is_read=False).count(),
    }
