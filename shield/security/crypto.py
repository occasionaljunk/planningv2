"""Key management and envelope encryption.

MVP: a local key-encryption key (KEK) stored in ``key_dir`` with 0600
permissions. Production: the ``LocalKMS`` class is replaced by an adapter for
an in-country HSM or KMS (PKCS#11, or a sovereign cloud KMS in the national
region) so key material never leaves the jurisdiction or the HSM boundary.

Each raw message is encrypted with a fresh 256-bit data-encryption key (DEK)
using AES-256-GCM. The DEK is wrapped with the KEK (AES key wrap via GCM).
"""
from __future__ import annotations

import hashlib
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..config import get_settings


class LocalKMS:
    """File-backed key store. Keys are generated on first use."""

    KEYS = ("kek", "pseudonym", "token")

    def __init__(self, key_dir: Path | None = None):
        self.key_dir = Path(key_dir or get_settings().key_dir)
        self.key_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.key_dir, 0o700)
        except OSError:
            pass

    def _path(self, name: str) -> Path:
        return self.key_dir / f"{name}.key"

    def get(self, name: str) -> bytes:
        p = self._path(name)
        if not p.exists():
            p.write_bytes(secrets.token_bytes(32))
            os.chmod(p, 0o600)
        data = p.read_bytes()
        if len(data) != 32:
            raise ValueError(f"key {name} is corrupt")
        return data

    def key_id(self, name: str) -> str:
        return f"{name}:" + hashlib.sha256(self.get(name)).hexdigest()[:12]

    def describe(self) -> list[dict]:
        out = []
        for k in self.KEYS:
            p = self._path(k)
            out.append({
                "name": k,
                "key_id": self.key_id(k),
                "algorithm": "AES-256-GCM" if k == "kek" else "HMAC-SHA256",
                "storage": "local file (MVP). Production: in-country HSM/KMS",
                "permissions": oct(p.stat().st_mode & 0o777) if p.exists() else None,
            })
        return out


_kms: LocalKMS | None = None


def get_kms() -> LocalKMS:
    global _kms
    if _kms is None or _kms.key_dir != Path(get_settings().key_dir):
        _kms = LocalKMS()
    return _kms


@dataclass
class Envelope:
    key_id: str
    wrapped_dek: bytes
    nonce: bytes
    ciphertext: bytes
    sha256: str


def encrypt(plaintext: bytes, aad: bytes = b"") -> Envelope:
    kms = get_kms()
    kek = kms.get("kek")
    dek = AESGCM.generate_key(bit_length=256)
    nonce = secrets.token_bytes(12)
    ct = AESGCM(dek).encrypt(nonce, plaintext, aad)
    wrap_nonce = secrets.token_bytes(12)
    wrapped = wrap_nonce + AESGCM(kek).encrypt(wrap_nonce, dek, b"dek-wrap")
    return Envelope(kms.key_id("kek"), wrapped, nonce, ct, hashlib.sha256(plaintext).hexdigest())


def decrypt(env: Envelope, aad: bytes = b"") -> bytes:
    kek = get_kms().get("kek")
    wrap_nonce, wrapped = env.wrapped_dek[:12], env.wrapped_dek[12:]
    dek = AESGCM(kek).decrypt(wrap_nonce, wrapped, b"dek-wrap")
    pt = AESGCM(dek).decrypt(env.nonce, env.ciphertext, aad)
    if hashlib.sha256(pt).hexdigest() != env.sha256:
        raise ValueError("integrity check failed")
    return pt
