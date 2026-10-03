"""Incident notifications to a Slack- or Discord-compatible incoming webhook, sent only after the change commits."""
from __future__ import annotations

import logging
import threading

import httpx
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.config import get_settings

logger = logging.getLogger(__name__)
_PENDING = "nexops_notifications"
_ICONS = {
    "incident.opened": "🚨",
    "approval.required": "🛂",
    "remediation.failed": "❌",
    "recovery.failed": "⚠️",
    "incident.resolved": "✅",
    "flag.disabled": "🚩",
    "canary.failed": "🐤",
    "test": "🔔",
}


def queue(session: Session, kind: str, title: str, detail: str, path: str | None = None) -> None:
    """Queue a notification; it is delivered after the session commits and dropped on rollback."""
    settings = get_settings()
    if not settings.notify_webhook_url or kind not in settings.notify_events:
        return
    link = f"{settings.public_url.rstrip('/')}{path}" if path else None
    if not session.in_transaction():
        session.begin()  # tie the notification to a transaction so commit/rollback decide its fate
    session.info.setdefault(_PENDING, []).append({"kind": kind, "title": title, "detail": detail, "link": link})


def render(item: dict) -> dict:
    text = f"{_ICONS.get(item['kind'], '•')} *{item['title']}*\n{item['detail']}"
    if item.get("link"):
        text += f"\n{item['link']}"
    # Slack reads "text"; Discord reads "content". Each ignores the other key.
    return {"text": text, "content": text.replace("*", "**")}


def send_test(url: str) -> dict:
    """Send one message synchronously (Settings → Send test alert)."""
    item = {"kind": "test", "title": "NexOps test alert", "detail": "Notifications are configured correctly. You will be alerted when incidents open, need approval, fail recovery or resolve.", "link": f"{get_settings().public_url.rstrip('/')}/settings"}
    try:
        response = httpx.post(url, json=render(item), timeout=8.0)
        return {"ok": response.status_code < 300, "status": response.status_code, "detail": response.text[:200]}
    except httpx.HTTPError as error:
        return {"ok": False, "status": None, "detail": str(error)[:200]}


def _deliver(url: str, items: list[dict]) -> None:
    for item in items:
        try:
            httpx.post(url, json=render(item), timeout=5.0).raise_for_status()
            logger.info("Notification %s sent: %s", item["kind"], item["title"])
        except httpx.HTTPError as error:
            logger.warning("Notification %s failed: %s", item["kind"], error)


@event.listens_for(Session, "after_commit")
def _after_commit(session: Session) -> None:
    items = session.info.pop(_PENDING, None)
    url = get_settings().notify_webhook_url
    if items and url:
        threading.Thread(target=_deliver, args=(url, items), name="nexops-notify", daemon=True).start()


@event.listens_for(Session, "after_soft_rollback")
def _after_rollback(session: Session, _previous_transaction) -> None:
    # Fires for every rollback, including ones where no SQL had been sent yet.
    session.info.pop(_PENDING, None)
