"""Stage 5: calibrated confidence that an event is real and worth investigating.

A logistic model over interpretable evidence features. It starts from expert
prior weights and is re-fitted (maximum a posteriori, Gaussian prior centred
on the expert weights) every time the government gives feedback, so the
platform adapts to local data and practice without forgetting sensible
defaults after a handful of examples. Every alert shows each feature's
contribution to its confidence.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np

from ..db import get_conn

FEATURES = [
    ("statistical_strength", "Strength of the statistical signal (smallest p-value across detectors, log scale)"),
    ("detector_agreement", "Agreement between independent detector families (time-series models, spatial scans, laboratory, sharp-onset)"),
    ("source_corroboration", "Number of independent institutions showing an excess"),
    ("lab_support", "Laboratory results support a specific cause or show unexplained illness"),
    ("persistence", "Days in the last week with clearly elevated counts"),
    ("size", "Number of excess cases (log scale)"),
    ("growth", "Sustained growth (reproduction number above 1 or measurable doubling time)"),
    ("event_significance", "Statistical significance of the event's own 14-day excess"),
    ("relative_increase", "How far counts are above the expected level (log scale)"),
    ("source_concentration", "Share of the excess coming from a single data source"),
    ("coding_anomaly", "Abrupt change in diagnosis coding or mismatch between free text and codes"),
    ("chance_probability", "Probability, from the differential diagnosis, that this is chance variation"),
    ("artefact_probability", "Probability, from the differential diagnosis, that this is a data or coding artefact"),
]
PRIOR = {"bias": -3.8, "statistical_strength": 2.2, "detector_agreement": 1.6, "source_corroboration": 1.6,
         "lab_support": 1.4, "persistence": 1.0, "size": 1.0, "growth": 0.9, "event_significance": 1.5,
         "relative_increase": 1.5, "source_concentration": -2.6, "coding_anomaly": -2.4, "chance_probability": -3.0,
         "artefact_probability": -2.0}
PRIOR_STRENGTH = 2.0   # Gaussian prior precision around the expert weights
LEVELS = [(0.8, "Very high"), (0.6, "High"), (0.4, "Moderate"), (0.0, "Low")]


def features(e, ch: dict, hyps: list[dict]) -> dict[str, float]:
    sigs = e.signals
    strength = min(max((s.strength for s in sigs), default=0), 12) / 12
    fams = set()
    for s in sigs:
        for f in s.fired:
            if f in ("regression_7d", "farrington_flexible"):
                fams.add("temporal")
            elif f in ("space_time_permutation", "expectation_based_poisson"):
                fams.add("spatial")
            elif f.startswith("lab"):
                fams.add("lab")
            elif f in ("ears_c3", "cusum"):
                fams.add("sharp")
    lab_support = 0.0
    if any(s.kind == "lab" for s in sigs):
        lab_support = 1.0
    top = hyps[0] if hyps else None
    if top and top["id"] not in ("chance", "reporting_artefact") and any(ev["delta"] >= 3 for ev in top.get("evidence_for", [])):
        lab_support = 1.0
        fams.add("lab")
    curve = ch["epi_curve"]
    obs = np.array(curve["observed"][-7:], float)
    exp = np.array(curve["expected"][-7:], float)
    persistence = float(np.mean(obs > exp + 2 * np.sqrt(np.maximum(exp, 1)))) if len(obs) else 0.0
    conc = ch["top_excess_source_share"] if ch["n_sources_with_excess"] <= 1 else ch["top_excess_source_share"] * 0.5
    coding = ch.get("coding") or {}
    shifts = coding.get("top_code_shifts") or []
    dis, dis_b = coding.get("text_code_disagreement"), coding.get("baseline_text_code_disagreement")
    anomaly = 0.0
    if shifts and shifts[0]["change"] >= 0.2:
        anomaly += 0.6
    if dis is not None and dis_b is not None and dis - dis_b >= 0.2:
        anomaly += 0.6
    growth = ch.get("growth") or {}
    rt = ch.get("rt") or {}
    pw = max(ch.get("p_value_window", 1.0), 1e-12)
    chance = next((h["probability"] for h in hyps if h["id"] == "chance"), 0.0)
    artefact = next((h["probability"] for h in hyps if h["id"] == "reporting_artefact"), 0.0)
    return {
        "statistical_strength": round(strength, 3),
        "detector_agreement": round(len(fams) / 4, 3),
        "source_corroboration": round(min(ch["n_sources_with_excess"], 3) / 3, 3),
        "lab_support": lab_support,
        "persistence": round(persistence, 3),
        "size": round(min(np.log1p(ch["excess"]) / np.log1p(200), 1.0), 3),
        "growth": 1.0 if (growth.get("doubling_time_days") or rt.get("rt_lo", 0) > 1.0) else 0.0,
        "event_significance": round(min(-np.log10(pw), 12) / 12, 3),
        "relative_increase": round(float(np.clip(np.log2(max(ch["ratio"], 1e-6)), 0, 1.5) / 1.5), 3),
        "source_concentration": round(float(conc) if ch["excess"] > 0 else 0.0, 3),
        "coding_anomaly": round(min(anomaly, 1.0), 3),
        "chance_probability": round(float(chance), 3),
        "artefact_probability": round(float(artefact), 3),
    }


def _state(key: str, default):
    row = get_conn().execute("SELECT value FROM model_state WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def _save(key: str, value) -> None:
    con = get_conn()
    con.execute("INSERT INTO model_state(key, value, updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET "
                "value=excluded.value, updated_at=excluded.updated_at",
                (key, json.dumps(value), datetime.now(timezone.utc).isoformat(timespec="seconds")))
    con.commit()


def weights() -> dict[str, float]:
    return _state("confidence_weights", dict(PRIOR))


def model_version() -> int:
    return len(_state("confidence_versions", []))


def predict(f: dict[str, float], w: dict[str, float] | None = None) -> tuple[float, list[dict]]:
    w = w or weights()
    z = w["bias"]
    contrib = []
    for name, desc in FEATURES:
        c = w[name] * f[name]
        z += c
        contrib.append({"feature": name, "description": desc, "value": f[name], "weight": round(w[name], 3),
                        "contribution": round(c, 3)})
    p = 1 / (1 + np.exp(-z))
    contrib.sort(key=lambda r: -abs(r["contribution"]))
    return float(p), contrib


def level(p: float) -> str:
    for thr, name in LEVELS:
        if p >= thr:
            return name
    return "Low"


def refit(examples: list[dict]) -> dict[str, float]:
    """MAP logistic regression with a Gaussian prior centred on the expert weights."""
    names = ["bias"] + [n for n, _ in FEATURES]
    prior = np.array([PRIOR[n] for n in names])
    if not examples:
        return dict(PRIOR)
    X = np.array([[1.0] + [ex["features"][n] for n, _ in FEATURES] for ex in examples])
    y = np.array([ex["label"] for ex in examples], float)
    sw = np.array([ex.get("weight", 1.0) for ex in examples], float)
    theta = prior.copy()
    lam = PRIOR_STRENGTH
    for _ in range(50):
        p = 1 / (1 + np.exp(-(X @ theta)))
        g = X.T @ (sw * (p - y)) + lam * (theta - prior)
        H = (X * (sw * p * (1 - p))[:, None]).T @ X + lam * np.eye(len(theta))
        step = np.linalg.solve(H, g)
        theta -= step
        if np.max(np.abs(step)) < 1e-8:
            break
    return {n: round(float(t), 4) for n, t in zip(names, theta)}
