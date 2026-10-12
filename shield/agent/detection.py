"""Stage 1 of the agent: run every detector over every syndrome x stratum and
the spatial and laboratory detectors, and return the signals that fire."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np
from scipy import stats

from ..detect import scan, temporal
from .data import FAMILIES, SYNDROME_FAMILY, SYNDROME_LABEL, AnalysisData

DETECT_SYNDROMES = ["ARI", "ILI", "RESP", "SEVERE_RESP", "GI", "FEVER", "RASH", "NEURO"]
STRATA = ["all", "age", "nb", "inst", "facility"]
SCAN_SYNDROMES = ["ARI", "SEVERE_RESP", "GI", "RASH", "NEURO", "FEVER"]


@dataclass
class Signal:
    id: str
    family: str
    syndrome: str
    stratum_type: str
    stratum_value: object
    label: str
    fired: list[str]
    results: list[dict]
    observed: float
    expected: float
    p_value: float
    strength: float
    window: tuple[int, int]
    case_ids: np.ndarray = field(default_factory=lambda: np.array([], int))
    pids: set = field(default_factory=set)
    kind: str = "temporal"     # temporal | spatial | lab | notifiable
    nbs: set = field(default_factory=set)

    @property
    def excess(self) -> float:
        return max(self.observed - self.expected, 0.0)

    def summary(self) -> dict:
        return {"id": self.id, "kind": self.kind, "syndrome": self.syndrome,
                "syndrome_label": SYNDROME_LABEL.get(self.syndrome, self.syndrome), "stratum_type": self.stratum_type,
                "stratum": self.label, "detectors_fired": self.fired, "observed": round(self.observed, 1),
                "expected": round(self.expected, 1), "p_value": float(f"{self.p_value:.3g}"),
                "strength": round(self.strength, 2), "results": self.results}


def _res_dict(r: temporal.DetectorResult) -> dict:
    d = {"detector": r.detector, "alarm": r.alarm, "p_value": float(f"{r.p_value:.3g}"), "score": round(r.score, 2),
         "observed": round(r.observed, 1), "expected": round(r.expected, 2), "threshold": round(float(r.upper), 2),
         "window_days": r.window_days}
    d.update({k: v for k, v in r.details.items() if k != "path"})
    return d


@dataclass
class DetectionOutput:
    signals: list[Signal]
    n_series: int
    nb_expected7: dict          # syndrome -> np.array per nb (sum of last 7 days expected)
    series_index: dict          # (syn, stype, value) -> {"expected": [...], "upper": [...]} for evaluation window
    timings: dict
    tested: list[dict]


def run_detection(data: AnalysisData, alpha: float = 0.01, fdr_q: float = 0.05, replicates: int = 999,
                  family_multiplier: dict | None = None, log=lambda *a: None) -> DetectionOutput:
    fm = family_multiplier or {}
    T = data.T
    t0 = time.time()
    candidates = []        # (syn, stype, value, label, reg, far, ears, cusum)
    nb_exp7: dict[str, np.ndarray] = {}
    series_index: dict = {}
    n_series = 0
    for syn in DETECT_SYNDROMES:
        fam = SYNDROME_FAMILY[syn]
        a = alpha * fm.get(fam, 1.0)
        mask = data.syndrome_mask(syn)
        for stype in STRATA:
            keys, values = data.strata_keys(stype)
            M = data.daily_matrix(mask, keys, len(values))
            if stype == "nb":
                nb_exp7[syn] = np.zeros(len(values))
            for i, v in enumerate(values):
                y = M[i]
                recent = y[T - 13:T + 1].sum()
                if y[max(0, T - 365):T + 1].sum() < 15 or recent < 3:
                    continue
                n_series += 1
                reg = temporal.regression_detector(y, data.t, data.dow, data.holiday, T, alpha=a, doy=data.doy, year=data.year, term=data.term)
                far = temporal.farrington_flexible(y, T, alpha=a)
                base_mean = y[T - 34:T - 6].mean()
                ear = temporal.ears(y, T) if base_mean >= 1.0 else {}
                cus = temporal.cusum(reg.z) if reg is not None else None
                if reg is not None:
                    series_index[(syn, stype, str(v))] = {"expected": reg.expected.round(2).tolist(),
                                                          "upper": reg.upper.tolist(), "phi": reg.phi}
                    if stype == "nb":
                        nb_exp7[syn][i] = reg.expected[-7:].sum()
                candidates.append((syn, stype, v, reg, far, ear, cus, y))
    t1 = time.time()
    # Benjamini-Hochberg false-discovery control across every series and both model-based tests
    p7 = np.array([c[3].result7.p_value if c[3] is not None else 1.0 for c in candidates])
    pf = np.array([c[4].p_value if c[4] is not None else 1.0 for c in candidates])
    sig_all = temporal.benjamini_hochberg(np.concatenate([p7, pf]), fdr_q)
    bh, bh_far = sig_all[:len(p7)], sig_all[len(p7):]
    signals: list[Signal] = []
    tested = []
    for k, (syn, stype, v, reg, far, ear, cus, y) in enumerate(candidates):
        results, fired = [], []
        if reg is not None:
            r7 = reg.result7
            r7.alarm = bool(r7.alarm and bh[k])
            results.append(_res_dict(r7))
            if r7.alarm:
                fired.append("regression_7d")
        if far is not None:
            far.alarm = bool(far.alarm and bh_far[k])
            results.append(_res_dict(far))
            if far.alarm:
                fired.append("farrington_flexible")
        for name in ("ears_c1", "ears_c2", "ears_c3"):
            if name in ear:
                results.append(_res_dict(ear[name]))
        if cus is not None:
            results.append(_res_dict(cus))
        ears_c3 = "ears_c3" in ear and ear["ears_c3"].alarm
        cus_alarm = cus is not None and cus.alarm
        if ears_c3:
            fired.append("ears_c3")
        if cus_alarm:
            fired.append("cusum")
        # EARS + CUSUM together catch sharp onsets the weekly models smooth over, but only with
        # supporting evidence from the regression baseline.
        sharp = ears_c3 and cus_alarm and reg is not None and reg.result7.p_value < 0.05
        core = any(f in fired for f in ("regression_7d", "farrington_flexible")) or sharp
        obs7 = float(y[T - 6:T + 1].sum())
        exp7 = float(reg.result7.expected) if reg is not None else (float(far.expected) if far else 0.0)
        pmin = min([r["p_value"] for r in results if r["detector"] in ("regression_7d", "farrington_flexible")] or [1.0])
        tested.append({"syndrome": syn, "stratum_type": stype, "stratum": data.stratum_label(stype, v), "observed_7d": obs7,
                       "expected_7d": round(exp7, 1), "p_value": pmin, "fired": fired if core else []})
        if not core:
            continue
        strength = min(-np.log10(max(pmin, 1e-12)), 12.0)
        d0 = T - 6
        sig = Signal(f"{syn}:{stype}:{v}", SYNDROME_FAMILY[syn], syn, stype, v, data.stratum_label(stype, v), fired,
                     results, obs7, exp7, pmin, strength, (d0, T), data.case_ids(syn, stype, v, d0, T))
        signals.append(sig)
    t2 = time.time()
    # mortality: weekly Farrington on all deaths (city-wide and 65+)
    for label, m in (("all", np.ones(len(data.a_id), bool)), ("65+", data.a_age >= 6)):
        y = data.daily_matrix(data.syndrome_mask("DEATHS") & m, np.zeros(len(data.a_id), int), 1)[0]
        far = temporal.farrington_flexible(y, T, alpha=alpha * fm.get("MORTALITY", 1.0))
        n_series += 1
        if far is not None and far.alarm:
            ids = data.a_id[data.syndrome_mask("DEATHS") & m & (data.a_day >= T - 6)]
            signals.append(Signal(f"DEATHS:age:{label}", "MORTALITY", "DEATHS", "all" if label == "all" else "age_group", label,
                                  f"Deaths ({label})", ["farrington_flexible"], [_res_dict(far)], far.observed,
                                  far.expected, far.p_value, min(-np.log10(max(far.p_value, 1e-12)), 12), (T - 6, T), ids))
    # spatial scans
    zones = scan.circular_zones(data.ref.centroids, 0.5, data.ref.pop_by_nb())
    rng = np.random.default_rng(data.T)
    for syn in SCAN_SYNDROMES:
        fam = SYNDROME_FAMILY[syn]
        p_stp = 0.005 * fm.get(fam, 1.0)
        p_ebp = 0.005 * fm.get(fam, 1.0)
        mask = data.syndrome_mask(syn) & (data.a_nb >= 0) & (data.a_day > T - 56)
        stp = scan.space_time_permutation_scan(data.a_nb[mask], (data.a_day[mask] - (T - 55)), data.ref.n_nb, 56,
                                               zones, 14, replicates, rng)
        found = []
        if stp is not None and stp.p_value <= p_stp:
            found.append(stp)
        if syn in nb_exp7:
            obs = data.daily_matrix(data.syndrome_mask(syn) & (data.a_day > T - 7), data.a_nb,
                                    data.ref.n_nb)[:, T - 6:T + 1].sum(1)
            exp = nb_exp7[syn]
            have = exp > 0
            if have.sum() >= 3:
                exp = np.where(have, exp, obs + 0.5)
                ebp = scan.expectation_based_poisson_scan(obs, exp, zones, replicates, rng)
                if ebp is not None and ebp.p_value <= p_ebp:
                    found.append(ebp)
                    # secondary, non-overlapping cluster (SaTScan-style)
                    rest = [z for z in zones if not set(z) & set(ebp.zones)]
                    if rest:
                        ebp2 = scan.expectation_based_poisson_scan(obs, exp, rest, replicates, rng)
                        if ebp2 is not None and ebp2.p_value <= p_ebp:
                            ebp2.details["secondary"] = True
                            found.append(ebp2)
        for sr in found:
            d0 = T - sr.window_days + 1
            ids = data.case_ids(syn, "zone", tuple(sr.zones), d0, T)
            res = {"detector": sr.method, "alarm": True, "p_value": sr.p_value, "observed": sr.observed,
                   "expected": round(sr.expected, 2), "relative_risk": round(sr.relative_risk, 2),
                   "llr": round(sr.llr, 2), "window_days": sr.window_days, "replicates": sr.replicates,
                   "zone": [data.ref.nb_names[z] for z in sr.zones], "secondary": sr.details.get("secondary", False)}
            signals.append(Signal(f"{syn}:zone:{'-'.join(map(str, sr.zones))}:{sr.method}", fam, syn, "zone",
                                  tuple(sr.zones), data.stratum_label("zone", sr.zones), [sr.method], [res], sr.observed,
                                  sr.expected, sr.p_value, min(-np.log10(sr.p_value), 12) + 0.5, (d0, T), ids,
                                  kind="spatial"))
    t3 = time.time()
    signals.extend(lab_signals(data, alpha))
    signals.extend(notifiable_signals(data))
    t4 = time.time()
    log(f"detection: {n_series} series, {len(signals)} signals")
    return DetectionOutput(signals, n_series, nb_exp7, series_index,
                           {"temporal_s": round(t2 - t0, 2), "spatial_s": round(t3 - t2, 2), "lab_s": round(t4 - t3, 2)},
                           tested)


def lab_signals(data: AnalysisData, alpha: float) -> list[Signal]:
    T = data.T
    labs = data.labs
    out: list[Signal] = []
    if labs.empty:
        return out
    n_days = T + 1
    # analyte positives
    for (panel, analyte), grp in labs[labs["result"].to_numpy(dtype=object) == "positive"].groupby(["panel", "analyte"]):
        y = np.bincount(grp["day"].clip(0, T).values, minlength=n_days).astype(float)[:n_days]
        if y[T - 6:T + 1].sum() < 3:
            continue
        reg = temporal.regression_detector(y, data.t, data.dow, data.holiday, T, alpha=alpha, min_history=56,
                                           doy=data.doy, year=data.year, term=data.term)
        base_rate = y[max(0, T - 379):T - 13].mean()
        r7 = reg.result7 if (reg is not None and base_rate >= 0.5) else temporal.rare_event_test(y, T, alpha)
        if r7.alarm:
            recent = grp[grp["day"] >= T - 6]
            pids = set(recent["pid"])
            fam = _lab_family(panel)
            out.append(Signal(f"LAB:{analyte}", fam, "LAB", "lab", analyte, f"Positive {analyte} tests",
                              ["lab_positives"], [_res_dict(r7) | {"detector": "lab_positives"}], r7.observed,
                              r7.expected, r7.p_value, min(-np.log10(max(r7.p_value, 1e-12)), 12), (T - 6, T),
                              _case_ids_for_pids(data, pids, fam), pids=pids, kind="lab",
                              nbs={int(n) for n in recent["nb"].dropna().value_counts().index[:3]}))
    # unexplained respiratory illness: panels negative for every analyte
    rp = labs[labs["panel"].str.startswith("Respiratory")]
    if not rp.empty:
        rp = rp.assign(pos=(rp["result"].to_numpy(dtype=object) == "positive").astype(int))
        per_order = rp.groupby("order_id").agg(day=("day", "first"), pid=("pid", "first"), npos=("pos", "sum"))
        per_order["neg"] = per_order["npos"] == 0
        tot = np.bincount(per_order["day"].clip(0, T).values, minlength=n_days)[:n_days].astype(float)
        neg = np.bincount(per_order.loc[per_order["neg"], "day"].clip(0, T).values, minlength=n_days)[:n_days].astype(float)
        reg = temporal.regression_detector(neg, data.t, data.dow, data.holiday, T, alpha=alpha, min_history=56,
                                           doy=data.doy, year=data.year, term=data.term)
        n7, t7 = neg[T - 6:T + 1].sum(), tot[T - 6:T + 1].sum()
        nb_, tb_ = neg[T - 34:T - 6].sum(), tot[T - 34:T - 6].sum()
        if reg is not None and t7 >= 10 and tb_ > 0:
            p_frac = float(stats.binomtest(int(n7), int(t7), min(max(nb_ / tb_, 1e-3), 0.999), "greater").pvalue)
            if reg.result7.alarm and p_frac < 0.01:
                pids = set(per_order.loc[per_order["neg"] & (per_order["day"] >= T - 6), "pid"])
                res = _res_dict(reg.result7) | {"detector": "lab_unexplained_respiratory",
                                                "negative_share_7d": round(n7 / t7, 3),
                                                "negative_share_baseline": round(nb_ / tb_, 3), "binomial_p": p_frac}
                out.append(Signal("LAB:unexplained_respiratory", "RESPIRATORY", "LAB", "lab", "unexplained respiratory",
                                  "Respiratory panels negative for all tested pathogens", ["lab_unexplained_respiratory"],
                                  [res], n7, reg.result7.expected, min(reg.result7.p_value, p_frac),
                                  min(-np.log10(max(min(reg.result7.p_value, p_frac), 1e-12)), 12), (T - 6, T),
                                  pids=pids, kind="lab"))
    return out


FAMILY_SYNDROMES = {"RESPIRATORY": ("ILI", "RESP"), "GI": ("GI",), "RASH": ("RASH", "FEVER"), "NEURO": ("NEURO",),
                    "FEVER": ("FEVER",)}


def _case_ids_for_pids(data: AnalysisData, pids: set, family: str, days: int = 14) -> np.ndarray:
    syn = np.zeros(len(data.a_id), bool)
    for s in FAMILY_SYNDROMES.get(family, ()):
        syn |= data.a_syn[s]
    m = syn & data.a_visit & (data.a_day > data.T - days) & data.ev["pid"].isin(pids).to_numpy()
    return data.a_id[m]


# Conditions where a single coded case (or a small number) is itself an alert, as in national notifiable-disease
# lists and the IHR Annex 2 decision instrument. (ICD-10 prefix, syndrome, family, minimum cases in 14 days)
NOTIFIABLE = {
    "Measles": ("B05", "RASH", "RASH", 1),
    "Invasive meningococcal disease": ("A39", "NEURO", "NEURO", 1),
    "Legionnaires' disease": ("A48.1", "SEVERE_RESP", "RESPIRATORY", 2),
    "Cholera": ("A00", "GI", "GI", 1),
    "Viral haemorrhagic fever": ("A98", "FEVER", "FEVER", 1),
    "Novel influenza A (e.g. H5N1)": ("J09", "ILI", "RESPIRATORY", 1),
    "Diphtheria": ("A36", "RESP", "RESPIRATORY", 1),
    "Poliomyelitis": ("A80", "NEURO", "NEURO", 1),
    "Plague": ("A20", "FEVER", "FEVER", 1),
    "Anthrax": ("A22", "FEVER", "FEVER", 1),
}


def notifiable_signals(data: AnalysisData) -> list[Signal]:
    T = data.T
    d0 = data.date_of(T - 13)
    end = f"{data.as_of.isoformat()}T23:59:59"
    rows = data.con.execute("SELECT id, icd10, neighbourhood_id, patient_pid, event_date FROM events WHERE is_primary=1 "
                            "AND event_date >= ? AND event_date <= ? AND received_at <= ? AND icd10 != ''",
                            (d0, data.as_of.isoformat(), end)).fetchall()
    out = []
    for name, (prefix, syn, fam, k) in NOTIFIABLE.items():
        hits = [r for r in rows if any(c.startswith(prefix) for c in r["icd10"].split("|"))]
        if len(hits) < k:
            continue
        pids = {r["patient_pid"] for r in hits}
        nbs = [r["neighbourhood_id"] for r in hits if r["neighbourhood_id"] is not None]
        top = [n for n, _ in __import__("collections").Counter(nbs).most_common(3)]
        res = {"detector": "notifiable_condition", "alarm": True, "condition": name, "icd10_prefix": prefix,
               "observed": len(hits), "expected": 0, "threshold": k, "p_value": 0.0, "window_days": 14,
               "first_case": min(r["event_date"] for r in hits)}
        out.append(Signal(f"NOTIFIABLE:{prefix}", fam, syn, "zone", tuple(sorted(top)) or (0,),
                          f"Notifiable condition: {name}", ["notifiable_condition"], [res], float(len(hits)), 0.0, 1e-6,
                          10.0, (T - 13, T), _case_ids_for_pids(data, pids, fam) if fam != "RESPIRATORY" or syn != "SEVERE_RESP"
                          else np.array([r["id"] for r in hits]), pids=pids, kind="notifiable", nbs=set(top)))
    return out


def _lab_family(panel: str) -> str:
    if panel.startswith("Respiratory") or panel.startswith("Legionella"):
        return "RESPIRATORY"
    if panel.startswith("Gastro"):
        return "GI"
    if panel.startswith("Measles"):
        return "RASH"
    return "OTHER"
