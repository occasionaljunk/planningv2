"""Stage 3: characterise each candidate event.

Builds the case definition (syndrome x place x time), then compares the cases
with the same syndrome and place over the preceding eight weeks to describe
*what is different now*: size and excess, epidemic curve against the model
baseline, growth and reproduction number, age/sex profile, geography,
schools and universities, severity, laboratory results, contributing data
sources and changes in diagnosis coding.
"""
from __future__ import annotations

from collections import Counter

import numpy as np
import pandas as pd
from scipy import stats

from ..detect import epi, temporal
from ..nlp.syndromes import classify
from ..security.pseudonym import AGE_BANDS
from .data import FAMILY_LABEL, SYNDROME_LABEL, AnalysisData
from .fusion import CandidateEvent

FAMILY_SI = {"RESPIRATORY": (5.0, 2.5), "GI": (3.6, 2.0), "RASH": (11.7, 2.0), "NEURO": (5.0, 2.0), "FEVER": (4.0, 2.0)}
WINDOW = 14
BASELINE = 56


def _case_syndrome(e: CandidateEvent) -> str:
    sigs = [s for s in e.signals if s.syndrome not in ("LAB",)]
    if not sigs:
        return {"RESPIRATORY": "ARI"}.get(e.family, e.family)
    locus = [s for s in sigs if s.stratum_type in ("nb", "inst", "zone")] or sigs
    best = max(locus, key=lambda s: s.strength)
    return best.syndrome


def place_mask(data: AnalysisData, e: CandidateEvent) -> tuple[np.ndarray, str]:
    if e.kind == "facility" and e.facilities:
        idx = [data.facilities.index(f) for f in e.facilities if f in data.facilities]
        return np.isin(data.a_fac, idx), " / ".join(data.ref.facility_name(f) for f in sorted(e.facilities))
    if e.nbs or e.insts:
        m = np.zeros(len(data.a_id), bool)
        if e.nbs:
            m |= np.isin(data.a_nb, list(e.nbs))
        if e.insts:
            m |= np.isin(data.a_inst, [data.institutions.index(i) for i in e.insts])
        parts = [data.ref.institution_name(i) for i in sorted(e.insts)] + [data.ref.nb_names[n] for n in sorted(e.nbs)]
        return m, ", ".join(parts)
    age_sigs = [s for s in e.signals if s.stratum_type == "age"]
    if age_sigs and not any(s.stratum_type == "all" for s in e.signals):
        bands = sorted({int(s.stratum_value) for s in age_sigs})
        return np.isin(data.a_age, bands), "City-wide, " + ", ".join(f"age {AGE_BANDS[b]}" for b in bands)
    return np.ones(len(data.a_id), bool), "City-wide"


def characterize(e: CandidateEvent, data: AnalysisData, det, exclude_nbs: set | None = None) -> dict:
    T = data.T
    syn = _case_syndrome(e)
    smask = data.syndrome_mask(syn) if syn != "DEATHS" else data.syndrome_mask("DEATHS")
    pmask, place_label = place_mask(data, e)
    base_mask = smask & pmask
    in_win = (data.a_day >= T - WINDOW + 1) & (data.a_day <= T)
    in_base = (data.a_day >= T - WINDOW - BASELINE + 1) & (data.a_day <= T - WINDOW)
    case_rows = np.nonzero(base_mask & in_win)[0]
    base_rows = np.nonzero(base_mask & in_base)[0]
    ev = data.ev
    cases = ev.iloc[case_rows]
    base = ev.iloc[base_rows]
    # epidemic curve against the model baseline for the exact case definition
    y = np.bincount(data.a_day[base_mask], minlength=T + 1)[:T + 1].astype(float)
    reg = temporal.regression_detector(y, data.t, data.dow, data.holiday, T, alpha=0.01, doy=data.doy, year=data.year, term=data.term)
    show = 56
    d0 = T - show + 1
    if reg is not None:
        expected_full = reg.mu_all[d0:T + 1]
        upper = np.concatenate([np.full(show - WINDOW, np.nan), reg.upper])
        expected_win = reg.expected
        phi = reg.phi
    else:
        mean = y[T - 70:T - 13].mean() if T > 70 else y.mean()
        expected_full = np.full(show, mean)
        upper = np.full(show, np.nan)
        expected_win = np.full(WINDOW, mean)
        phi = 1.0
    obs_win = y[T - WINDOW + 1:T + 1]
    exp_total = float(expected_win.sum())
    obs_total = float(obs_win.sum())
    excess_daily = np.clip(obs_win - expected_win, 0, None)
    excess = max(obs_total - exp_total, 0.0)
    # nowcast: correct the last days for records still in transit
    comp = data.delay_completeness()
    src_share = cases["source_id"].value_counts(normalize=True)
    nowcast = []
    for k in range(7):
        lag = k
        f = sum(src_share.get(sid, 0) * comp.get(sid, np.ones(31))[min(lag, 30)] for sid in src_share.index) or 1.0
        nowcast.append(round(float(y[T - k] / max(f, 0.05)), 1))
    nowcast = nowcast[::-1]
    # growth and reproduction number on excess incidence (56-day curve)
    full_excess = np.clip(y[d0:T + 1] - expected_full, 0, None)
    si = FAMILY_SI.get(e.family, (5.0, 2.5))
    rt = epi.cori_rt(full_excess, *si) if e.family != "MORTALITY" else None
    gr = epi.growth(full_excess, 14)
    # demographics
    age_case = np.bincount(cases["age_band"].clip(0, 7), minlength=8)
    age_base = np.bincount(base["age_band"].clip(0, 7), minlength=8)
    base_share = (age_base + 0.5) / (age_base.sum() + 4)
    exp_age = base_share * len(cases)
    age_rows = []
    for b in range(8):
        age_rows.append({"band": AGE_BANDS[b], "cases": int(age_case[b]), "share": round(age_case[b] / max(len(cases), 1), 3),
                         "baseline_share": round(float(base_share[b]), 3),
                         "relative_risk": round(float((age_case[b] + 0.5) / (exp_age[b] + 0.5)), 2)})
    chi_p = None
    if len(cases) >= 10 and age_base.sum() >= 10:
        keep = (age_case + age_base) > 0
        chi_p = float(stats.chi2_contingency(np.vstack([age_case[keep], age_base[keep]]))[1]) if keep.sum() > 1 else None
    male_case = float((cases["sex"] == "M").mean()) if len(cases) else 0.0
    male_base = float((base["sex"] == "M").mean()) if len(base) else 0.5
    # geography and institutions (cases vs model expectation where available)
    nb_rows = []
    pop_nb = data.ref.pop_by_nb()
    nb_case = np.bincount(cases["nb"].clip(-1, 99) + 1, minlength=data.ref.n_nb + 1)[1:]
    for n in range(data.ref.n_nb):
        if nb_case[n] == 0:
            continue
        key = (syn, "nb", str(n))
        in_area = e.kind != "locus" or n in e.nbs
        exp14 = float(np.sum(det.series_index[key]["expected"])) if (key in det.series_index and in_area) else None
        nb_rows.append({"id": n, "name": data.ref.nb_names[n], "cases": int(nb_case[n]), "in_event_area": bool(in_area),
                        "expected": round(exp14, 1) if exp14 is not None else None,
                        "relative_risk": round((nb_case[n] + 0.5) / (exp14 + 0.5), 2) if exp14 is not None else None,
                        "rate_per_10k": round(nb_case[n] / max(pop_nb[n], 1) * 1e4, 1)})
    nb_rows.sort(key=lambda r: (not r["in_event_area"], -r["cases"]))
    inst_rows = []
    inst_counts = Counter(cases["inst"].dropna())
    inst_base = Counter(base["inst"].dropna())
    for iid, c in inst_counts.most_common(8):
        key = (syn, "inst", iid)
        linked = e.kind != "locus" or iid in e.insts or not e.insts
        exp14 = float(np.sum(det.series_index[key]["expected"])) if (key in det.series_index and linked) else None
        row = data.ref.institutions.loc[data.ref.institutions["id"] == iid].iloc[0]
        inst_rows.append({"id": iid, "name": row["name"], "kind": row["kind"], "cases": int(c),
                          "expected": round(exp14, 1) if exp14 is not None else None,
                          "relative_risk": round((c + 0.5) / (exp14 + 0.5), 2) if exp14 is not None else None,
                          "attack_rate_per_1000": round(c / max(row["enrolment"], 1) * 1000, 1)})
    setting_share = {}
    for kind in ("university", "primary_school", "secondary_school"):
        ids = set(data.ref.institutions.loc[data.ref.institutions["kind"] == kind, "id"])
        sc = float(cases["inst"].isin(ids).mean()) if len(cases) else 0.0
        sb = float(base["inst"].isin(ids).mean()) if len(base) else 0.0
        setting_share[kind] = {"cases": round(sc, 3), "baseline": round(sb, 3)}
    # severity
    def rate(df, col):
        return float(df[col].mean()) if len(df) else 0.0
    adm_c, adm_b = rate(cases, "admitted"), rate(base, "admitted")
    adm_p = float(stats.binomtest(int(cases["admitted"].sum()), max(len(cases), 1), min(max(adm_b, 1e-3), 0.999),
                                  "greater").pvalue) if len(cases) else 1.0
    severity = {"admitted": int(cases["admitted"].sum()), "admitted_pct": round(100 * adm_c, 1),
                "baseline_admitted_pct": round(100 * adm_b, 1), "icu": int(cases["icu"].sum()),
                "deaths": int(cases["died"].sum()), "baseline_icu_pct": round(100 * rate(base, "icu"), 2),
                "admission_ratio": round((adm_c + 1e-3) / (adm_b + 1e-3), 2), "p_value_admissions": adm_p}
    # laboratory results for case-patients (tests collected from 2 days before the window)
    labs = data.labs
    pids = set(cases["pid"])
    lab_c = labs[labs["pid"].isin(pids) & (labs["day"] >= T - WINDOW - 1)]
    lab_window = labs[(labs["day"] >= T - WINDOW - BASELINE) & (labs["day"] <= T - WINDOW)]
    panels = []
    family_panels = {"RESPIRATORY": ("Respiratory", "Legionella"), "GI": ("Gastro",), "RASH": ("Measles",)}
    keep = family_panels.get(e.family)
    if keep:
        lab_c = lab_c[lab_c["panel"].str.startswith(keep)]
    for panel, grp in lab_c.groupby("panel"):
        orders = grp.groupby("order_id")["result"].apply(lambda s: (s == "positive").any())
        pos = grp[grp["result"] == "positive"]["analyte"].value_counts().to_dict()
        bgrp = lab_window[lab_window["panel"] == panel]
        b_orders = bgrp.groupby("order_id")["result"].apply(lambda s: (s == "positive").any())
        b_pos = bgrp[bgrp["result"] == "positive"]["analyte"].value_counts()
        n_b = max(len(b_orders), 1)
        neg_share = 1 - float(orders.mean()) if len(orders) else None
        neg_base = 1 - float(b_orders.mean()) if len(b_orders) else None
        p_neg = None
        if neg_share is not None and neg_base is not None and len(orders) >= 5:
            p_neg = float(stats.binomtest(int(round(neg_share * len(orders))), len(orders), min(max(neg_base, 1e-3), 0.999),
                                          "greater").pvalue)
        panels.append({"panel": panel, "tested": int(len(orders)), "positives": {k: int(v) for k, v in pos.items()},
                       "positivity": {k: round(v / max(len(orders), 1), 3) for k, v in pos.items()},
                       "baseline_positivity": {k: round(float(v) / n_b, 3) for k, v in b_pos.items()},
                       "negative_for_all_pct": round(100 * neg_share, 1) if neg_share is not None else None,
                       "baseline_negative_for_all_pct": round(100 * neg_base, 1) if neg_base is not None else None,
                       "p_value_negative_excess": p_neg})
    # sources: who sees the excess?
    src_rows = []
    sc = cases["source_id"].value_counts()
    sb = base["source_id"].value_counts(normalize=True)
    excess_src = {}
    for sid, c in sc.items():
        exp_s = float(sb.get(sid, 0) * len(cases) * (exp_total / max(obs_total, 1)))
        excess_src[sid] = c - exp_s
        src_rows.append({"source_id": sid, "name": data.ref.source_name(sid), "cases": int(c),
                         "share": round(c / max(len(cases), 1), 3), "baseline_share": round(float(sb.get(sid, 0)), 3),
                         "excess": round(c - exp_s, 1)})
    src_rows.sort(key=lambda r: -r["cases"])
    # A rise seen at one facility is checked against *other* institutions serving the same residents
    # over the last 7 days (neighbourhoods already covered by another event of this family are excluded).
    corroboration = None
    if e.kind == "facility" and len(cases):
        nb_counts = cases.loc[cases["nb"] >= 0, "nb"].value_counts()
        top_nbs = [int(n) for n in nb_counts.index[:4] if int(n) not in (exclude_nbs or set())]
        if top_nbs:
            other = smask & np.isin(data.a_nb, top_nbs) & ~pmask
            y2 = np.bincount(data.a_day[other], minlength=T + 1)[:T + 1].astype(float)
            reg2 = temporal.regression_detector(y2, data.t, data.dow, data.holiday, T, alpha=0.01, doy=data.doy, year=data.year, term=data.term)
            if reg2 is not None:
                o2, e2 = float(y2[T - 6:T + 1].sum()), float(reg2.expected[-7:].sum())
                p2 = float(temporal.nb_sf(o2, e2, reg2.phi))
                corroboration = {"neighbourhoods": [data.ref.nb_names[n] for n in top_nbs], "observed_7d": int(o2),
                                 "expected_7d": round(e2, 1), "ratio": round((o2 + 0.5) / (e2 + 0.5), 2), "p_value": p2,
                                 "corroborated": bool(p2 < 0.01 and o2 > 1.2 * e2)}
                if corroboration["corroborated"]:
                    excess_src["OTHER_INSTITUTIONS"] = o2 - e2
    pos_ex = {k: v for k, v in excess_src.items() if v > 0}
    top_excess_share = (max(pos_ex.values()) / sum(pos_ex.values())) if pos_ex else 0.0
    top_excess_source = max(pos_ex, key=pos_ex.get) if pos_ex else None
    hhi = float(sum((r["share"]) ** 2 for r in src_rows))
    # a source corroborates only if its own excess is clearly beyond noise (>= 3 and >= 2 standard deviations)
    exp_by_src = {sid: float(sb.get(sid, 0) * len(cases) * (exp_total / max(obs_total, 1))) for sid in sc.index}
    n_sources_up = sum(1 for k, v in excess_src.items() if v >= max(3.0, 2 * np.sqrt(max(exp_by_src.get(k, 0.0), 1.0))))
    if corroboration and corroboration["corroborated"]:
        n_sources_up = max(n_sources_up, 2)
    # coding: diagnosis-code mix and agreement between free text and codes
    coding = _coding_shift(data, cases["id"].tolist(), base["id"].tolist(), syn)
    curve = {"dates": [data.date_of(d) for d in range(d0, T + 1)], "observed": y[d0:T + 1].astype(int).tolist(),
             "expected": np.round(expected_full, 2).tolist(),
             "upper": [None if np.isnan(u) else float(u) for u in upper], "nowcast_last7": nowcast}
    first_alarm_day = None
    above = obs_win > np.maximum(expected_win * 1.5, expected_win + 2)
    if above.any():
        first_alarm_day = data.date_of(T - WINDOW + 1 + int(np.argmax(above)))
    onset_concentration = 0.0
    if obs_total > 0:
        top3 = np.sort(excess_daily)[-3:].sum()
        onset_concentration = float(top3 / max(excess_daily.sum(), 1e-9))
    return {
        "case_syndrome": syn, "case_syndrome_label": SYNDROME_LABEL.get(syn, syn), "family": e.family,
        "family_label": FAMILY_LABEL.get(e.family, e.family), "place": place_label, "kind": e.kind,
        "window": {"start": data.date_of(T - WINDOW + 1), "end": data.date_of(T), "days": WINDOW},
        "observed": int(obs_total), "expected": round(exp_total, 1), "excess": round(excess, 1),
        "ratio": round((obs_total + 0.5) / (exp_total + 0.5), 2), "overdispersion": round(float(phi), 2),
        "p_value_window": float(temporal.nb_sf(obs_total, exp_total, phi)) if exp_total > 0 else 0.0,
        "first_elevated_day": first_alarm_day, "onset_concentration": round(onset_concentration, 2),
        "epi_curve": curve, "rt": rt, "growth": gr,
        "demographics": {"age": age_rows, "age_chi2_p": chi_p, "male_share": round(male_case, 3),
                         "baseline_male_share": round(male_base, 3)},
        "geography": nb_rows, "institutions": inst_rows, "settings": setting_share, "severity": severity,
        "labs": panels, "sources": src_rows, "source_hhi": round(hhi, 3), "top_excess_source": top_excess_source,
        "top_excess_source_share": round(float(top_excess_share), 3), "n_sources_with_excess": n_sources_up,
        "notifiable": [r["condition"] for x in e.signals if x.kind == "notifiable" for r in x.results],
        "other_source_check": corroboration, "coding": coding, "case_event_ids": cases["id"].astype(int).tolist(), "case_pids": list(pids),
        "baseline_cases": int(len(base)),
    }


def _coding_shift(data: AnalysisData, case_ids: list[int], base_ids: list[int], syn: str) -> dict:
    con = data.con

    def fetch(ids):
        if not ids:
            return pd.DataFrame(columns=["id", "icd10", "chief_complaint", "admitted"])
        rows = []
        for i in range(0, len(ids), 900):
            chunk = ids[i:i + 900]
            rows += con.execute(f"SELECT id, icd10, chief_complaint, admitted FROM events WHERE id IN "
                                f"({','.join('?' * len(chunk))})", chunk).fetchall()
        return pd.DataFrame([dict(r) for r in rows])

    rng = np.random.default_rng(0)
    bsample = list(rng.choice(base_ids, size=min(len(base_ids), 3000), replace=False)) if base_ids else []
    c, b = fetch(case_ids), fetch([int(x) for x in bsample])

    def mix(df):
        codes = Counter()
        for x in df["icd10"].fillna(""):
            for code in [k for k in x.split("|") if k]:
                codes[code] += 1
        tot = max(sum(codes.values()), 1)
        return {k: v / tot for k, v in codes.items()}

    def disagreement(df):
        if df.empty:
            return None
        has_text = df["chief_complaint"].fillna("").str.len() > 0
        sub = df[has_text]
        if sub.empty:
            return None
        text_only = [classify(cc, "", False)[0] for cc in sub["chief_complaint"]]
        fam = {"ARI": {"ILI", "RESP"}, "SEVERE_RESP": {"ILI", "RESP"}}.get(syn, {syn})
        return float(np.mean([t not in fam and t != "FEVER" for t in text_only]))

    mc, mb = mix(c), mix(b)
    shifts = sorted(({"code": k, "case_share": round(v, 3), "baseline_share": round(mb.get(k, 0.0), 3),
                      "change": round(v - mb.get(k, 0.0), 3)} for k, v in mc.items()), key=lambda r: -r["change"])[:6]
    top_cc = Counter(c["chief_complaint"].fillna("").str.lower().str.strip()).most_common(6) if not c.empty else []
    return {"top_code_shifts": shifts, "text_code_disagreement": disagreement(c),
            "baseline_text_code_disagreement": disagreement(b),
            "top_chief_complaints": [{"text": t or "(no free text)", "count": n} for t, n in top_cc]}
