"""Pseudonymisation of patient identifiers.

A national identifier is replaced by HMAC-SHA256(secret, normalised_id). The
secret lives in the KMS, so the platform can link records about the same
person across hospitals, clinics, insurers and labs without ever storing
who that person is. Re-identification requires the source institution's own
records, which stay with that institution.
"""
from __future__ import annotations

import hashlib
import hmac
import re

from .crypto import get_kms

_NORMALISE = re.compile(r"[^0-9A-Za-z]")


class Pseudonymiser:
    def __init__(self, key: bytes | None = None):
        self._key = key or get_kms().get("pseudonym")

    def pid(self, national_id: str) -> str:
        norm = _NORMALISE.sub("", str(national_id)).upper()
        return hmac.new(self._key, norm.encode(), hashlib.sha256).hexdigest()[:32]

    def encounter_key(self, pid: str, date: str, facility_id: str) -> str:
        msg = f"{pid}|{date}|{facility_id}".encode()
        return hmac.new(self._key, msg, hashlib.sha256).hexdigest()[:24]


def age_band(age: int | None) -> int | None:
    """Coarsen exact age into the surveillance age bands used platform-wide."""
    if age is None:
        return None
    edges = [5, 12, 18, 25, 45, 65, 75]
    for i, e in enumerate(edges):
        if age < e:
            return i
    return len(edges)


AGE_BANDS = ["0-4", "5-11", "12-17", "18-24", "25-44", "45-64", "65-74", "75+"]
