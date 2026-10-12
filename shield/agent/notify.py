"""Notifications: in-app inbox plus optional in-country webhooks.

Webhook targets are checked against the sovereignty allow-list before any
delivery; the payload contains only the alert summary and a link, never
record-level data.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from ..config import get_settings
from ..db import get_conn
from ..security import audit
from ..security.residency import GUARD


def notify(alert_id: int, title: str, priority: str, level: str, confidence: float, summary: str) -> list[dict]:
    con = get_conn()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    msg = f"[{priority}] {title} (confidence {confidence:.0%}, {level.lower()})"
    out = [{"channel": "in_app", "target": "all government users", "status": "delivered"}]
    for url in get_settings().webhooks:
        if not GUARD.url_allowed(url):
            out.append({"channel": "webhook", "target": url, "status": "blocked: outside sovereign allow-list"})
            continue
        try:
            import httpx
            httpx.post(url, json={"alert_id": alert_id, "title": title, "priority": priority, "confidence": confidence,
                                  "summary": summary}, timeout=5)
            out.append({"channel": "webhook", "target": url, "status": "delivered"})
        except Exception as e:
            out.append({"channel": "webhook", "target": url, "status": f"failed: {e.__class__.__name__}"})
    for o in out:
        con.execute("INSERT INTO notifications(alert_id, created_at, channel, target, status, message) VALUES (?,?,?,?,?,?)",
                    (alert_id, now, o["channel"], o["target"], o["status"], msg))
    con.commit()
    audit.record("agent", "system", "alert.notify", f"alert:{alert_id}", {"channels": out, "priority": priority})
    return out
