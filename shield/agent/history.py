"""Stage 7: historical context.

Matches the leading hypotheses to documented past outbreaks (knowledge base
with sources) and to this jurisdiction's own confirmed events (from feedback),
and scales published attack, hospitalisation and death rates to the affected
population to give an order-of-magnitude sense of what is at stake.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from ..db import get_conn

KB = Path(__file__).resolve().parent.parent / "knowledge" / "historical_epidemics.json"


@lru_cache(maxsize=1)
def knowledge() -> list[dict]:
    return json.loads(KB.read_text())["events"]


def context(hyps: list[dict], ch: dict, affected_population: int, city_population: int) -> dict:
    ids = [h["id"] for h in hyps[:3] if h["probability"] >= 0.1] or [hyps[0]["id"]] if hyps else []
    analogues = []
    for rank, hid in enumerate(ids):
        for ev in knowledge():
            if hid in ev["matches"] and ev["id"] not in {a["id"] for a in analogues}:
                a = {k: ev.get(k) for k in ("id", "name", "years", "location", "pathogen", "cases", "hospitalisations",
                                             "deaths", "economic_impact", "social_impact", "lessons", "sources")}
                a["matched_hypothesis"] = hid
                rates = ev.get("rates")
                if rates:
                    proj = {}
                    for label, pop in (("affected_area", affected_population), ("whole_city", city_population)):
                        if not pop:
                            continue
                        p = {"population": int(pop)}
                        if "attack_rate" in rates:
                            p["cases"] = int(round(rates["attack_rate"] * pop))
                        if "hospitalisation_per_100k" in rates:
                            p["hospitalisations"] = int(round(rates["hospitalisation_per_100k"] * pop / 1e5))
                        if "deaths_per_100k" in rates:
                            p["deaths"] = int(round(rates["deaths_per_100k"] * pop / 1e5))
                        proj[label] = p
                    a["projection"] = proj
                    a["projection_note"] = ("Illustrative scaling of the published rates to this population; not a forecast. "
                                            "Actual impact depends on the pathogen, immunity, health-system capacity and the response.")
                analogues.append(a)
    analogues = analogues[:5]
    local = []
    con = get_conn()
    rows = con.execute(
        "SELECT a.id, a.title, a.first_as_of, a.syndrome_family, f.verdict, f.confirmed_cause, f.actions, f.outcomes, f.notes "
        "FROM feedback f JOIN alerts a ON a.id = f.alert_id WHERE f.verdict IN ('confirmed','partially_correct') "
        "ORDER BY f.created_at DESC LIMIT 20").fetchall()
    for r in rows:
        if r["syndrome_family"] == ch["family"] or r["confirmed_cause"] in ids:
            local.append({"alert_id": r["id"], "title": r["title"], "detected": r["first_as_of"], "verdict": r["verdict"],
                          "confirmed_cause": r["confirmed_cause"], "actions": json.loads(r["actions"] or "[]"),
                          "outcomes": json.loads(r["outcomes"] or "{}"), "notes": r["notes"]})
    return {"analogues": analogues, "local_history": local[:3]}
