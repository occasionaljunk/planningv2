"""Shield AI HTTP API and web application."""
from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
from datetime import date, datetime, timedelta, timezone

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..config import PACKAGE_DIR, get_settings
from ..db import get_conn, init_db, kv_get, migrate
from ..security import audit
from ..security.auth import PERMISSIONS, ROLES, can, login, verify_token
from ..security.crypto import Envelope, decrypt, get_kms
from ..security.disclosure import suppress
from ..security.residency import GUARD, assert_startup_policy, install_egress_guard, run_checks, try_outbound

from contextlib import asynccontextmanager

WEB = PACKAGE_DIR / "web"


def _startup() -> None:
    s = get_settings()
    con = get_conn()
    init_db(con)
    migrate(con)
    install_egress_guard(s)
    assert_startup_policy(s)
    if not con.execute("SELECT 1 FROM users LIMIT 1").fetchone() and s.environment != "production":
        from ..security.auth import seed_demo_users
        seed_demo_users()


@asynccontextmanager
async def lifespan(_app):
    _startup()
    yield


app = FastAPI(title="Shield AI", version=__version__, docs_url=None, redoc_url=None,
              openapi_url="/api/openapi.json", lifespan=lifespan)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["Content-Security-Policy"] = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                                               "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
                                               "base-uri 'none'; form-action 'self'")
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
    if request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


# --------------------------------------------------------------------------- auth helpers
class User(dict):
    @property
    def name(self) -> str:
        return self["sub"]

    @property
    def role(self) -> str:
        return self["role"]


def current_user(authorization: str = Header(default="")) -> User:
    token = authorization.removeprefix("Bearer ").strip()
    body = verify_token(token) if token else None
    if not body:
        raise HTTPException(401, "authentication required")
    return User(body)


def require(permission: str):
    def dep(user: User = Depends(current_user)) -> User:
        if not can(user.role, permission):
            audit.record(user.name, user.role, "access.denied", permission, {})
            raise HTTPException(403, f"your role ({user.role}) does not allow: {permission}")
        return user
    return dep


def _clock() -> str:
    return kv_get("clock", None) or date.today().isoformat()


# --------------------------------------------------------------------------- auth
@app.post("/api/auth/login")
def api_login(body: dict = Body(...)):
    res = login(body.get("username", ""), body.get("password", ""))
    if not res:
        audit.record(body.get("username", "?"), "unknown", "auth.failed", "login", {})
        raise HTTPException(401, "invalid credentials")
    audit.record(res["username"], res["role"], "auth.login", "login", {})
    return res


@app.get("/api/me")
def api_me(user: User = Depends(current_user)):
    s = get_settings()
    row = get_conn().execute("SELECT display_name FROM users WHERE username=?", (user.name,)).fetchone()
    return {"username": user.name, "role": user.role, "role_description": ROLES[user.role],
            "display_name": row["display_name"] if row else user.name,
            "permissions": sorted(p for p, roles in PERMISSIONS.items() if user.role in roles),
            "deployment": {"name": s.deployment_name, "environment": s.environment, "jurisdiction": s.jurisdiction,
                           "country_code": s.country_code, "hosting_region": s.hosting_region, "version": __version__},
            "clock": _clock()}


# --------------------------------------------------------------------------- overview
SYNS = ["ILI", "RESP", "GI", "FEVER", "RASH", "NEURO"]


@app.get("/api/overview")
def api_overview(user: User = Depends(require("view_dashboard"))):
    con = get_conn()
    clock = _clock()
    end = date.fromisoformat(clock)
    start = (end - timedelta(days=119)).isoformat()
    rows = con.execute("SELECT event_date, syndrome, COUNT(*) n FROM events WHERE is_primary=1 AND record_type!='death' "
                       "AND event_date BETWEEN ? AND ? GROUP BY 1, 2", (start, clock)).fetchall()
    dates = [(end - timedelta(days=119 - i)).isoformat() for i in range(120)]
    idx = {d: i for i, d in enumerate(dates)}
    series = {s: [0] * 120 for s in SYNS + ["OTHER"]}
    for r in rows:
        if r["syndrome"] in series and r["event_date"] in idx:
            series[r["syndrome"]][idx[r["event_date"]]] = r["n"]
    total = con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    day_ago = f"{(end - timedelta(days=1)).isoformat()}T23:59:59"
    last24 = con.execute("SELECT COUNT(*) FROM events WHERE received_at > ?", (day_ago,)).fetchone()[0]
    labs24 = con.execute("SELECT COUNT(*) FROM labs WHERE received_at > ?", (day_ago,)).fetchone()[0]
    srcs = _source_status(con, clock)
    alerts = con.execute("SELECT id, title, syndrome_family, priority, confidence, level, status, first_as_of, as_of, "
                         "top_hypothesis, notified FROM alerts WHERE status != 'closed' ORDER BY confidence DESC").fetchall()
    pop = con.execute("SELECT SUM(count) FROM population").fetchone()[0] or 0
    # map: last 7 days vs the previous four weeks, per neighbourhood and syndrome
    d7 = (end - timedelta(days=6)).isoformat()
    d35 = (end - timedelta(days=34)).isoformat()
    d8 = (end - timedelta(days=7)).isoformat()
    agg = con.execute("SELECT neighbourhood_id nb, syndrome, SUM(event_date >= ?) recent, SUM(event_date BETWEEN ? AND ?) prior "
                      "FROM events WHERE is_primary=1 AND record_type!='death' AND event_date BETWEEN ? AND ? "
                      "AND neighbourhood_id IS NOT NULL GROUP BY 1, 2", (d7, d35, d8, d35, clock)).fetchall()
    geo = con.execute("SELECT * FROM geography ORDER BY neighbourhood_id").fetchall()
    popnb = {r[0]: r[1] for r in con.execute("SELECT neighbourhood_id, SUM(count) FROM population GROUP BY 1")}
    nbmap = {g["neighbourhood_id"]: {"id": g["neighbourhood_id"], "name": g["name"], "polygon": json.loads(g["polygon"]),
                                     "population": popnb.get(g["neighbourhood_id"], 0), "syndromes": {}} for g in geo}
    for r in agg:
        if r["nb"] in nbmap:
            prior_week = (r["prior"] or 0) / 4
            nbmap[r["nb"]]["syndromes"][r["syndrome"]] = {
                "last7": suppress(r["recent"] or 0, user.role), "prior_weekly_avg": round(prior_week, 1),
                "ratio": round(((r["recent"] or 0) + 1) / (prior_week + 1), 2)}
    for nb in nbmap.values():
        ari_recent = sum((nb["syndromes"].get(s, {}).get("ratio", 1) for s in ("ILI", "RESP"))) / 2
        nb["syndromes"]["ARI"] = {"ratio": round(ari_recent, 2)}
    level_counts = {}
    for a in alerts:
        if a["notified"]:
            level_counts[a["level"]] = level_counts.get(a["level"], 0) + 1
    audit.record(user.name, user.role, "view.overview", "dashboard", {})
    return {
        "clock": clock, "jurisdiction": get_settings().jurisdiction,
        "kpis": {"records_total": total, "records_24h": last24, "labs_24h": labs24, "population_covered": pop,
                 "sources_online": sum(1 for s in srcs if s["status"] == "online"), "sources_total": len(srcs),
                 "alerts_by_level": level_counts,
                 "active_alerts": sum(1 for a in alerts if a["notified"]),
                 "watch_list": sum(1 for a in alerts if not a["notified"])},
        "trend": {"dates": dates, "series": series},
        "alerts": [dict(a) for a in alerts],
        "map": list(nbmap.values()),
        "notify_min_confidence": get_settings().notify_min_confidence,
    }


# --------------------------------------------------------------------------- alerts
def _suppress_report(rep: dict, role: str) -> dict:
    if role in ("epidemiologist", "data_steward"):
        return rep
    ch = rep.get("characterisation", {})
    for g in ch.get("geography", []):
        g["cases"] = suppress(g["cases"], role)
    for i in ch.get("institutions", []):
        i["cases"] = suppress(i["cases"], role)
    for a in ch.get("demographics", {}).get("age", []):
        a["cases"] = suppress(a["cases"], role)
    sev = ch.get("severity", {})
    for k in ("admitted", "icu", "deaths"):
        if k in sev:
            sev[k] = suppress(sev[k], role)
    rep["disclosure_control"] = f"Counts between 1 and {get_settings().min_cell_size - 1} are shown as '<{get_settings().min_cell_size}' for your role."
    return rep


@app.get("/api/alerts")
def api_alerts(status: str | None = None, user: User = Depends(require("view_dashboard"))):
    con = get_conn()
    q = "SELECT id, title, syndrome_family, priority, confidence, level, status, first_as_of, as_of, top_hypothesis, " \
        "notified, history FROM alerts"
    args: tuple = ()
    if status:
        q += " WHERE status=?"
        args = (status,)
    rows = [dict(r) | {"history": json.loads(r["history"])} for r in con.execute(q + " ORDER BY as_of DESC, confidence DESC", args)]
    fb = {r[0]: r[1] for r in con.execute("SELECT alert_id, verdict FROM feedback ORDER BY id")}
    for r in rows:
        r["feedback_verdict"] = fb.get(r["id"])
        r["watch_only"] = not r["notified"]
    return rows


@app.get("/api/alerts/{alert_id}")
def api_alert(alert_id: int, user: User = Depends(require("view_dashboard"))):
    con = get_conn()
    r = con.execute("SELECT * FROM alerts WHERE id=?", (alert_id,)).fetchone()
    if not r:
        raise HTTPException(404, "alert not found")
    rep = _suppress_report(json.loads(r["report"]), user.role)
    feedback = [dict(f) | {k: json.loads(f[k] or "null") for k in ("findings_review", "actions", "outcomes", "learning")}
                for f in con.execute("SELECT * FROM feedback WHERE alert_id=? ORDER BY id", (alert_id,))]
    run = con.execute("SELECT id, as_of, trace FROM agent_runs WHERE id=?", (r["run_id"],)).fetchone() if r["run_id"] else None
    audit.record(user.name, user.role, "view.alert", f"alert:{alert_id}", {})
    return {"id": r["id"], "status": r["status"], "first_as_of": r["first_as_of"], "as_of": r["as_of"],
            "notified": bool(r["notified"]), "history": json.loads(r["history"]), "report": rep, "feedback": feedback,
            "agent_trace": json.loads(run["trace"]) if run else [], "run_id": r["run_id"],
            "can_view_line_list": can(user.role, "view_line_list"), "can_feedback": can(user.role, "submit_feedback")}


LINE_COLS = ["id", "event_date", "age_band", "sex", "neighbourhood", "institution", "facility_id", "source_id",
             "encounter_class", "syndrome", "chief_complaint", "icd10", "admitted", "icu", "died", "patient", "raw_id"]


def _line_list(alert_id: int, limit: int, offset: int) -> tuple[list[dict], int]:
    from ..security.pseudonym import AGE_BANDS
    con = get_conn()
    total = con.execute("SELECT COUNT(*) FROM alert_cases WHERE alert_id=?", (alert_id,)).fetchone()[0]
    rows = con.execute(
        "SELECT e.*, g.name AS nbname, (SELECT institution_id FROM enrolment en WHERE en.patient_pid=e.patient_pid LIMIT 1) inst "
        "FROM alert_cases ac JOIN events e ON e.id = ac.event_id LEFT JOIN geography g ON g.neighbourhood_id = e.neighbourhood_id "
        "WHERE ac.alert_id=? ORDER BY e.event_date DESC, e.id DESC LIMIT ? OFFSET ?", (alert_id, limit, offset)).fetchall()
    out = []
    for r in rows:
        out.append({"id": r["id"], "event_date": r["event_date"],
                    "age_band": AGE_BANDS[r["age_band"]] if r["age_band"] is not None else None, "sex": r["sex"],
                    "neighbourhood": r["nbname"], "institution": r["inst"], "facility_id": r["facility_id"],
                    "source_id": r["source_id"], "encounter_class": r["encounter_class"], "syndrome": r["syndrome"],
                    "chief_complaint": r["chief_complaint"], "icd10": r["icd10"], "admitted": r["admitted"],
                    "icu": r["icu"], "died": r["died"], "patient": r["patient_pid"][:12], "raw_id": r["raw_id"]})
    return out, total


@app.get("/api/alerts/{alert_id}/cases")
def api_alert_cases(alert_id: int, limit: int = Query(100, le=1000), offset: int = 0,
                    user: User = Depends(require("view_line_list"))):
    rows, total = _line_list(alert_id, limit, offset)
    audit.record(user.name, user.role, "view.line_list", f"alert:{alert_id}", {"rows": len(rows), "offset": offset})
    return {"total": total, "rows": rows, "columns": LINE_COLS,
            "note": "Pseudonymised records. 'patient' is a keyed hash; no names, IDs, addresses or dates of birth are held."}


@app.get("/api/alerts/{alert_id}/cases.csv")
def api_alert_cases_csv(alert_id: int, user: User = Depends(require("export"))):
    rows, total = _line_list(alert_id, 100000, 0)
    buf = io.StringIO()
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    buf.write(f"# Shield AI export | alert {alert_id} | exported by {user.name} ({user.role}) at {ts}\n")
    buf.write(f"# OFFICIAL-SENSITIVE: pseudonymised health data. Must remain within {get_settings().jurisdiction} "
              f"({get_settings().country_code}). This export is logged in the audit trail.\n")
    w = csv.DictWriter(buf, fieldnames=LINE_COLS)
    w.writeheader()
    w.writerows(rows)
    digest = hashlib.sha256(buf.getvalue().encode()).hexdigest()
    audit.record(user.name, user.role, "export.line_list", f"alert:{alert_id}", {"rows": total, "sha256": digest})
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="shield-alert-{alert_id}-cases.csv"',
                             "X-Export-SHA256": digest})


@app.post("/api/alerts/{alert_id}/status")
def api_alert_status(alert_id: int, body: dict = Body(...), user: User = Depends(require("update_alert_status"))):
    st = body.get("status")
    if st not in ("new", "investigating", "closed"):
        raise HTTPException(400, "status must be new, investigating or closed")
    con = get_conn()
    con.execute("UPDATE alerts SET status=? WHERE id=?", (st, alert_id))
    con.commit()
    audit.record(user.name, user.role, "alert.status", f"alert:{alert_id}", {"status": st})
    return {"ok": True}


@app.post("/api/alerts/{alert_id}/feedback")
def api_feedback(alert_id: int, body: dict = Body(...), user: User = Depends(require("submit_feedback"))):
    from ..agent.feedback import submit_feedback
    try:
        return submit_feedback(alert_id, user.name, user.role, body)
    except KeyError:
        raise HTTPException(404, "alert not found")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/raw/{raw_id}")
def api_raw(raw_id: int, justification: str = "", user: User = Depends(require("view_raw_message"))):
    if len(justification.strip()) < 15:
        raise HTTPException(400, "a written justification of at least 15 characters is required (break-glass access)")
    con = get_conn()
    r = con.execute("SELECT * FROM raw_messages WHERE id=?", (raw_id,)).fetchone()
    if not r:
        raise HTTPException(404, "raw message not found (historical bulk loads keep only batch manifests)")
    pt = decrypt(Envelope(r["key_id"], r["wrapped_dek"], r["nonce"], r["ciphertext"], r["sha256"]), aad=r["source_id"].encode())
    audit.record(user.name, user.role, "breakglass.raw_message", f"raw:{raw_id}", {"justification": justification.strip(),
                                                                                    "source": r["source_id"]})
    return {"id": raw_id, "source_id": r["source_id"], "received_at": r["received_at"], "format": r["format"],
            "key_id": r["key_id"], "sha256": r["sha256"], "payload": pt.decode(errors="replace"),
            "warning": "Contains direct identifiers. Access has been recorded in the tamper-evident audit log."}


# --------------------------------------------------------------------------- sources
def _source_status(con, clock: str) -> list[dict]:
    end = date.fromisoformat(clock)
    d1 = f"{(end - timedelta(days=1)).isoformat()}T23:59:59"
    d7 = f"{(end - timedelta(days=7)).isoformat()}T23:59:59"
    counts = {r[0]: (r[1], r[2]) for r in con.execute(
        "SELECT source_id, SUM(received_at > ?), SUM(received_at > ?) FROM events WHERE received_at > ? GROUP BY 1", (d1, d7, d7))}
    lab = con.execute("SELECT SUM(received_at > ?), SUM(received_at > ?) FROM labs WHERE received_at > ?", (d1, d7, d7)).fetchone()
    counts["SRC-LAB"] = (lab[0] or 0, lab[1] or 0)
    delays = {r[0]: r[1] for r in con.execute(
        "SELECT source_id, AVG((julianday(received_at) - julianday(event_date)) * 24) FROM events "
        "WHERE event_date >= ? GROUP BY 1", ((end - timedelta(days=60)).isoformat(),))}
    out = []
    for s in con.execute("SELECT * FROM sources ORDER BY institution_type, name"):
        c24, c7 = counts.get(s["id"], (0, 0))
        last = s["last_received_at"]
        batch = s["institution_type"] in ("health_insurer", "life_insurer", "registry")
        stale_days = 3 if batch else 2
        online = bool(last) and last >= f"{(end - timedelta(days=stale_days)).isoformat()}"
        if s["institution_type"] == "registry":
            online = bool(last)
        status = "disconnected" if not s["connected"] else ("online" if online else "delayed")
        out.append({"id": s["id"], "name": s["name"], "institution_type": s["institution_type"], "ownership": s["ownership"],
                    "protocol": s["protocol"], "endpoint": s["endpoint"], "connected": bool(s["connected"]),
                    "records_received": s["records_received"], "last_received_at": last, "records_24h": int(c24 or 0),
                    "records_7d": int(c7 or 0), "avg_delay_hours": round(delays[s["id"]], 1) if delays.get(s["id"]) else None,
                    "status": status, "notes": s["notes"]})
    return out


@app.get("/api/sources")
def api_sources(user: User = Depends(require("view_dashboard"))):
    return {"clock": _clock(), "sources": _source_status(get_conn(), _clock())}


@app.post("/api/sources/{source_id}/connection")
def api_source_connection(source_id: str, body: dict = Body(...), user: User = Depends(require("manage_sources"))):
    con = get_conn()
    con.execute("UPDATE sources SET connected=? WHERE id=?", (1 if body.get("connected") else 0, source_id))
    con.commit()
    audit.record(user.name, user.role, "source.connection", source_id, {"connected": bool(body.get("connected"))})
    return {"ok": True}


# --------------------------------------------------------------------------- ingestion (institution systems)
def _source_auth(source_id: str, key: str) -> None:
    row = get_conn().execute("SELECT api_key_hash, connected FROM sources WHERE id=?", (source_id,)).fetchone()
    if not row or not row["api_key_hash"] or not hmac.compare_digest(row["api_key_hash"], hashlib.sha256(key.encode()).hexdigest()):
        audit.record(source_id or "?", "source", "ingest.denied", source_id or "?", {})
        raise HTTPException(401, "invalid source credentials")
    if not row["connected"]:
        raise HTTPException(403, "source is disconnected by the data steward")


def _ingest(fmt: str, payload: str, source_id: str, key: str):
    from ..ingest.pipeline import IngestPipeline
    _source_auth(source_id, key)
    received = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        res = IngestPipeline().ingest(source_id, fmt, payload, received)
    except (ValueError, KeyError) as e:
        audit.record(source_id, "source", "ingest.rejected", source_id, {"error": str(e)[:200]})
        raise HTTPException(422, f"message rejected: {e}")
    audit.record(source_id, "source", "ingest.accepted", source_id, {"format": fmt, **res})
    return res


@app.post("/api/ingest/fhir")
async def ingest_fhir(request: Request, x_source_id: str = Header(...), x_source_key: str = Header(...)):
    return _ingest("fhir", (await request.body()).decode(), x_source_id, x_source_key)


@app.post("/api/ingest/hl7v2")
async def ingest_hl7(request: Request, x_source_id: str = Header(...), x_source_key: str = Header(...)):
    return _ingest("hl7v2", (await request.body()).decode(), x_source_id, x_source_key)


@app.post("/api/ingest/claims")
async def ingest_claims(request: Request, x_source_id: str = Header(...), x_source_key: str = Header(...)):
    return _ingest("claims_csv", (await request.body()).decode(), x_source_id, x_source_key)


@app.post("/api/ingest/deaths")
async def ingest_deaths(request: Request, x_source_id: str = Header(...), x_source_key: str = Header(...)):
    return _ingest("death_csv", (await request.body()).decode(), x_source_id, x_source_key)


@app.post("/api/ingest/registry")
async def ingest_registry(request: Request, x_source_id: str = Header(...), x_source_key: str = Header(...)):
    return _ingest("registry_csv", (await request.body()).decode(), x_source_id, x_source_key)


# --------------------------------------------------------------------------- agent
@app.post("/api/agent/run")
def api_run(user: User = Depends(require("run_agent"))):
    from ..agent.orchestrator import run_agent
    res = run_agent(date.fromisoformat(_clock()), actor=user.name)
    return res


@app.get("/api/agent/runs")
def api_runs(limit: int = 30, user: User = Depends(require("view_dashboard"))):
    rows = get_conn().execute("SELECT id, started_at, finished_at, as_of, n_series, n_signals, n_events FROM agent_runs "
                              "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/agent/runs/{run_id}")
def api_run_detail(run_id: int, user: User = Depends(require("view_dashboard"))):
    r = get_conn().execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
    if not r:
        raise HTTPException(404, "run not found")
    return dict(r) | {"trace": json.loads(r["trace"])}


@app.get("/api/series")
def api_series(syndrome: str = "ARI", neighbourhood: int | None = None, days: int = 365,
               user: User = Depends(require("view_dashboard"))):
    con = get_conn()
    end = date.fromisoformat(_clock())
    start = (end - timedelta(days=days - 1)).isoformat()
    syns = ("ILI", "RESP") if syndrome == "ARI" else (syndrome,)
    q = (f"SELECT event_date, COUNT(*) FROM events WHERE is_primary=1 AND record_type!='death' AND syndrome IN "
         f"({','.join('?' * len(syns))}) AND event_date BETWEEN ? AND ?")
    args = list(syns) + [start, end.isoformat()]
    if neighbourhood is not None:
        q += " AND neighbourhood_id=?"
        args.append(neighbourhood)
    rows = dict(con.execute(q + " GROUP BY 1", args).fetchall())
    dates = [(end - timedelta(days=days - 1 - i)).isoformat() for i in range(days)]
    return {"dates": dates, "counts": [rows.get(d, 0) for d in dates]}


# --------------------------------------------------------------------------- model and learning
@app.get("/api/model")
def api_model(user: User = Depends(require("view_model"))):
    from ..agent import confidence
    st = confidence._state
    con = get_conn()
    fb = [dict(r) | {"learning": json.loads(r["learning"] or "{}"), "actions": json.loads(r["actions"] or "[]")}
          for r in con.execute("SELECT f.*, a.title FROM feedback f JOIN alerts a ON a.id=f.alert_id ORDER BY f.id DESC LIMIT 30")]
    rel = st("detector_reliability", {})
    from ..config import get_settings as gs
    ev_path = gs().data_dir / "evaluation.json"
    return {"features": [{"name": n, "description": d} for n, d in confidence.FEATURES],
            "prior_weights": confidence.PRIOR, "current_weights": confidence.weights(),
            "versions": st("confidence_versions", []), "examples": len(st("confidence_examples", [])),
            "detector_reliability": {k: {"alpha": v[0], "beta": v[1], "ppv": round(v[0] / (v[0] + v[1]), 3)} for k, v in rel.items()},
            "family_multiplier": st("family_multiplier", {}), "family_tally": st("family_tally", {}),
            "hypothesis_counts": st("hypothesis_counts", {}), "feedback": fb,
            "evaluation": json.loads(ev_path.read_text()) if ev_path.exists() else None,
            "settings": {"alert_alpha": gs().alert_alpha, "fdr_q": gs().fdr_q, "scan_replicates": gs().scan_replicates,
                         "notify_min_confidence": gs().notify_min_confidence}}


# --------------------------------------------------------------------------- security and audit
@app.get("/api/security/posture")
def api_posture(user: User = Depends(require("view_security"))):
    con = get_conn()
    chain = audit.verify_chain(con)
    inv = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in
           ("events", "labs", "raw_messages", "enrolment", "alerts", "feedback", "audit_log")}
    s = get_settings()
    return {"checks": run_checks(s), "egress_guard": {"installed": GUARD.installed, "allow_list": s.allowed_egress,
                                                      "blocked_attempts": list(GUARD.blocked)[-20:]},
            "keys": get_kms().describe(), "audit_chain": chain, "inventory": inv,
            "residency": {"country_code": s.country_code, "jurisdiction": s.jurisdiction, "hosting_region": s.hosting_region,
                          "approved_regions": s.allowed_storage_regions, "data_dir": str(s.data_dir),
                          "environment": s.environment},
            "data_minimisation": {
                "stored": ["keyed pseudonym of the patient", "age in years and age band", "sex", "neighbourhood (from postcode)",
                           "facility, date, encounter type", "diagnosis codes and triage text", "admission / ICU / death flags",
                           "laboratory panel, analyte and result"],
                "not_stored_in_analytics": ["name", "national ID", "street address", "date of birth", "phone", "medical record number"],
                "raw_messages": "Encrypted with AES-256-GCM (per-message key wrapped by the key-encryption key); "
                                "decryptable only by a data steward with written justification, which is audited."}}


@app.post("/api/security/egress-test")
def api_egress_test(body: dict = Body(...), user: User = Depends(require("view_security"))):
    url = body.get("url", "https://example.com")
    res = try_outbound(url)
    audit.record(user.name, user.role, "security.egress_test", url, res)
    return res


@app.get("/api/audit")
def api_audit(limit: int = Query(100, le=1000), offset: int = 0, action: str | None = None,
              user: User = Depends(require("view_audit"))):
    con = get_conn()
    q, args = "SELECT * FROM audit_log", []
    if action:
        q += " WHERE action LIKE ?"
        args.append(f"{action}%")
    total = con.execute(q.replace("*", "COUNT(*)"), args).fetchone()[0]
    rows = con.execute(q + " ORDER BY id DESC LIMIT ? OFFSET ?", args + [limit, offset]).fetchall()
    return {"total": total, "rows": [dict(r) | {"details": json.loads(r["details"])} for r in rows]}


@app.get("/api/audit/verify")
def api_audit_verify(user: User = Depends(require("view_audit"))):
    res = audit.verify_chain()
    audit.record(user.name, user.role, "audit.verify", "audit_log", {"valid": res["valid"], "entries": res["entries"]})
    return res


# --------------------------------------------------------------------------- demo controls
@app.get("/api/demo/clock")
def api_clock(user: User = Depends(current_user)):
    from ..demo import load_truth
    return {"clock": _clock(), "backfill_end": kv_get("backfill_end", None), "demo": bool(load_truth()),
            "environment": get_settings().environment}


@app.post("/api/demo/advance")
def api_advance(body: dict = Body(default={}), user: User = Depends(require("demo_control"))):
    from ..demo import advance_to
    if get_settings().environment == "production":
        raise HTTPException(403, "demo controls are disabled in production")
    days = max(1, min(int(body.get("days", 1)), 7))
    target = (date.fromisoformat(_clock()) + timedelta(days=days)).isoformat()
    res = advance_to(target, progress=lambda *a: None)
    audit.record(user.name, user.role, "demo.advance", target, {"days": days})
    return res


@app.post("/api/demo/rewind")
def api_rewind(body: dict = Body(...), user: User = Depends(require("demo_control"))):
    from ..demo import rewind_to
    if get_settings().environment == "production":
        raise HTTPException(403, "demo controls are disabled in production")
    target = body.get("date")
    res = rewind_to(target, progress=lambda *a: None)
    audit.record(user.name, user.role, "demo.rewind", target, {})
    return res


@app.get("/api/demo/scenarios")
def api_scenarios(user: User = Depends(require("demo_control"))):
    from ..demo import load_truth
    t = load_truth()
    return {"outbreaks": t.get("outbreaks", []), "note": t.get("note")}


# --------------------------------------------------------------------------- web app
@app.get("/", include_in_schema=False)
def index():
    return FileResponse(WEB / "index.html")


app.mount("/static", StaticFiles(directory=WEB), name="static")
