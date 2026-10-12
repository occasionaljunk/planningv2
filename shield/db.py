"""Platform database (SQLite for the MVP; PostgreSQL in production).

The platform stores only pseudonymised, minimised surveillance records.
Direct identifiers (national ID, name, exact address, date of birth) never
reach these tables; the original source message is kept only as an
AES-256-GCM encrypted blob in ``raw_messages`` for audited, break-glass access.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .config import get_settings

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    institution_type TEXT NOT NULL,      -- hospital | clinic | university_clinic | laboratory | health_insurer | life_insurer | registry
    ownership TEXT NOT NULL,             -- public | private
    protocol TEXT NOT NULL,              -- FHIR R4 | HL7 v2.5.1 | Claims CSV | ...
    endpoint TEXT NOT NULL,
    neighbourhood_id INTEGER,
    connected INTEGER NOT NULL DEFAULT 1,
    api_key_hash TEXT,
    records_received INTEGER NOT NULL DEFAULT 0,
    last_received_at TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS raw_messages (
    id INTEGER PRIMARY KEY,
    source_id TEXT NOT NULL,
    received_at TEXT NOT NULL,
    format TEXT NOT NULL,
    key_id TEXT NOT NULL,
    wrapped_dek BLOB NOT NULL,
    nonce BLOB NOT NULL,
    ciphertext BLOB NOT NULL,
    sha256 TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    source_id TEXT NOT NULL,
    raw_id INTEGER,
    record_type TEXT NOT NULL,           -- encounter | claim | death
    encounter_id TEXT NOT NULL,          -- identifier in the source system
    encounter_key TEXT NOT NULL,         -- HMAC(patient, date, facility) used for de-duplication
    patient_pid TEXT NOT NULL,           -- pseudonym (HMAC-SHA256 of national ID)
    facility_id TEXT NOT NULL,
    event_date TEXT NOT NULL,
    received_at TEXT NOT NULL,
    age INTEGER,
    age_band INTEGER,
    sex TEXT,
    neighbourhood_id INTEGER,
    encounter_class TEXT,                -- emergency | outpatient | inpatient | death
    chief_complaint TEXT,
    icd10 TEXT,                          -- pipe separated
    syndrome TEXT,                       -- primary syndrome
    severe_resp INTEGER NOT NULL DEFAULT 0,
    admitted INTEGER NOT NULL DEFAULT 0,
    icu INTEGER NOT NULL DEFAULT 0,
    died INTEGER NOT NULL DEFAULT 0,
    is_primary INTEGER NOT NULL DEFAULT 1,  -- 0 when a claim duplicates an EHR encounter
    linked_event_id INTEGER
);
CREATE INDEX IF NOT EXISTS ix_events_date ON events(event_date);
CREATE INDEX IF NOT EXISTS ix_events_key ON events(encounter_key);
CREATE INDEX IF NOT EXISTS ix_events_received ON events(received_at);
CREATE INDEX IF NOT EXISTS ix_events_pid ON events(patient_pid);

CREATE TABLE IF NOT EXISTS labs (
    id INTEGER PRIMARY KEY,
    source_id TEXT NOT NULL,
    raw_id INTEGER,
    patient_pid TEXT NOT NULL,
    order_id TEXT NOT NULL,
    collected_date TEXT NOT NULL,
    received_at TEXT NOT NULL,
    panel TEXT NOT NULL,
    loinc TEXT NOT NULL,
    analyte TEXT NOT NULL,
    result TEXT NOT NULL,                -- positive | negative
    age_band INTEGER,
    neighbourhood_id INTEGER
);
CREATE INDEX IF NOT EXISTS ix_labs_date ON labs(collected_date);
CREATE INDEX IF NOT EXISTS ix_labs_pid ON labs(patient_pid);

CREATE TABLE IF NOT EXISTS enrolment (
    patient_pid TEXT NOT NULL,
    institution_id TEXT NOT NULL,
    PRIMARY KEY (patient_pid, institution_id)
);

CREATE TABLE IF NOT EXISTS population (
    neighbourhood_id INTEGER NOT NULL,
    age_band INTEGER NOT NULL,
    sex TEXT NOT NULL,
    count INTEGER NOT NULL,
    PRIMARY KEY (neighbourhood_id, age_band, sex)
);

CREATE TABLE IF NOT EXISTS geography (
    neighbourhood_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    postcode_prefix TEXT NOT NULL,
    centroid_x REAL NOT NULL,
    centroid_y REAL NOT NULL,
    polygon TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS institutions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,                  -- primary_school | secondary_school | university
    neighbourhood_id INTEGER NOT NULL,
    enrolment INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    as_of TEXT NOT NULL,
    n_series INTEGER,
    n_signals INTEGER,
    n_events INTEGER,
    trace TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    first_as_of TEXT NOT NULL,
    as_of TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',  -- new | investigating | closed
    title TEXT NOT NULL,
    syndrome_family TEXT NOT NULL,
    top_hypothesis TEXT,
    confidence REAL NOT NULL,
    level TEXT NOT NULL,
    priority TEXT NOT NULL,
    notified INTEGER NOT NULL DEFAULT 0,
    run_id INTEGER,
    report TEXT NOT NULL,
    history TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS ix_alerts_fp ON alerts(fingerprint);

CREATE TABLE IF NOT EXISTS alert_cases (
    alert_id INTEGER NOT NULL,
    event_id INTEGER NOT NULL,
    PRIMARY KEY (alert_id, event_id)
);

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY,
    alert_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    username TEXT NOT NULL,
    verdict TEXT NOT NULL,               -- confirmed | partially_correct | false_alarm | inconclusive
    confirmed_cause TEXT,
    findings_review TEXT,
    actions TEXT,
    outcomes TEXT,
    notes TEXT,
    learning TEXT
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY,
    alert_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    channel TEXT NOT NULL,
    target TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT NOT NULL,
    read_by TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS model_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    actor TEXT NOT NULL,
    role TEXT NOT NULL,
    action TEXT NOT NULL,
    resource TEXT NOT NULL,
    details TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_local = threading.local()
_lock = threading.RLock()


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    path = str(path or get_settings().db_path)
    con = sqlite3.connect(path, timeout=60, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


def get_conn() -> sqlite3.Connection:
    """Thread-local connection to the platform database."""
    path = str(get_settings().db_path)
    con = getattr(_local, "con", None)
    if con is None or getattr(_local, "path", None) != path:
        con = connect(path)
        _local.con = con
        _local.path = path
    return con


def close_thread_conn() -> None:
    con = getattr(_local, "con", None)
    if con is not None:
        con.close()
        _local.con = None


def init_db(con: sqlite3.Connection | None = None) -> None:
    con = con or get_conn()
    con.executescript(SCHEMA)
    con.commit()


@contextmanager
def write_lock() -> Iterator[None]:
    with _lock:
        yield


def kv_get(key: str, default: Any = None, con: sqlite3.Connection | None = None) -> Any:
    con = con or get_conn()
    row = con.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def kv_set(key: str, value: Any, con: sqlite3.Connection | None = None) -> None:
    con = con or get_conn()
    con.execute("INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)))
    con.commit()


MIGRATIONS = [
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_events_src_enc ON events(source_id, encounter_id)",
    "CREATE INDEX IF NOT EXISTS ix_labs_received ON labs(received_at)",
]


def migrate(con: sqlite3.Connection | None = None) -> None:
    con = con or get_conn()
    for m in MIGRATIONS:
        con.execute(m)
    con.commit()
