"""Stage 8: turn the structured findings into a briefing.

Default backend is deterministic templates: every sentence is generated from
numbers computed upstream, so the text can never state something the analysis
did not find. Optionally (``narrative.backend = "local_llm"``) a language
model hosted inside the country rewrites the summary for readability; it
receives only the structured findings (no records) and its output is shown
alongside, never instead of, the computed facts.
"""
from __future__ import annotations

import json

from ..config import get_settings
from ..security.residency import GUARD

PRIORITY_TEXT = {"P1": "Act now", "P2": "Investigate within 48 hours", "P3": "Monitor"}


def _fmt_p(p: float) -> str:
    return "<0.001" if p < 0.001 else f"{p:.3f}"


def title(ch: dict, hyps: list[dict], confidence: float = 0.0) -> str:
    top = hyps[0] if hyps else None
    syn = ch["case_syndrome_label"]
    place = ch["place"]
    if top and top["id"] == "reporting_artefact" and top["probability"] >= 0.4:
        return f"Probable data artefact: apparent rise in {syn} at {place}"
    if top and top["id"] == "chance" and top["probability"] >= 0.4:
        if confidence < 0.5:
            return f"Possible chance fluctuation: {syn} in {place}"
        return f"Unexplained increase in {syn}: {place}"
    if ch["family"] == "MORTALITY":
        return f"Excess deaths: {place}"
    lead = top["name"] if top and top["probability"] >= 0.35 and top["id"] != "chance" else "Unexplained increase"
    shape = "cluster" if ch["kind"] == "locus" else "increase"
    return f"{lead} suspected: {syn} {shape} in {place}" if lead != "Unexplained increase" else \
        f"Unexplained {shape} in {syn}: {place}"


def summary(ch: dict, hyps: list[dict], conf: float, lvl: str) -> str:
    s = []
    s.append(f"Over the {ch['window']['days']} days to {ch['window']['end']}, the platform recorded {ch['observed']} "
             f"{ch['case_syndrome_label']} encounters in {ch['place']} against {ch['expected']:.0f} expected "
             f"({ch['ratio']:.1f} times the usual level; p {_fmt_p(ch['p_value_window'])}).")
    g = ch.get("growth") or {}
    rt = ch.get("rt") or {}
    if g.get("doubling_time_days"):
        s.append(f"Excess cases are doubling about every {g['doubling_time_days']} days"
                 + (f" (reproduction number {rt['rt']}, 95% interval {rt['rt_lo']}-{rt['rt_hi']})." if rt else "."))
    elif g.get("halving_time_days"):
        s.append(f"The excess is now declining (halving about every {g['halving_time_days']} days).")
    age = sorted(ch["demographics"]["age"], key=lambda a: -a["relative_risk"])
    top_age = [a for a in age if a["relative_risk"] >= 1.5 and a["cases"] >= 3][:2]
    if top_age:
        s.append("Most affected: " + " and ".join(f"ages {a['band']} ({a['relative_risk']:.1f} times their usual share)"
                                                  for a in top_age) + ".")
    insts = [i for i in ch["institutions"] if (i.get("relative_risk") or 0) >= 2 and i["cases"] >= 3][:2]
    if insts:
        s.append("Linked settings: " + "; ".join(f"{i['name']} ({i['cases']} cases, {i['attack_rate_per_1000']} per 1,000 enrolled)"
                                                 for i in insts) + ".")
    sev = ch["severity"]
    if sev["admission_ratio"] >= 1.5 and sev["admitted"] >= 3:
        s.append(f"{sev['admitted']} patients were admitted ({sev['admitted_pct']}% vs {sev['baseline_admitted_pct']}% "
                 f"normally), {sev['icu']} to intensive care, {sev['deaths']} died.")
    if hyps:
        h = hyps[0]
        alt = ", ".join(f"{x['name'].lower()} ({x['probability']:.0%})" for x in hyps[1:3] if x["probability"] >= 0.05)
        s.append(f"Most likely explanation: {h['name'].lower()} ({h['probability']:.0%})" + (f"; alternatives: {alt}." if alt else "."))
    s.append(f"Confidence that this is a real event worth investigating: {conf:.0%} ({lvl.lower()}).")
    return " ".join(s)


def key_findings(ch: dict, hyps: list[dict], signals: list[dict]) -> list[dict]:
    kf = []
    kf.append({"kind": "size", "text": f"{ch['observed']} cases vs {ch['expected']:.0f} expected in 14 days "
                                       f"({ch['excess']:.0f} excess, {ch['ratio']:.1f}x).",
               "evidence": ["epi_curve"]})
    if ch.get("first_elevated_day"):
        kf.append({"kind": "timing", "text": f"Counts first clearly exceeded the baseline on {ch['first_elevated_day']}.",
                   "evidence": ["epi_curve"]})
    g, rt = ch.get("growth") or {}, ch.get("rt") or {}
    if g.get("doubling_time_days"):
        kf.append({"kind": "growth", "text": f"Doubling time {g['doubling_time_days']} days (95% CI {g['doubling_ci'][0]}-"
                                             f"{g['doubling_ci'][1]})" + (f"; Rt {rt['rt']} ({rt['rt_lo']}-{rt['rt_hi']})." if rt else "."),
                   "evidence": ["growth", "rt"]})
    geo = [x for x in ch["geography"] if (x.get("relative_risk") or 0) >= 1.5][:3]
    if geo:
        kf.append({"kind": "place", "text": "Highest relative risk: " + ", ".join(
            f"{x['name']} ({x['relative_risk']}x, {x['rate_per_10k']} per 10,000)" for x in geo) + ".", "evidence": ["geography"]})
    insts = [i for i in ch["institutions"] if (i.get("relative_risk") or 0) >= 2][:3]
    if insts:
        kf.append({"kind": "setting", "text": "Settings: " + ", ".join(f"{i['name']} ({i['cases']} cases, {i['relative_risk']}x expected)"
                                                                       for i in insts) + ".", "evidence": ["institutions"]})
    age = [a for a in ch["demographics"]["age"] if a["relative_risk"] >= 1.5 and a["cases"] >= 3]
    if age:
        kf.append({"kind": "people", "text": "Over-represented ages: " + ", ".join(f"{a['band']} ({a['share']:.0%} of cases vs "
                                                                                    f"{a['baseline_share']:.0%} usually)"
                                                                                    for a in age[:3]) + ".", "evidence": ["demographics"]})
    for p in ch["labs"]:
        if p["tested"] >= 3:
            pos = ", ".join(f"{k} {v}" for k, v in p["positives"].items()) or "none positive"
            extra = ""
            if p["negative_for_all_pct"] is not None and p["panel"].startswith("Respiratory"):
                extra = f"; {p['negative_for_all_pct']:.0f}% negative for every tested virus (usually {p['baseline_negative_for_all_pct']:.0f}%)"
            kf.append({"kind": "laboratory", "text": f"{p['panel']}: {p['tested']} tested, {pos}{extra}.", "evidence": ["labs"]})
    sev = ch["severity"]
    kf.append({"kind": "severity", "text": f"{sev['admitted']} admitted ({sev['admitted_pct']}% vs {sev['baseline_admitted_pct']}% "
                                           f"baseline), {sev['icu']} intensive care, {sev['deaths']} deaths.", "evidence": ["severity"]})
    kf.append({"kind": "data", "text": f"Seen by {ch['n_sources_with_excess']} independent data source(s); "
                                       f"{ch['top_excess_source_share']:.0%} of the excess from the largest contributor.",
               "evidence": ["sources"]})
    shifts = (ch.get("coding") or {}).get("top_code_shifts") or []
    if shifts and shifts[0]["change"] >= 0.2:
        s0 = shifts[0]
        kf.append({"kind": "data", "text": f"Code {s0['code']} rose from {s0['baseline_share']:.0%} to {s0['case_share']:.0%} "
                                           "of diagnosis codes.", "evidence": ["coding"]})
    detectors = sorted({d for s in signals for d in s["detectors_fired"]})
    kf.append({"kind": "detection", "text": f"{len(signals)} signals from {len(detectors)} detection methods: "
                                            + ", ".join(d.replace('_', ' ') for d in detectors) + ".", "evidence": ["signals"]})
    return kf


def recommended_actions(ch: dict, hyps: list[dict], priority: str) -> list[str]:
    acts = []
    if priority == "P1":
        acts.append("Convene the outbreak response team within 24 hours and assign an investigation lead.")
    top = hyps[0] if hyps else None
    if top and top["id"] == "reporting_artefact" and top["probability"] >= 0.4:
        acts.append(f"Contact the data team at {ch['sources'][0]['name'] if ch['sources'] else 'the main source'} before any field response.")
    for i in ch["institutions"][:2]:
        if (i.get("relative_risk") or 0) >= 2:
            acts.append(f"Contact {i['name']} to check absenteeism and reinforce hygiene and exclusion guidance.")
    if top:
        acts.extend(top["tests"][:3])
    if ch["severity"]["admission_ratio"] >= 1.5:
        acts.append("Alert hospitals to expect more admissions and check bed and intensive-care capacity.")
    if ch["family"] == "RESPIRATORY" and top and top["id"] == "novel_respiratory":
        acts.append("Notify the national IHR focal point if an unexplained severe respiratory cluster is confirmed "
                    "(International Health Regulations, Annex 2).")
    return acts[:7]


def llm_rewrite(structured: dict) -> str | None:
    s = get_settings()
    if s.narrative_backend != "local_llm" or not GUARD.url_allowed(s.local_llm_url):
        return None
    try:
        import httpx
        prompt = ("Rewrite this outbreak briefing for a health minister in plain language, in at most 120 words. "
                  "Use only the facts given; do not add numbers.\n" + json.dumps(structured)[:6000])
        r = httpx.post(s.local_llm_url, json={"model": s.local_llm_model, "messages": [{"role": "user", "content": prompt}],
                                              "temperature": 0.2}, timeout=20)
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        return None
