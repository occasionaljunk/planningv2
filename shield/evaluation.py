"""Validate the agent against simulation ground truth.

Reads the agent's alert history from the database (no re-runs needed) and
compares it with what was actually simulated: when each outbreak started,
when the agent first raised an alert containing its cases, what it said the
cause was at first detection and at the end, and how many alerts did not
correspond to any simulated event.
"""
from __future__ import annotations

import json
from datetime import date

from .config import get_settings
from .db import get_conn
from .demo import load_truth


def evaluate(write: bool = True) -> dict:
    con = get_conn()
    truth = load_truth()
    if not truth:
        raise RuntimeError("no ground truth: run the demo seed first")
    rec_by_outbreak: dict[str, set] = {}
    for r in truth["records"]:
        rec_by_outbreak.setdefault(r["truth_outbreak"], set()).add(r["encounter_id"])
    alerts = con.execute("SELECT id, title, first_as_of, history, confidence, top_hypothesis, report FROM alerts").fetchall()
    alert_enc = {}
    for a in alerts:
        alert_enc[a["id"]] = {r[0] for r in con.execute(
            "SELECT e.encounter_id FROM alert_cases ac JOIN events e ON e.id = ac.event_id WHERE ac.alert_id=?", (a["id"],))}
    notify = get_settings().notify_min_confidence
    scenarios, matched_alerts = [], set()
    for ob in truth["outbreaks"]:
        recs = rec_by_outbreak.get(ob["id"], set())
        hits = [a for a in alerts if recs and len(alert_enc[a["id"]] & recs) >= 3]
        if ob["id"] == "coding_artefact_2026":
            hits = [a for a in alerts if a["top_hypothesis"] == "reporting_artefact" or len(alert_enc[a["id"]] & recs) >= 3]
        matched_alerts |= {a["id"] for a in hits}
        row = {"id": ob["id"], "name": ob["name"], "start": ob["start"], "expected": ob.get("hypothesis")}
        if hits:
            first = min(hits, key=lambda a: a["first_as_of"])
            hist = json.loads(first["history"])
            hist = [h for h in hist if "confidence" in h]
            notified = [h for h in hist if h["confidence"] >= notify]
            row.update({"first_alert": first["first_as_of"],
                        "first_notified": notified[0]["as_of"] if notified else None,
                        "days_to_detect": (date.fromisoformat(first["first_as_of"]) - date.fromisoformat(ob["start"])).days,
                        "first_hypothesis": hist[0].get("top_hypothesis") if hist else None,
                        "final_hypothesis": json.loads(first["report"])["hypotheses"][0]["name"],
                        "final_confidence": first["confidence"],
                        "correct_cause": first["top_hypothesis"] == ob.get("hypothesis")})
        else:
            row.update({"first_alert": None, "days_to_detect": None, "correct_cause": False})
        scenarios.append(row)
    unmatched = [a for a in alerts if a["id"] not in matched_alerts]
    unmatched_notified = [a for a in unmatched if a["confidence"] >= notify]
    out = {
        "description": "Agent alerts compared with the simulated ground truth (the agent never reads it). "
                       "'Days to detect' counts from the start of the simulated outbreak or exposure, before most "
                       "patients had even sought care.",
        "scenarios": scenarios,
        "alerts_total": len(alerts), "alerts_not_matching_any_scenario": len(unmatched),
        "of_which_above_notification_threshold": len(unmatched_notified),
        "unmatched_titles": [a["title"] for a in unmatched],
        "false_alarm_summary": f"{len(unmatched)} of {len(alerts)} alerts did not correspond to a simulated event; "
                               f"{len(unmatched_notified)} of those reached the notification threshold (the rest stayed "
                               "on the watch list).",
    }
    if write:
        (get_settings().data_dir / "evaluation.json").write_text(json.dumps(out, indent=1))
    return out
