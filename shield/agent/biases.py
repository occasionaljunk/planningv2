"""Stage 6: biases and limitations, computed from the data for each event.

Every alert states what could make it wrong, quantified where possible, so a
decision-maker can weigh it and an analyst knows what to check first.
"""
from __future__ import annotations

import numpy as np
from scipy import stats

from .data import AnalysisData


def assess(ch: dict, data: AnalysisData, n_series: int, fdr_q: float) -> list[dict]:
    out = []

    def add(kind, severity, direction, text, metric=None):
        out.append({"type": kind, "severity": severity, "direction": direction, "description": text, "metric": metric})

    T = data.T
    # 1. coverage: visit capture per resident by neighbourhood (where residents' care is not directly connected)
    pop = data.ref.pop_by_nb()
    base = (data.a_day > T - 365) & data.a_visit & (data.a_nb >= 0)
    visits = np.bincount(data.a_nb[base], minlength=data.ref.n_nb).astype(float)
    per_cap = visits / np.maximum(pop, 1)
    city = np.median(per_cap)
    ev_nbs = [g["id"] for g in ch["geography"][:4]]
    low = [data.ref.nb_names[n] for n in ev_nbs if per_cap[n] < 0.8 * city]
    claims_share = 0.0
    if ch["case_event_ids"]:
        ids = set(ch["case_event_ids"])
        m = np.isin(data.a_id, list(ids))
        claims_share = float((data.ev["record_type"].to_numpy(dtype=object)[m] == "claim").mean()) if m.any() else 0.0
    if low:
        add("coverage", "medium", "under-estimate",
            f"Fewer records per resident than the city median in {', '.join(low)}: part of this population is treated at "
            "facilities without a direct data feed (visible only through insurance claims, days later) or is uninsured.",
            {"records_per_resident_city_median": round(float(city), 2),
             "by_neighbourhood": {data.ref.nb_names[n]: round(float(per_cap[n]), 2) for n in ev_nbs}})
    if claims_share > 0.05:
        add("coverage", "low", "delay",
            f"{claims_share:.0%} of case records reached the platform only through insurance claims, which arrive 3-21 days "
            "after the visit; recent days are therefore incomplete for these facilities.", {"claims_share": round(claims_share, 3)})
    # 2. reporting delay
    comp = data.delay_completeness()
    shares = {r["source_id"]: r["share"] for r in ch["sources"]}
    f0 = sum(shares.get(s, 0) * comp.get(s, np.ones(31))[0] for s in shares) or 1.0
    f2 = sum(shares.get(s, 0) * comp.get(s, np.ones(31))[2] for s in shares) or 1.0
    if f0 < 0.97:
        add("reporting_delay", "medium" if f0 < 0.85 else "low", "under-estimate",
            f"About {100 * (1 - f0):.0f}% of the records for the most recent day are typically still in transit "
            f"({100 * (1 - f2):.0f}% after two days). Latest counts are nowcast-corrected; growth may be understated.",
            {"completeness_day0": round(float(f0), 3), "completeness_day2": round(float(f2), 3)})
    # 3. testing / ascertainment
    for p in ch["labs"]:
        if p["tested"] >= 5:
            tested_share = p["tested"] / max(ch["observed"], 1)
            add("testing", "low", "either",
                f"{p['tested']} of {ch['observed']} case-patients ({tested_share:.0%}) had a {p['panel'].lower()}. "
                "Patients who are tested are sicker or present to hospital, so laboratory results may not represent milder cases.",
                {"tested": p["tested"], "tested_share": round(tested_share, 3)})
            break
    else:
        add("testing", "medium", "unknown", "Few or no case-patients have been tested, so the cause cannot be confirmed "
            "from laboratory data yet.")
    # 4. single-source concentration
    if ch["top_excess_source_share"] >= 0.6 and ch["excess"] > 0:
        add("data_source", "high" if ch["top_excess_source_share"] >= 0.8 else "medium", "over-estimate",
            f"{ch['top_excess_source_share']:.0%} of the excess comes from {data.ref.source_name(ch['top_excess_source'])}. "
            "A change in one institution's systems, coding or patient volume can mimic an outbreak.",
            {"source": ch["top_excess_source"], "share": ch["top_excess_source_share"]})
    # 5. small numbers
    if ch["observed"] < 30:
        lo, hi = stats.poisson.interval(0.95, max(ch["observed"], 0.5))
        add("small_numbers", "medium", "either",
            f"Only {ch['observed']} cases: the 95% interval for the true count is roughly {lo:.0f}-{hi:.0f}, so ratios "
            "and profiles are uncertain.", {"observed": ch["observed"], "ci95": [float(lo), float(hi)]})
    # 6. multiple testing
    add("multiple_testing", "low", "over-estimate",
        f"The agent tested {n_series} syndrome-by-population series today plus spatial scans. False discoveries are "
        f"controlled at {fdr_q:.0%} (Benjamini-Hochberg) and scan clusters require p <= 0.005, but some alerts will still be chance findings.",
        {"series_tested": n_series, "fdr_q": fdr_q})
    # 7. care-seeking differences by age
    age = ch["demographics"]["age"]
    young_adult = next((a for a in age if a["band"] == "18-24"), None)
    add("care_seeking", "low", "under-estimate",
        "Young adults and men seek care less often for mild illness, and students mostly use campus clinics; older adults "
        "and young children are over-represented in emergency departments. Case counts reflect who seeks care, not who is ill.",
        {"share_18_24": young_adult["share"] if young_adult else None})
    # 8. syndrome classification
    cod = ch.get("coding") or {}
    dis = cod.get("text_code_disagreement")
    if dis is not None:
        dis_b = cod.get("baseline_text_code_disagreement") or 0
        add("classification", "high" if dis - dis_b >= 0.2 else "low", "either",
            f"Syndromes are assigned from diagnosis codes and triage text. For {dis:.0%} of these cases the free text does "
            f"not describe the syndrome (baseline {dis_b:.0%}); coding practice changes shift counts.",
            {"text_code_disagreement": round(dis, 3), "baseline": round(dis_b, 3)})
    # 9. model assumptions
    add("model_assumptions", "low", "either",
        "Expected counts come from the last two years of the same population, with seasonality, day-of-week and holiday "
        "effects. An unusually early or late seasonal wave, a new facility or a change in the population can look like an "
        "outbreak, and a slow rise may be partly absorbed into the baseline.")
    if ch["family"] == "MORTALITY" or ch["severity"]["deaths"] > 0:
        add("mortality_lag", "medium", "under-estimate",
            "Deaths outside hospital reach the platform only through life-insurance claims (typically 1-4 weeks later) and only "
            "for insured adults. Recent mortality is under-counted.")
    order = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda b: order[b["severity"]])
    return out
