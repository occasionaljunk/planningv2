"""Demo environment: seed, replay and rewind.

``seed`` builds the simulated City of Riverton, bulk-loads history received up
to ``backfill_end`` (as a real deployment would on day one), keeps everything
received later as a pending feed, and then replays the pending feed day by day
through the real connectors (FHIR, HL7 v2, claims and death CSVs) with the
agent running every day, up to ``clock``.
"""
from __future__ import annotations

import json
import os
import pickle
import shutil
from datetime import date, timedelta

import pandas as pd

from .config import get_settings
from .db import close_thread_conn, get_conn, init_db, kv_get, kv_set, migrate
from .ingest.pipeline import IngestPipeline
from .reference import load_city_reference
from .security import audit
from .security.auth import seed_demo_users

BACKFILL_END = "2026-09-14"
DEFAULT_CLOCK = "2026-10-10"
HISTORY_RUNS = ["2024-03-11", "2024-03-18", "2024-03-25"]

_FEED: dict | None = None


def _paths():
    s = get_settings()
    return {"db": s.db_path, "feed": s.data_dir / "feed.pkl", "truth": s.data_dir / "ground_truth.json",
            "snapshot": s.data_dir / "snapshot.db", "keys": s.data_dir / "source_keys.json"}


def load_feed() -> dict:
    global _FEED
    if _FEED is None:
        with open(_paths()["feed"], "rb") as fh:
            _FEED = pickle.load(fh)
    return _FEED


def load_truth() -> dict:
    p = _paths()["truth"]
    return json.loads(p.read_text()) if p.exists() else {}


def get_clock() -> str:
    return kv_get("clock", BACKFILL_END)


def _reset_files() -> None:
    p = _paths()
    close_thread_conn()
    for suffix in ("", "-wal", "-shm"):
        f = str(p["db"]) + suffix
        if os.path.exists(f):
            os.remove(f)
    from .agent.data import clear_cache
    clear_cache()


def seed(clock: str = DEFAULT_CLOCK, backfill_end: str = BACKFILL_END, history: bool = True, progress=print) -> dict:
    from .synth.feeds import render_registry_csv, source_frames
    from .synth.generator import SimConfig, generate

    global _FEED
    _FEED = None
    p = _paths()
    _reset_files()
    con = get_conn()
    init_db(con)
    migrate(con)
    seed_demo_users()
    world = generate(SimConfig(), progress=progress)
    keys = load_city_reference(world.persons)
    p["keys"].write_text(json.dumps(keys, indent=1))
    os.chmod(p["keys"], 0o600)
    frames = source_frames(world)
    pipe = IngestPipeline(con)
    progress("[seed] registry enrolment via connector")
    pipe.ingest("SRC-EDU", "registry_csv", render_registry_csv(frames["registry"]), f"{world.config.start}T06:00:00")
    cutoff = f"{backfill_end}T23:59:59"
    events = pd.concat([frames[k] for k in ("ehr", "claims", "deaths")], ignore_index=True)
    events["icd10"] = events["icd10"].fillna("")
    hist = events[events["received_at"] <= cutoff]
    progress(f"[seed] bulk back-fill of {len(hist):,} records received up to {backfill_end}")
    pipe.bulk_events(hist, progress=progress)
    labs = frames["labs"]
    pipe.bulk_labs(labs[labs["received_at"] <= cutoff])
    pending = {k: frames[k][frames[k]["received_at"] > cutoff].reset_index(drop=True)
               for k in ("ehr", "claims", "deaths", "labs")}
    with open(p["feed"], "wb") as fh:
        pickle.dump(pending, fh)
    truth = {"outbreaks": world.outbreaks,
             "records": world.truth.to_dict(orient="records"),
             "note": "Demo ground truth. Never read by the agent; used for evaluation and scripted feedback only."}
    p["truth"].write_text(json.dumps(truth, default=str))
    kv_set("clock", backfill_end)
    kv_set("backfill_end", backfill_end)
    audit.record("system", "system", "demo.seed", "platform", {"records": int(len(hist)), "backfill_end": backfill_end})
    if history:
        seed_history(progress)
    con.commit()
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    shutil.copyfile(p["db"], p["snapshot"])
    if clock and clock > backfill_end:
        advance_to(clock, progress=progress)
    return {"clock": get_clock()}


def seed_history(progress=print) -> None:
    """Run the agent on historical dates and apply the feedback an epidemiologist gave at the time."""
    from .agent.feedback import submit_feedback
    from .agent.orchestrator import run_agent

    for d in HISTORY_RUNS:
        progress(f"[seed] historical agent run as of {d}")
        run_agent(date.fromisoformat(d), actor="system", notify=False)
    con = get_conn()
    rows = con.execute("SELECT id, top_hypothesis, syndrome_family, title FROM alerts").fetchall()
    for r in rows:
        if r["top_hypothesis"] == "measles" or r["syndrome_family"] == "RASH":
            submit_feedback(r["id"], "epi", "epidemiologist", {
                "verdict": "confirmed", "confirmed_cause": "measles",
                "findings_review": {"location": "correct", "age_profile": "correct", "hypothesis": "correct",
                                    "severity": "correct"},
                "actions": ["Case investigation and contact tracing", "Catch-up MMR vaccination clinics in Cedar Hills",
                            "Exclusion of unvaccinated contacts from school for 21 days"],
                "outcomes": {"cases": 43, "hospitalised": 9, "deaths": 0,
                             "notes": "Outbreak declared over on 20 April 2024. MMR coverage in Cedar Hills rose from 84% to 93%."},
                "notes": "Index case was an unvaccinated child returning from travel. Early alert gave us about a week."})
            con.execute("UPDATE alerts SET status='closed' WHERE id=?", (r["id"],))
        else:
            submit_feedback(r["id"], "epi", "epidemiologist", {
                "verdict": "inconclusive", "findings_review": {}, "actions": ["Reviewed; no action"],
                "outcomes": {}, "notes": "Historical alert reviewed during onboarding."})
            con.execute("UPDATE alerts SET status='closed' WHERE id=?", (r["id"],))
    con.commit()


def advance_to(target: str, run_agent_daily: bool = True, progress=print) -> dict:
    from .agent.orchestrator import run_agent
    from .synth.feeds import messages_for_window

    frames = load_feed()
    pipe = IngestPipeline(get_conn())
    clock = date.fromisoformat(get_clock())
    end = date.fromisoformat(target)
    summary = []
    while clock < end:
        nxt = clock + timedelta(days=1)
        msgs = messages_for_window(frames, f"{clock.isoformat()}T23:59:59", f"{nxt.isoformat()}T23:59:59")
        counts: dict[str, int] = {}
        for received_at, sid, fmt, payload in msgs:
            pipe.ingest(sid, fmt, payload, received_at, commit=False)
            counts[sid] = counts.get(sid, 0) + 1
        get_conn().commit()
        kv_set("clock", nxt.isoformat())
        audit.record("system", "system", "feed.ingest_day", nxt.isoformat(), {"messages": len(msgs), "by_source": counts})
        res = run_agent(nxt, actor="system") if run_agent_daily else None
        progress(f"[replay] {nxt}: {len(msgs)} messages ingested" +
                 (f"; agent: {res['signals']} signals, {res['events']} events, {res['new_alerts']} new alerts" if res else ""))
        summary.append({"date": nxt.isoformat(), "messages": len(msgs), "agent": res})
        clock = nxt
    return {"clock": get_clock(), "days": summary}


def rewind_to(target: str, progress=print) -> dict:
    """Restore the post-back-fill snapshot and replay forward to ``target``."""
    p = _paths()
    if not p["snapshot"].exists():
        raise RuntimeError("no snapshot; run seed first")
    _reset_files()
    shutil.copyfile(p["snapshot"], p["db"])
    from .agent.data import clear_cache
    clear_cache()
    if target > get_clock():
        return advance_to(target, progress=progress)
    return {"clock": get_clock()}
