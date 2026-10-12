import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="shield-test-")
os.environ["SHIELD_DATA_DIR"] = _TMP
os.environ["SHIELD_ENV"] = "demo"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from shield.config import reset_settings_cache  # noqa: E402

reset_settings_cache()


@pytest.fixture(scope="session")
def db():
    from shield.db import get_conn, init_db, migrate
    from shield.reference import load_city_reference
    from shield.security.auth import seed_demo_users
    con = get_conn()
    init_db(con)
    migrate(con)
    seed_demo_users()
    keys = load_city_reference(None)
    return {"con": con, "keys": keys}


@pytest.fixture(scope="session")
def small_world():
    """Two months of baseline activity for the simulated city (no outbreaks)."""
    from datetime import date
    from shield.synth.generator import SimConfig, generate
    return generate(SimConfig(start=date(2026, 1, 1), end=date(2026, 2, 28), scenarios=()), progress=lambda *a: None)
