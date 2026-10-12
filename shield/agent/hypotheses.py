"""Stage 4: differential diagnosis.

A transparent Bayesian scoring of candidate explanations (pathogens and a
"data artefact" explanation) against the characterised evidence. Each piece of
evidence contributes a log-likelihood ratio that is shown to the user, so
analysts can see exactly why one hypothesis ranks above another and challenge
it. Probabilities are relative among the listed hypotheses; they are not a
clinical diagnosis.
"""
from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from pathlib import Path

import numpy as np

KB = Path(__file__).resolve().parent.parent / "knowledge" / "pathogens.json"


@lru_cache(maxsize=1)
def knowledge() -> list[dict]:
    return json.loads(KB.read_text())["hypotheses"]


def _panel(ch: dict, name: str | None) -> dict | None:
    if not name:
        return None
    return next((p for p in ch["labs"] if p["panel"] == name), None)


def score(ch: dict, as_of: date, local_counts: dict | None = None) -> list[dict]:
    fam = ch["family"]
    cands = [h for h in knowledge() if h["family"] in (fam, "ANY")]
    if not cands:
        return []
    age = ch["demographics"]["age"]
    n_cases = sum(a["cases"] for a in age)
    excess_frac = min(max(ch["excess"] / max(ch["observed"], 1), 0.0), 0.95)
    base_share = np.array([a["baseline_share"] for a in age])
    counts = np.array([a["cases"] for a in age], float)
    growth = ch.get("growth") or {}
    rt = ch.get("rt") or {}
    propagated = bool(growth.get("doubling_time_days")) or (rt.get("rt_lo", 0) > 1.0)
    declining = bool(growth.get("halving_time_days"))
    n_places = len([g for g in ch["geography"] if (g.get("relative_risk") or 0) >= 1.5])
    compact = ch["kind"] != "citywide" and n_places <= 2
    sev = ch["severity"]
    sources_concentrated = ch["top_excess_source_share"] >= 0.8 and ch["n_sources_with_excess"] <= 1
    coding = ch.get("coding") or {}
    shifts = coding.get("top_code_shifts") or []
    big_code_shift = bool(shifts and shifts[0]["change"] >= 0.2)
    dis, dis_b = coding.get("text_code_disagreement"), coding.get("baseline_text_code_disagreement")
    text_mismatch = dis is not None and dis_b is not None and dis - dis_b >= 0.2
    results = []
    for h in cands:
        ev = []

        def add(delta, text):
            ev.append({"delta": round(float(delta), 2), "text": text})

        is_artefact = h["id"] == "reporting_artefact"
        if h["id"] == "chance":
            pw = ch.get("p_value_window", 1.0)
            if pw > 0.05:
                add(2.0, f"The 14-day excess is not statistically significant on its own (p = {pw:.2f})")
            elif pw > 0.001:
                add(0.0, f"The 14-day excess is modest (p = {pw:.3f})")
            else:
                add(-3.0, f"The excess is very unlikely to be chance (p < 0.001)")
            if ch["ratio"] < 1.2:
                add(1.0, f"Counts are only {ch['ratio']:.2f} times the expected level")
            if ch["excess"] >= 30:
                add(-1.5, f"{ch['excess']:.0f} excess cases")
            if any(p["tested"] >= 3 and any(v >= 2 for v in p["positives"].values()) for p in ch["labs"]) and ch["family"] != "RESPIRATORY":
                add(-0.5, "Some laboratory confirmation")
        elif not is_artefact:
            # age profile: multinomial likelihood of the observed age mix under a mixture of baseline and pathogen excess
            prof = np.array(h["age_profile"], float)
            q_h = base_share * prof
            q_h = q_h / q_h.sum()
            q = (1 - excess_frac) * base_share + excess_frac * q_h
            q_flat = (1 - excess_frac) * base_share + excess_frac * base_share
            ll = float((counts * (np.log(q) - np.log(q_flat))).sum())
            ll = float(np.clip(ll * 0.5 / max(np.sqrt(max(n_cases, 1)) / 4, 1), -4, 4))
            if abs(ll) >= 0.2:
                add(ll, "Age profile of cases " + ("matches" if ll > 0 else "does not match") + f" the typical profile of {h['name'].lower()}")
            # notifiable-condition codes recorded by clinicians
            cond = {"Measles": "measles", "Invasive meningococcal disease": "meningococcal",
                    "Legionnaires' disease": "legionella"}
            for c in ch.get("notifiable", []):
                if cond.get(c) == h["id"]:
                    add(3.0, f"Clinicians recorded the notifiable diagnosis '{c}'")
            # season
            m = as_of.month
            if h["season_months"]:
                if m in h["season_months"]:
                    add(0.4, "Occurs during its usual season")
                else:
                    add(-0.7, "Outside its usual season")
            # laboratory
            lab = h["lab"] or {}
            panel = _panel(ch, lab.get("panel"))
            if lab.get("expects_negative_panels"):
                rp = _panel(ch, "Respiratory pathogen panel (NAAT)")
                if rp and rp["tested"] >= 6 and rp["negative_for_all_pct"] is not None:
                    neg, negb = rp["negative_for_all_pct"], rp["baseline_negative_for_all_pct"] or 0
                    pv = rp.get("p_value_negative_excess") or 1
                    if neg >= 85 and pv < 0.01:
                        add(3.2, f"{neg:.0f}% of {rp['tested']} respiratory panels negative for all routinely tested "
                                 f"viruses (baseline {negb:.0f}%): illness not explained by known pathogens")
                    elif neg >= 75 and neg > negb + 5 and pv < 0.05:
                        add(2.0, f"{neg:.0f}% of {rp['tested']} respiratory panels negative for all routinely tested "
                                 f"viruses (baseline {negb:.0f}%)")
                    elif neg < negb + 5:
                        add(-2.0, "Respiratory panels are no more often negative than usual; known viruses explain cases")
            elif panel and lab.get("analytes"):
                pos = sum(panel["positives"].get(a, 0) for a in lab["analytes"])
                base_pos = sum(panel["baseline_positivity"].get(a, 0) for a in lab["analytes"])
                share = pos / max(panel["tested"], 1)
                if pos >= 2 and share >= max(2 * base_pos, 0.15):
                    add(3.5, f"{pos} of {panel['tested']} tested case-patients positive for {', '.join(lab['analytes'])} "
                             f"({share:.0%} vs {base_pos:.0%} baseline)")
                elif panel["tested"] >= 5 and pos == 0:
                    add(-2.5, f"0 of {panel['tested']} tested case-patients positive for {', '.join(lab['analytes'])}")
                elif panel["tested"] >= 8 and share < 0.75 * base_pos:
                    add(-1.8, f"Positivity for {', '.join(lab['analytes'])} lower than usual ({share:.0%} vs {base_pos:.0%}): "
                              "the rise is not driven by this pathogen")
                elif panel["tested"] >= 5 and share < base_pos:
                    add(-1.0, f"Positivity for {', '.join(lab['analytes'])} not raised ({share:.0%} vs {base_pos:.0%})")
            elif lab.get("panel") and not panel:
                add(-0.2, f"No {lab['panel'].lower()} results yet for case-patients")
            # transmission pattern
            pat = h["pattern"]
            if propagated:
                add(1.0 if pat.startswith("propagated") else (0.3 if "then_propagated" in pat else -0.8),
                    "Cases are increasing in a pattern consistent with person-to-person spread" if pat.startswith("propagated")
                    else "Sustained growth is less typical of a single exposure")
            meaningful = ch["ratio"] >= 1.3 and ch["excess"] >= 8
            if compact and meaningful and ch["onset_concentration"] >= 0.5 and not propagated:
                add(1.2 if "point_source" in pat else -0.4, "Cases concentrated in a few days and places (consistent with a common exposure)")
            if pat == "point_source" and ch["kind"] == "locus" and compact and meaningful:
                add(0.6, "Geographically compact cluster")
            if declining and pat.startswith("propagated"):
                add(-0.3, "Cases already declining")
            # setting
            st = ch["settings"]
            if st["university"]["cases"] >= max(0.25, 2 * st["university"]["baseline"]):
                add(0.8 if "university" in h["settings"] else -0.3, "Cases concentrated among university students")
            school = max(st["primary_school"]["cases"] - st["primary_school"]["baseline"],
                         st["secondary_school"]["cases"] - st["secondary_school"]["baseline"])
            if school >= 0.15:
                add(0.8 if "school" in h["settings"] else -0.3, "Cases concentrated among school pupils")
            # severity
            ratio = sev["admission_ratio"]
            if ratio >= 1.5 and sev["p_value_admissions"] < 0.05:
                add({"high": 1.2, "elevated": 0.8, "moderate": 0.1, "low": -0.8}.get(h["severity"], 0),
                    f"Admission rate {sev['admitted_pct']}% vs {sev['baseline_admitted_pct']}% baseline")
            elif ratio <= 1.1 and h["severity"] == "high":
                add(-0.8, "Severity not raised, unlike typical presentations")
            if h["id"] == "legionella" and ch["demographics"]["male_share"] - ch["demographics"]["baseline_male_share"] >= 0.1:
                add(0.6, "Male predominance among cases")
            # a single data source carrying all the excess argues against any real pathogen
            if sources_concentrated and ch["observed"] > 0:
                add(-1.5, f"Almost all excess comes from one data source")
        else:
            osc = ch.get("other_source_check")
            if osc and osc.get("corroborated"):
                add(-3.0, f"Other institutions see the same rise among residents of {', '.join(osc['neighbourhoods'][:3])} "
                          f"({osc['observed_7d']} vs {osc['expected_7d']} expected in 7 days)")
            elif sources_concentrated:
                add(1.2, f"{ch['top_excess_source_share']:.0%} of the excess comes from a single data source; "
                         "other institutions serving the same population show no rise"
                         + (f" ({osc['observed_7d']} vs {osc['expected_7d']} expected)" if osc else ""))
            else:
                add(-2.5, f"Excess is corroborated by {ch['n_sources_with_excess']} independent data sources")
            if big_code_shift:
                s0 = shifts[0]
                add(2.0, f"Diagnosis code {s0['code']} jumped from {s0['baseline_share']:.0%} to {s0['case_share']:.0%} of codes")
            if text_mismatch:
                add(1.5, f"{dis:.0%} of records have free text that does not describe this syndrome (baseline {dis_b:.0%})")
            if not big_code_shift and not text_mismatch:
                add(-1.0, "No change in diagnosis coding or in the agreement between free text and codes")
            if not any(p["tested"] >= 3 and sum(p["positives"].values()) > 0 for p in ch["labs"]):
                add(0.4, "No laboratory corroboration")
            else:
                add(-1.0, "Laboratory results support a real illness")
            if propagated:
                add(-0.5, "Growth pattern looks epidemiological")
            if ch["kind"] != "facility":
                add(-1.0, "Signal is defined by where patients live, not by one facility")
        lc = local_counts or {}
        n_total = sum(lc.values())
        prior = (h["prior"] * 20 + lc.get(h["id"], 0)) / (20 + n_total)
        if lc.get(h["id"]):
            add(0.0, f"Locally confirmed {lc[h['id']]} time(s) before (prior raised from feedback)")
        logp = np.log(prior) + sum(x["delta"] for x in ev)
        results.append({"id": h["id"], "name": h["name"], "log_score": logp, "evidence": ev, "tests": h["tests"],
                        "transmission": h["transmission"], "serial_interval": h["serial_interval"],
                        "incubation_days": h["incubation_days"]})
    ls = np.array([r["log_score"] for r in results])
    p = np.exp(ls - ls.max())
    p = p / p.sum()
    for r, pp in zip(results, p):
        r["probability"] = round(float(pp), 3)
        r["evidence_for"] = [e for e in r["evidence"] if e["delta"] > 0]
        r["evidence_against"] = [e for e in r["evidence"] if e["delta"] < 0]
        r["notes"] = [e["text"] for e in r["evidence"] if e["delta"] == 0]
        del r["log_score"], r["evidence"]
    results.sort(key=lambda r: -r["probability"])
    return results[:5]
