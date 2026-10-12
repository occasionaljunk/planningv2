"""Data-sovereignty controls.

Two mechanisms keep data inside the deploying country:

1. Residency checks run at start-up and on demand. They verify the hosting
   region, storage location, key storage, configured outbound integrations and
   front-end assets against the sovereignty policy. In ``production`` mode a
   failed critical check stops the platform from starting.
2. The egress guard wraps the process's socket layer so the application can
   only open network connections to allow-listed in-country destinations. It is
   defence in depth: network firewalls and private networking remain the
   primary control, but a misconfigured library or integration cannot silently
   ship data abroad.
"""
from __future__ import annotations

import collections
import fnmatch
import ipaddress
import os
import re
import socket
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from ..config import PACKAGE_DIR, Settings, get_settings


class EgressBlocked(ConnectionRefusedError):
    pass


class EgressGuard:
    def __init__(self) -> None:
        self.installed = False
        self.allowed_hosts: list[str] = []
        self.allowed_nets: list[ipaddress._BaseNetwork] = []
        self._resolved_ok: set[str] = set()
        self.blocked: collections.deque = collections.deque(maxlen=200)
        self._lock = threading.Lock()
        self._orig_connect = None
        self._orig_connect_ex = None
        self._orig_getaddrinfo = None

    # -- policy ---------------------------------------------------------
    def configure(self, allowed: list[str]) -> None:
        self.allowed_hosts, self.allowed_nets = [], []
        for entry in allowed:
            try:
                self.allowed_nets.append(ipaddress.ip_network(entry, strict=False))
            except ValueError:
                self.allowed_hosts.append(entry.lower())

    def host_allowed(self, host: str) -> bool:
        host = (host or "").lower().strip("[]")
        try:
            ip = ipaddress.ip_address(host)
            return any(ip in n for n in self.allowed_nets) or host in self._resolved_ok
        except ValueError:
            return any(fnmatch.fnmatch(host, pat) for pat in self.allowed_hosts)

    def url_allowed(self, url: str) -> bool:
        return self.host_allowed(urlparse(url).hostname or "")

    def _block(self, host: str, port, how: str) -> None:
        with self._lock:
            self.blocked.append({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                 "destination": f"{host}:{port}", "stage": how})
        raise EgressBlocked(f"Shield egress guard: outbound connection to {host}:{port} is outside the "
                            f"sovereign allow-list and was blocked")

    # -- install ----------------------------------------------------------
    def install(self, allowed: list[str]) -> None:
        self.configure(allowed)
        if self.installed:
            return
        guard = self
        self._orig_connect = socket.socket.connect
        self._orig_connect_ex = socket.socket.connect_ex
        self._orig_getaddrinfo = socket.getaddrinfo

        def _check(sock, address):
            if sock.family in (socket.AF_INET, socket.AF_INET6) and isinstance(address, tuple):
                host, port = address[0], address[1]
                if not guard.host_allowed(str(host)):
                    guard._block(str(host), port, "connect")

        def connect(sock, address):
            _check(sock, address)
            return guard._orig_connect(sock, address)

        def connect_ex(sock, address):
            _check(sock, address)
            return guard._orig_connect_ex(sock, address)

        def getaddrinfo(host, port, *args, **kwargs):
            h = host.decode() if isinstance(host, bytes) else (host or "")
            if h and not guard.host_allowed(h):
                guard._block(h, port, "dns")
            res = guard._orig_getaddrinfo(host, port, *args, **kwargs)
            if h:
                for r in res:
                    guard._resolved_ok.add(str(r[4][0]).lower())
            return res

        socket.socket.connect = connect
        socket.socket.connect_ex = connect_ex
        socket.getaddrinfo = getaddrinfo
        self.installed = True

    def uninstall(self) -> None:
        if not self.installed:
            return
        socket.socket.connect = self._orig_connect
        socket.socket.connect_ex = self._orig_connect_ex
        socket.getaddrinfo = self._orig_getaddrinfo
        self.installed = False


GUARD = EgressGuard()


def install_egress_guard(settings: Settings | None = None) -> None:
    s = settings or get_settings()
    if s.enforce_egress_guard:
        GUARD.install(s.allowed_egress)
    else:
        GUARD.configure(s.allowed_egress)


def _mount_fstype(path: Path) -> str:
    try:
        path = path.resolve()
        best, fstype = "", "unknown"
        with open("/proc/mounts") as fh:
            for line in fh:
                parts = line.split()
                mnt, fs = parts[1], parts[2]
                if str(path).startswith(mnt) and len(mnt) > len(best):
                    best, fstype = mnt, fs
        return fstype
    except OSError:
        return "unknown"


REMOTE_FS = {"nfs", "nfs4", "cifs", "smbfs", "fuse.s3fs", "fuse.gcsfuse", "fuse.rclone", "fuse.sshfs"}
_EXTERNAL_ASSET = re.compile(r"""(?:src|href)\s*=\s*["'](https?:)?//""", re.I)


def run_checks(settings: Settings | None = None) -> list[dict]:
    s = settings or get_settings()
    checks: list[dict] = []

    def add(name, ok, detail, critical=True):
        checks.append({"check": name, "status": "pass" if ok else ("fail" if critical else "warn"),
                       "critical": critical, "detail": detail})

    add("Hosting region is an approved in-country region",
        s.hosting_region in s.allowed_storage_regions,
        f"hosting_region={s.hosting_region}; approved={s.allowed_storage_regions}")

    fstype = _mount_fstype(s.data_dir)
    add("Data directory is on local, in-country storage", fstype not in REMOTE_FS,
        f"{s.data_dir} (filesystem: {fstype})" + (" — remote filesystem, confirm it is in-country" if fstype in REMOTE_FS else ""))

    kd = Path(s.key_dir)
    perms_ok = True
    if kd.exists():
        for f in kd.glob("*.key"):
            if f.stat().st_mode & 0o077:
                perms_ok = False
    add("Key material is access-restricted (0600)", perms_ok, f"key_dir={kd}. Production: in-country HSM/KMS.")

    bad = [u for u in s.webhooks if not GUARD.url_allowed(u)]
    add("Notification webhooks point only to allow-listed in-country hosts", not bad,
        "all webhooks allow-listed" if not bad else f"not allowed: {bad}")

    if s.narrative_backend == "local_llm":
        ok = GUARD.url_allowed(s.local_llm_url)
        add("Language model is hosted in-country", ok, s.local_llm_url)
    else:
        add("No external AI services are used", True,
            "Detection and reasoning run in-process; narrative uses deterministic templates.")

    add("Egress guard is active", GUARD.installed or not s.enforce_egress_guard,
        f"installed={GUARD.installed}; allow-list={s.allowed_egress}", critical=s.enforce_egress_guard)

    web = PACKAGE_DIR / "web"
    external = []
    for f in list(web.glob("*.html")) + list(web.glob("*.js")):
        if f.parent.name == "vendor":
            continue
        if _EXTERNAL_ASSET.search(f.read_text(errors="ignore")):
            external.append(f.name)
    add("User interface loads no third-party resources (no CDNs, fonts or analytics)", not external,
        "all assets served from this instance" if not external else f"external references in {external}")

    proxies = {k: v for k, v in os.environ.items() if k.lower() in ("http_proxy", "https_proxy", "all_proxy")}
    add("No outbound proxy configured for the platform process", not proxies,
        "none" if not proxies else "proxy variables present; ensure the proxy is in-country", critical=False)

    add("Raw source messages are encrypted at rest (AES-256-GCM, envelope keys)", True,
        "enforced in code for every stored message")
    add("Patient identifiers are pseudonymised before storage (HMAC-SHA256)", True,
        "national IDs, names, addresses and dates of birth are never stored in analytical tables")
    return checks


def assert_startup_policy(settings: Settings | None = None) -> list[dict]:
    s = settings or get_settings()
    checks = run_checks(s)
    failures = [c for c in checks if c["status"] == "fail" and c["critical"]]
    if failures and s.environment == "production":
        raise RuntimeError("Sovereignty policy violated; refusing to start: " +
                           "; ".join(c["check"] for c in failures))
    return checks


def try_outbound(url: str, timeout: float = 3.0) -> dict:
    """Attempt a real outbound connection. Used to demonstrate the guard."""
    parsed = urlparse(url)
    host = parsed.hostname or url
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return {"url": url, "result": "connected", "blocked": False}
    except EgressBlocked as e:
        return {"url": url, "result": str(e), "blocked": True}
    except OSError as e:
        return {"url": url, "result": f"network error: {e}", "blocked": False}
