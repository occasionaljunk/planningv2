"""Authentication and role-based access control.

MVP: username/password (scrypt hashes) and HMAC-signed bearer tokens.
Production: replace ``login`` with the government's identity provider via
OpenID Connect plus hardware-backed MFA, keeping the same role model.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from ..config import get_settings
from ..db import get_conn
from .crypto import get_kms

ROLES = {
    "decision_maker": "Senior government official. Sees findings and aggregate data with small-cell suppression; submits decisions and feedback.",
    "epidemiologist": "Public-health analyst. Sees full findings and the pseudonymised line list; runs the agent; submits feedback.",
    "data_steward": "Manages institution connections; may decrypt a raw source message with a written justification (break-glass).",
    "auditor": "Independent oversight. Reads the audit log and security posture; no access to health records.",
}

PERMISSIONS = {
    "view_dashboard": {"decision_maker", "epidemiologist", "data_steward"},
    "view_line_list": {"epidemiologist", "data_steward"},
    "view_raw_message": {"data_steward"},
    "submit_feedback": {"decision_maker", "epidemiologist"},
    "update_alert_status": {"decision_maker", "epidemiologist"},
    "manage_sources": {"data_steward"},
    "run_agent": {"epidemiologist", "data_steward"},
    "demo_control": {"epidemiologist", "data_steward", "decision_maker"},
    "view_audit": {"auditor", "data_steward"},
    "view_security": {"auditor", "data_steward", "decision_maker", "epidemiologist"},
    "view_model": {"epidemiologist", "data_steward", "decision_maker", "auditor"},
    "export": {"epidemiologist"},
}

DEMO_USERS = [
    ("minister", "Hon. A. Rivera, Health Minister", "decision_maker"),
    ("epi", "Dr. S. Okafor, Chief Epidemiologist", "epidemiologist"),
    ("steward", "J. Lindqvist, Data Steward", "data_steward"),
    ("auditor", "M. Haddad, Independent Auditor", "auditor"),
]
DEMO_PASSWORD = "shield-demo"


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_b64, dk_b64 = stored.split("$")
        salt = base64.b64decode(salt_b64)
        dk = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except Exception:
        return False


def create_user(username: str, display_name: str, role: str, password: str) -> None:
    if role not in ROLES:
        raise ValueError(f"unknown role {role}")
    con = get_conn()
    con.execute("INSERT OR REPLACE INTO users(username, display_name, role, password_hash) VALUES (?,?,?,?)",
                (username, display_name, role, hash_password(password)))
    con.commit()


def seed_demo_users() -> None:
    for u, name, role in DEMO_USERS:
        create_user(u, name, role, DEMO_PASSWORD)


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue_token(username: str, role: str) -> str:
    body = {"sub": username, "role": role, "iat": int(time.time()),
            "exp": int(time.time()) + 60 * get_settings().token_ttl_minutes, "jti": secrets.token_hex(8)}
    payload = _b64(json.dumps(body, separators=(",", ":")).encode())
    sig = hmac.new(get_kms().get("token"), payload.encode(), hashlib.sha256).digest()
    return payload + "." + _b64(sig)


def verify_token(token: str) -> dict | None:
    try:
        payload, sig = token.split(".")
        expected = hmac.new(get_kms().get("token"), payload.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64(sig)):
            return None
        body = json.loads(_unb64(payload))
        if body["exp"] < time.time():
            return None
        return body
    except Exception:
        return None


def login(username: str, password: str) -> dict | None:
    row = get_conn().execute("SELECT * FROM users WHERE username=? AND active=1", (username,)).fetchone()
    if not row or not verify_password(password, row["password_hash"]):
        return None
    return {"token": issue_token(row["username"], row["role"]), "username": row["username"],
            "display_name": row["display_name"], "role": row["role"]}


def can(role: str, permission: str) -> bool:
    return role in PERMISSIONS.get(permission, set())
