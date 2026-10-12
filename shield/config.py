"""Configuration loading.

Settings come from a TOML file (``SHIELD_CONFIG`` or ``config/shield.toml``),
falling back to ``config/shield.example.toml`` defaults. Environment variables
``SHIELD_DATA_DIR`` and ``SHIELD_ENV`` override the most common values, which
keeps tests and containers simple.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = Path(__file__).resolve().parent


@dataclass
class Settings:
    deployment_name: str = "Riverton Public Health Authority"
    environment: str = "demo"
    country_code: str = "RV"
    jurisdiction: str = "City of Riverton"
    hosting_region: str = "rv-national-dc-1"
    allowed_storage_regions: list[str] = field(default_factory=lambda: ["rv-national-dc-1"])
    allowed_egress: list[str] = field(default_factory=lambda: ["127.0.0.0/8", "::1", "localhost"])
    enforce_egress_guard: bool = True
    data_dir: Path = ROOT / "data"
    token_ttl_minutes: int = 480
    min_cell_size: int = 5
    key_dir: Path = ROOT / "data" / "keys"
    alert_alpha: float = 0.01
    fdr_q: float = 0.05
    scan_replicates: int = 999
    notify_min_confidence: float = 0.50
    lookback_days: int = 14
    narrative_backend: str = "template"
    local_llm_url: str = ""
    local_llm_model: str = ""
    webhooks: list[str] = field(default_factory=list)
    config_path: str = ""

    @property
    def db_path(self) -> Path:
        return self.data_dir / "shield.db"

    @property
    def simulator_db_path(self) -> Path:
        return self.data_dir / "simulator.db"


def _resolve(p: str | Path, base: Path) -> Path:
    p = Path(p)
    return p if p.is_absolute() else (base / p).resolve()


def load_settings(path: str | None = None) -> Settings:
    candidates = [path, os.environ.get("SHIELD_CONFIG"), ROOT / "config" / "shield.toml",
                  ROOT / "config" / "shield.example.toml"]
    raw: dict = {}
    used = ""
    for c in candidates:
        if c and Path(c).exists():
            with open(c, "rb") as fh:
                raw = tomllib.load(fh)
            used = str(c)
            break
    dep = raw.get("deployment", {})
    sov = raw.get("sovereignty", {})
    sec = raw.get("security", {})
    agent = raw.get("agent", {})
    narr = raw.get("narrative", {})
    notif = raw.get("notifications", {})
    s = Settings()
    s.config_path = used
    s.deployment_name = dep.get("name", s.deployment_name)
    s.environment = os.environ.get("SHIELD_ENV", dep.get("environment", s.environment))
    s.country_code = sov.get("country_code", s.country_code)
    s.jurisdiction = sov.get("jurisdiction", s.jurisdiction)
    s.hosting_region = sov.get("hosting_region", s.hosting_region)
    s.allowed_storage_regions = list(sov.get("allowed_storage_regions", s.allowed_storage_regions))
    s.allowed_egress = list(sov.get("allowed_egress", s.allowed_egress))
    s.enforce_egress_guard = bool(sov.get("enforce_egress_guard", s.enforce_egress_guard))
    data_dir = os.environ.get("SHIELD_DATA_DIR") or sov.get("data_dir") or "./data"
    s.data_dir = _resolve(data_dir, ROOT)
    s.token_ttl_minutes = int(sec.get("token_ttl_minutes", s.token_ttl_minutes))
    s.min_cell_size = int(sec.get("min_cell_size", s.min_cell_size))
    key_dir = sec.get("key_dir")
    s.key_dir = _resolve(key_dir, ROOT) if key_dir and not os.environ.get("SHIELD_DATA_DIR") else s.data_dir / "keys"
    s.alert_alpha = float(agent.get("alert_alpha", s.alert_alpha))
    s.fdr_q = float(agent.get("fdr_q", s.fdr_q))
    s.scan_replicates = int(os.environ.get("SHIELD_SCAN_REPLICATES", agent.get("scan_replicates", s.scan_replicates)))
    s.notify_min_confidence = float(agent.get("notify_min_confidence", s.notify_min_confidence))
    s.lookback_days = int(agent.get("lookback_days", s.lookback_days))
    s.narrative_backend = narr.get("backend", s.narrative_backend)
    s.local_llm_url = narr.get("local_llm_url", "")
    s.local_llm_model = narr.get("local_llm_model", "")
    s.webhooks = list(notif.get("webhooks", []))
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return s


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
