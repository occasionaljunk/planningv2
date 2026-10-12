"""Government feedback and what the platform learns from it.

Each piece of feedback updates four things, all recorded and visible:

1. Confidence model: the alert's evidence features become a labelled example
   (confirmed / partially correct = real, false alarm = not real) and the
   logistic confidence model is re-fitted around its expert prior.
2. Detector reliability: Beta-Binomial positive-predictive value per detector.
3. Alert thresholds: families that keep producing false alarms get a stricter
   false-alarm rate (never looser than the configured base rate).
4. Local epidemiology: confirmed causes raise that cause's prior in the
   differential diagnosis; actions and outcomes become local history that
   future alerts cite.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from ..db import get_conn
from ..security import audit
from . import confidence

VERDICTS = {"confirmed": (1.0, 1.0), "partially_correct": (1.0, 0.6), "false_alarm": (0.0, 1.0), "inconclusive": (None, 0)}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def submit_feedback(alert_id: int, username: str, role: str, body: dict) -> dict:
    con = get_conn()
    row = con.execute("SELECT * FROM alerts WHERE id=?", (alert_id,)).fetchone()
    if not row:
        raise KeyError("alert not found")
    verdict = body.get("verdict")
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {list(VERDICTS)}")
    report = json.loads(row["report"])
    label, weight = VERDICTS[verdict]
    learning = {"verdict": verdict, "changes": []}
    before = confidence.weights()
    if label is not None:
        examples = confidence._state("confidence_examples", [])
        examples = [e for e in examples if e["alert_id"] != alert_id]
        examples.append({"alert_id": alert_id, "features": report["confidence"]["features"], "label": label,
                         "weight": weight, "ts": _now()})
        confidence._save("confidence_examples", examples)
        after = confidence.refit(examples)
        confidence._save("confidence_weights", after)
        versions = confidence._state("confidence_versions", [])
        versions.append({"version": len(versions) + 1, "ts": _now(), "examples": len(examples), "weights": after,
                         "trigger": f"feedback on alert #{alert_id} ({verdict})"})
        confidence._save("confidence_versions", versions)
        changed = sorted(((k, before[k], after[k]) for k in after if abs(after[k] - before[k]) >= 0.01),
                         key=lambda x: -abs(x[2] - x[1]))
        p_before = report["confidence"]["value"]
        p_after, _ = confidence.predict(report["confidence"]["features"], after)
        learning["changes"].append({"what": "confidence model", "examples": len(examples),
                                    "weights_changed": [{"feature": k, "from": round(a, 3), "to": round(b, 3)}
                                                        for k, a, b in changed[:6]],
                                    "this_alert_confidence": {"before": round(p_before, 3), "after": round(p_after, 3)}})
        # detector reliability
        rel = confidence._state("detector_reliability", {})
        fired = {d for s in report.get("signals", []) for d in s.get("detectors_fired", [])}
        for d in fired:
            a, b = rel.get(d, [1.0, 1.0])
            rel[d] = [a + label * weight, b + (1 - label) * weight]
        confidence._save("detector_reliability", rel)
        learning["changes"].append({"what": "detector reliability", "detectors": sorted(fired)})
        # alert thresholds per family
        fam = row["syndrome_family"]
        tally = confidence._state("family_tally", {})
        tp, fp = tally.get(fam, [0, 0])
        tp, fp = tp + (1 if label == 1 else 0), fp + (1 if label == 0 else 0)
        tally[fam] = [tp, fp]
        confidence._save("family_tally", tally)
        mult = confidence._state("family_multiplier", {})
        old = mult.get(fam, 1.0)
        new = float(min(1.0, max(0.25, 2 ** ((tp - fp) / 4))))
        mult[fam] = new
        confidence._save("family_multiplier", mult)
        if abs(new - old) > 1e-9:
            learning["changes"].append({"what": "alert threshold", "family": fam, "false_alarm_rate_multiplier":
                                        {"from": round(old, 3), "to": round(new, 3)}})
    cause = body.get("confirmed_cause")
    if cause and verdict in ("confirmed", "partially_correct"):
        counts = confidence._state("hypothesis_counts", {})
        counts[cause] = counts.get(cause, 0) + 1
        confidence._save("hypothesis_counts", counts)
        learning["changes"].append({"what": "local epidemiology", "cause": cause, "confirmed_events": counts[cause]})
    if verdict == "false_alarm":
        counts = confidence._state("hypothesis_counts", {})
        counts["reporting_artefact"] = counts.get("reporting_artefact", 0) + (1 if body.get("confirmed_cause") ==
                                                                               "reporting_artefact" else 0)
        confidence._save("hypothesis_counts", counts)
    con.execute("INSERT INTO feedback(alert_id, created_at, username, verdict, confirmed_cause, findings_review, actions, "
                "outcomes, notes, learning) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (alert_id, _now(), username, verdict, cause, json.dumps(body.get("findings_review", {})),
                 json.dumps(body.get("actions", [])), json.dumps(body.get("outcomes", {})), body.get("notes", ""),
                 json.dumps(learning)))
    if body.get("close"):
        con.execute("UPDATE alerts SET status='closed' WHERE id=?", (alert_id,))
    elif row["status"] == "new":
        con.execute("UPDATE alerts SET status='investigating' WHERE id=?", (alert_id,))
    con.commit()
    audit.record(username, role, "feedback.submit", f"alert:{alert_id}", {"verdict": verdict, "cause": cause})
    return learning


def learned_priors() -> dict[str, int]:
    return confidence._state("hypothesis_counts", {})


def family_multiplier() -> dict[str, float]:
    return confidence._state("family_multiplier", {})
