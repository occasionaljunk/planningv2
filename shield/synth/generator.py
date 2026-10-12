"""Synthetic but realistic health-system data for the City of Riverton.

What is generated
-----------------
* A population register (~203,000 people) with age, sex, home postcode,
  health-insurance status, life-insurance status and school/university
  enrolment.
* Three years of encounters at hospitals, clinics, urgent care and
  university health centres, driven by published-style visit rates with:
  seasonal influenza waves of different timing and severity each winter, RSV
  in infants, endemic SARS-CoV-2 waves, winter norovirus, summer enteric
  infections, back-to-school colds, day-of-week and holiday effects, students
  leaving the city in summer, over-dispersion from shared daily shocks,
  incomplete coding, abbreviations and typos.
* Laboratory orders and results whose positivity follows the circulating
  pathogens; insurer claims that duplicate most encounters with realistic lags;
  deaths from age-specific mortality, partly visible only via life insurers.
* Outbreak scenarios with ground truth (see ``scenarios.py``).

The agent never sees ground truth: it only receives what the source systems
would send, through the same connectors a real deployment uses.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from . import clinical as C
from .city import (AGE_RANGES, FACILITIES, FACILITY_INDEX, INSTITUTIONS, NEIGHBOURHOODS, age_distribution)

SYN = C.SYNDROMES
NB_N = len(NEIGHBOURHOODS)
AB_N = 8
G_N = NB_N * AB_N
EPOCH = date(1970, 1, 1)


@dataclass
class SimConfig:
    start: date = date(2023, 9, 1)
    end: date = date(2026, 10, 24)
    seed: int = 20261010
    reference_date: date = date(2026, 10, 1)
    scenarios: tuple = ("measles_2024", "novel_resp_2026", "noro_school_2026", "legionella_2026", "coding_artefact_2026")


@dataclass
class World:
    config: SimConfig
    persons: pd.DataFrame
    encounters: pd.DataFrame
    labs: pd.DataFrame
    claims: pd.DataFrame
    death_claims: pd.DataFrame
    enrolment: pd.DataFrame
    truth: pd.DataFrame
    outbreaks: list = field(default_factory=list)


# --------------------------------------------------------------------------- calendar
def gauss(x, mu, sd):
    return np.exp(-0.5 * ((x - mu) / sd) ** 2)


class Calendar:
    def __init__(self, start: date, end: date):
        self.start, self.end = start, end
        self.days = pd.date_range(start, end, freq="D")
        self.n = len(self.days)
        self.ordinal = np.array([d.toordinal() for d in self.days.date])
        self.doy = self.days.dayofyear.values
        self.dow = self.days.dayofweek.values
        md = list(zip(self.days.month, self.days.day))
        self.holiday = np.array([(m, d) in {(1, 1), (5, 1), (12, 25), (12, 26), (8, 15)} for m, d in md])
        years = range(start.year - 1, end.year + 2)

        def yearly(month, day, sd, height=1.0):
            out = np.zeros(self.n)
            for y in years:
                out += height * gauss(self.ordinal, date(y, month, day).toordinal(), sd)
            return out

        flu_seasons = [(date(2022, 12, 20), 3.0, 20), (date(2023, 12, 28), 3.2, 20), (date(2025, 1, 28), 4.6, 22),
                       (date(2025, 12, 22), 3.5, 18), (date(2027, 1, 10), 3.5, 20)]
        self.flu = np.zeros(self.n)
        for pk, h, sd in flu_seasons:
            self.flu += h * gauss(self.ordinal, pk.toordinal(), sd)
        self.flu_late = np.zeros(self.n)
        for pk, h, sd in flu_seasons:
            self.flu_late += 0.35 * h * gauss(self.ordinal, pk.toordinal() + 28, sd)
        self.rsv = yearly(12, 10, 25, 1.0)
        self.covid = yearly(8, 15, 25, 0.6) + yearly(1, 5, 25, 0.4)
        self.winter = 0.5 * (1 + np.cos(2 * np.pi * (self.doy - 15) / 365.25))
        self.summer_gi = yearly(7, 20, 30, 1.0)
        self.bts = yearly(9, 18, 12, 1.0)
        self.entero = yearly(8, 25, 25, 1.0)
        self.spring = yearly(4, 20, 40, 1.0)
        m, d = self.days.month.values, self.days.day.values
        summer_break = ((m == 6) & (d >= 20)) | (m == 7) | ((m == 8) & (d <= 25))
        winter_break = ((m == 12) & (d >= 20)) | ((m == 1) & (d <= 5))
        self.student_away = np.where(summer_break, 0.65, np.where(winter_break, 0.45, 0.0))

    def index(self, d: date) -> int:
        return (d - self.start).days


# --------------------------------------------------------------------------- population
def build_population(rng: np.random.Generator, ref: date) -> tuple[pd.DataFrame, pd.DataFrame]:
    frames = []
    for nb in NEIGHBOURHOODS:
        n = nb.population
        bands = rng.choice(AB_N, size=n, p=age_distribution(nb))
        lo = np.array([r[0] for r in AGE_RANGES])[bands]
        hi = np.array([r[1] for r in AGE_RANGES])[bands]
        age = rng.integers(lo, hi + 1)
        p_female = np.where(bands >= 7, 0.60, np.where(bands == 6, 0.54, 0.50))
        sex = np.where(rng.random(n) < p_female, "F", "M")
        u = rng.random(n)
        insurance = np.where(u < nb.uninsured, "none", np.where(u < nb.uninsured + nb.private_insured, "private", "public"))
        life_p = 0.30 + 0.25 * (nb.profile in ("affluent", "retirement")) - 0.12 * (nb.profile in ("industrial", "downtown"))
        life = (age >= 25) & (rng.random(n) < life_p)
        frames.append(pd.DataFrame({"nb": nb.id, "band": bands, "age_ref": age, "sex": sex, "insurance": insurance,
                                    "life_insured": life}))
    p = pd.concat(frames, ignore_index=True)
    n = len(p)
    ids = rng.choice(900_000_000, size=n, replace=False) + 100_000_000
    p["national_id"] = [f"RV{x:09d}" for x in ids]
    offset = rng.integers(0, 365, n)
    p["dob"] = [ref - timedelta(days=int(a * 365.25 + o)) for a, o in zip(p["age_ref"].values, offset)]
    p["postcode"] = [f"{NEIGHBOURHOODS[b].postcode}-{x:04d}" for b, x in zip(p["nb"].values, rng.integers(1000, 9999, n))]
    p["group"] = p["nb"] * AB_N + p["band"]
    # enrolment
    primary = {nbid: inst.id for inst in INSTITUTIONS if inst.kind == "primary_school" for nbid in inst.serves}
    secondary = {nbid: inst.id for inst in INSTITUTIONS if inst.kind == "secondary_school" for nbid in inst.serves}
    school = np.full(n, None, dtype=object)
    band = p["band"].values
    nbv = p["nb"].values
    school[band == 1] = [primary[x] for x in nbv[band == 1]]
    school[band == 2] = [secondary[x] for x in nbv[band == 2]]
    u = rng.random(n)
    rsu_p = np.where(nbv == 1, 0.85, np.where(nbv == 5, 0.15, 0.18))
    hbc_p = np.where(nbv == 5, 0.35, np.where(nbv == 1, 0.03, 0.06))
    is18 = band == 3
    school[is18 & (u < rsu_p)] = "UNI-RSU"
    school[is18 & (u >= rsu_p) & (u < rsu_p + hbc_p)] = "UNI-HBC"
    p["institution"] = school
    p = p.sort_values("group", kind="stable").reset_index(drop=True)
    enrol = p.loc[p["institution"].notna(), ["national_id", "institution"]].reset_index(drop=True)
    return p, enrol


# --------------------------------------------------------------------------- helpers
def _weighted_choice(rng, options: list[tuple], size: int):
    vals = np.array([o[0] for o in options], dtype=object)
    w = np.array([o[1] for o in options], dtype=float)
    return vals[rng.choice(len(vals), size=size, p=w / w.sum())]


def _typo(rng, texts: np.ndarray, rate: float = 0.04) -> np.ndarray:
    out = texts.copy()
    idx = np.nonzero(rng.random(len(texts)) < rate)[0]
    for i in idx:
        t = out[i]
        if isinstance(t, str) and len(t) > 4:
            j = rng.integers(1, len(t) - 1)
            out[i] = t[:j] + t[j + 1:]
    return out


class Assembler:
    """Turns (person, day, syndrome, setting) tuples into full encounter rows."""

    def __init__(self, rng, cal: Calendar, persons: pd.DataFrame):
        self.rng, self.cal, self.p = rng, cal, persons
        self.counter = 0
        self.home_clinic = {0: [("FAC-NCH", 1)], 1: [("FAC-NCH", 1)], 2: [("FAC-NCH", 0.7), ("FAC-EFP", 0.3)],
                            3: [("FAC-NCH", 1)], 4: [("FAC-WMG", 0.75), ("FAC-NCH", 0.25)],
                            5: [("FAC-WMG", 0.45), ("FAC-NCH", 0.35), ("FAC-SUC", 0.20)],
                            6: [("FAC-EFP", 0.75), ("FAC-WMG", 0.25)], 7: [("FAC-EFP", 1)], 8: [("FAC-SUC", 1)],
                            9: [("FAC-SUC", 1)], 10: [("FAC-SUC", 0.7), ("FAC-EFP", 0.3)], 11: [("FAC-EFP", 1)]}
        self.near_smm = np.isin(np.arange(NB_N), [1, 2, 3, 6, 10])

    def assemble(self, person_ix, day_ix, syn_ix, is_ed, *, cc=None, icd=None, admit_p=None, icu_p=None,
                 death_p=None, outbreak=None, pathogen=None) -> pd.DataFrame:
        rng, p, cal = self.rng, self.p, self.cal
        n = len(person_ix)
        if n == 0:
            return pd.DataFrame()
        person_ix = np.asarray(person_ix)
        day_ix = np.asarray(day_ix)
        syn_ix = np.asarray(syn_ix)
        is_ed = np.asarray(is_ed, dtype=bool)
        nb = p["nb"].values[person_ix]
        band = p["band"].values[person_ix]
        ins = p["insurance"].values[person_ix]
        inst = p["institution"].values[person_ix]
        dow = cal.dow[day_ix]
        hol = cal.holiday[day_ix]
        # facility
        fac = np.empty(n, dtype=object)
        p_smm = np.clip(0.2 + 0.35 * (ins == "private") + 0.2 * self.near_smm[nb] - 0.1 * (ins == "none"), 0.05, 0.9)
        ed_ix = np.nonzero(is_ed)[0]
        fac[ed_ix] = np.where(rng.random(len(ed_ix)) < p_smm[ed_ix], "FAC-SMM", "FAC-RGH")
        op_ix = np.nonzero(~is_ed)[0]
        for i in op_ix:
            opts = self.home_clinic[nb[i]]
            fac[i] = opts[0][0] if len(opts) == 1 else opts[rng.choice(len(opts), p=[o[1] for o in opts])][0]
        u = rng.random(n)
        uninsured_public = (~is_ed) & (ins == "none") & (u < 0.7)
        fac[uninsured_public] = "FAC-NCH"
        u = rng.random(n)
        fac[(~is_ed) & (inst == "UNI-RSU") & (u < 0.75)] = "FAC-RSU"
        fac[(~is_ed) & (inst == "UNI-HBC") & (u < 0.70)] = "FAC-HBC"
        weekend_closed = (~is_ed) & (((dow == 5) & np.isin(fac, ["FAC-RSU", "FAC-HBC"])) | (dow == 6) | hol)
        fac[weekend_closed] = "FAC-SUC"
        # disposition
        syn_names = np.array(SYN, dtype=object)[syn_ix]
        if admit_p is None:
            admit_p = np.array([C.ADMIT_P[s][b] for s, b in zip(syn_names, band)])
        admitted = is_ed & (rng.random(n) < admit_p)
        if icu_p is None:
            icu_p = np.array([C.ICU_GIVEN_ADMIT[s] for s in syn_names])
        icu = admitted & (rng.random(n) < icu_p)
        if death_p is None:
            death_p = np.array(C.DEATH_GIVEN_ADMIT)[band] * np.where(icu, 2.5, 1.0)
        died = admitted & (rng.random(n) < death_p)
        # text and codes
        if cc is None:
            cc = np.empty(n, dtype=object)
            for k, s in enumerate(SYN):
                m = syn_ix == k
                if m.any():
                    cc[m] = _weighted_choice(rng, C.CHIEF_COMPLAINTS[s], int(m.sum()))
        cc = np.asarray(cc, dtype=object)
        if icd is None:
            icd = np.empty(n, dtype=object)
            for k, s in enumerate(SYN):
                m = np.nonzero(syn_ix == k)[0]
                if not len(m):
                    continue
                if s == "OTHER":
                    icd[m] = [str(rng.choice(C.OTHER_ICD[_closest_other(t)])) for t in cc[m]]
                else:
                    icd[m] = _weighted_choice(rng, C.ICD10[s], len(m))
            resp_like = np.isin(syn_names, ["ILI", "RESP"]) & admitted & (rng.random(n) < 0.55)
            icd[resp_like] = rng.choice(C.PNEUMONIA_CODES, size=int(resp_like.sum()))
        cc = _typo(rng, cc)
        icd = np.asarray(icd, dtype=object)
        icd = np.where(icu & np.isin(syn_names, ["ILI", "RESP"]), [f"{x}|J96.00" for x in icd], icd)
        # time of day and reporting lag
        hours = np.where(is_ed, rng.integers(0, 24, n), rng.integers(8, 19, n))
        minutes = rng.integers(0, 60, n)
        base = np.array([cal.days[d].to_pydatetime() for d in day_ix], dtype="datetime64[s]")
        event_dt = base + (hours * 3600 + minutes * 60).astype("timedelta64[s]")
        fac_obj = [FACILITIES[FACILITY_INDEX[f]] for f in fac]
        med = np.array([f.lag_hours[0] for f in fac_obj])
        sig = np.array([f.lag_hours[1] for f in fac_obj])
        lag_h = np.where(med > 0, med * np.exp(sig * rng.standard_normal(n)), np.nan)
        received = event_dt + np.nan_to_num(lag_h * 3600, nan=0).astype("timedelta64[s]")
        # incomplete coding in early triage feeds
        missing_icd = np.where(is_ed, rng.random(n) < 0.12, rng.random(n) < 0.03)
        missing_cc = (~is_ed) & (rng.random(n) < 0.05)
        start = self.counter
        self.counter += n
        df = pd.DataFrame({
            "encounter_id": [f"{f[4:]}-E{start + k:08d}" for k, f in enumerate(fac)],
            "person_ix": person_ix, "day_ix": day_ix, "event_dt": event_dt, "facility_id": fac,
            "encounter_class": np.where(admitted, "inpatient", np.where(is_ed, "emergency", "outpatient")),
            "chief_complaint": np.where(missing_cc, "", cc), "icd10": np.where(missing_icd, "", icd),
            "icd10_billing": icd, "admitted": admitted, "icu": icu, "died": died,
            "ehr_received_at": received, "truth_syndrome": syn_names,
            "truth_outbreak": outbreak if outbreak is not None else np.full(n, None, dtype=object),
            "truth_pathogen": pathogen if pathogen is not None else np.full(n, None, dtype=object),
        })
        return df


def _closest_other(text) -> str:
    if not isinstance(text, str):
        return "med refill"
    for k in C.OTHER_ICD:
        if k in text or text in k:
            return k
    return "med refill"


# --------------------------------------------------------------------------- baseline
def season_matrix(cal: Calendar) -> np.ndarray:
    """Seasonal multiplier S[syndrome, day, age_band]."""
    D = cal.n
    S = np.ones((len(SYN), D, AB_N))
    fluage = np.array([1.3, 1.4, 1.2, 0.9, 0.85, 0.9, 1.0, 1.1])
    kid = np.array([0.5, 1, 1, 0.3, 0.1, 0.1, 0, 0])
    rsvage = np.array([1.6, 0.3, 0.1, 0.05, 0.05, 0.1, 0.3, 0.5])
    f, rsv, cov, bts = cal.flu[:, None], cal.rsv[:, None], cal.covid[:, None], cal.bts[:, None]
    S[0] = 0.35 + (f + cal.flu_late[:, None]) * fluage + 0.5 * cov + 0.3 * bts * kid
    S[1] = 0.65 + 0.35 * cal.winter[:, None] + 0.25 * f + rsv * rsvage + 0.25 * cov + 0.4 * bts * kid
    S[2] = 0.75 + 0.45 * cal.winter[:, None] + 0.25 * cal.summer_gi[:, None]
    S[3] = 0.6 + 0.2 * f + 0.3 * cal.winter[:, None]
    S[4] = 0.9 + 0.2 * cal.spring[:, None]
    S[5] = 0.8 + 0.6 * cal.entero[:, None]
    S[6] = 0.97 + 0.06 * cal.winter[:, None]
    return S


DOW_ED = np.array([1.12, 1.03, 1.00, 0.98, 0.97, 0.93, 0.97])
DOW_OP = np.array([1.30, 1.15, 1.10, 1.10, 1.05, 0.55, 0.20])


def baseline_encounters(rng, cal: Calendar, persons: pd.DataFrame, asm: Assembler) -> pd.DataFrame:
    group_sizes = np.bincount(persons["group"].values, minlength=G_N)
    group_start = np.concatenate([[0], np.cumsum(group_sizes)[:-1]])
    care = np.array([nb.care_seeking for nb in NEIGHBOURHOODS])
    g_nb = np.arange(G_N) // AB_N
    g_ab = np.arange(G_N) % AB_N
    student_share = (persons.assign(st=persons["institution"].isin(["UNI-RSU", "UNI-HBC"]))
                     .groupby("group")["st"].mean().reindex(range(G_N), fill_value=0).values)
    S = season_matrix(cal)                                  # (7, D, 8)
    rates = np.array([C.VISIT_RATES[s] for s in SYN]) / 365.0  # (7, 8)
    lam = (group_sizes[None, None, :] * rates[:, None, g_ab] * S[:, :, g_ab] * care[None, None, g_nb])
    presence = 1 - cal.student_away[:, None] * student_share[None, :]
    lam = lam * presence[None, :, :]                       # (7, D, G)
    shock = rng.gamma(60, 1 / 60, size=(len(SYN), cal.n))
    lam = lam * shock[:, :, None]
    ed_share = np.array([C.ED_SHARE[s] for s in SYN])[:, None, None]
    hol = cal.holiday
    dow_ed = (DOW_ED[cal.dow] * np.where(hol, 1.1, 1.0))[None, :, None]
    dow_op = (DOW_OP[cal.dow] * np.where(hol, 0.15, 1.0))[None, :, None]
    frames = []
    for is_ed, mult in ((True, ed_share * dow_ed), (False, (1 - ed_share) * dow_op)):
        counts = rng.poisson(lam * mult)
        flat = counts.ravel()
        cell = np.repeat(np.arange(flat.size), flat)
        s_ix, d_ix, g_ix = np.unravel_index(cell, counts.shape)
        person = group_start[g_ix] + (rng.random(len(g_ix)) * group_sizes[g_ix]).astype(int)
        frames.append(asm.assemble(person, d_ix, s_ix, np.full(len(cell), is_ed)))
    return pd.concat(frames, ignore_index=True)


def background_deaths(rng, cal: Calendar, persons: pd.DataFrame, asm: Assembler):
    group_sizes = np.bincount(persons["group"].values, minlength=G_N)
    group_start = np.concatenate([[0], np.cumsum(group_sizes)[:-1]])
    g_ab = np.arange(G_N) % AB_N
    mort = np.array(C.MORTALITY)[g_ab] / 365.0
    fmax = cal.flu.max()
    seasonal = 1 + 0.30 * cal.flu / fmax + 0.08 * cal.winter
    lam = group_sizes[None, :] * mort[None, :] * seasonal[:, None]
    counts = rng.poisson(lam)
    cell = np.repeat(np.arange(counts.size), counts.ravel())
    d_ix, g_ix = np.unravel_index(cell, counts.shape)
    person = group_start[g_ix] + (rng.random(len(g_ix)) * group_sizes[g_ix]).astype(int)
    n = len(person)
    causes = C.DEATH_CAUSES
    w = np.array([c[2] for c in causes], float)
    cause_ix = np.empty(n, int)
    flu_boost = 1 + 1.2 * cal.flu[d_ix] / fmax
    for i in range(n):
        ww = w.copy()
        ww[[k for k, c in enumerate(causes) if c[1] == "resp"]] *= flu_boost[i]
        cause_ix[i] = rng.choice(len(causes), p=ww / ww.sum())
    code = np.array([causes[k][0] for k in cause_ix], dtype=object)
    cat = np.array([causes[k][1] for k in cause_ix], dtype=object)
    in_hospital = rng.random(n) < 0.55
    # In-hospital deaths are visible through the hospital EHR feed.
    hix = np.nonzero(in_hospital)[0]
    cc_map = {"resp": "SOB, decreased responsiveness", "cvd": "unresponsive, chest pain", "cancer": "palliative admission",
              "other": "weakness, decline", "injury": "fall"}
    syn_map = {"resp": 1, "cvd": 6, "cancer": 6, "other": 6, "injury": 6}
    hosp = asm.assemble(person[hix], d_ix[hix], np.array([syn_map[c] for c in cat[hix]]), np.ones(len(hix), bool),
                        cc=np.array([cc_map[c] for c in cat[hix]], dtype=object), icd=code[hix],
                        admit_p=np.ones(len(hix)), icu_p=np.full(len(hix), 0.3), death_p=np.ones(len(hix)))
    deaths = pd.DataFrame({"person_ix": person, "day_ix": d_ix, "cause_icd10": code, "cause_cat": cat,
                           "in_hospital": in_hospital})
    return hosp, deaths


# --------------------------------------------------------------------------- labs
def lab_orders(rng, cal: Calendar, persons: pd.DataFrame, enc: pd.DataFrame) -> pd.DataFrame:
    n = len(enc)
    syn = enc["truth_syndrome"].values
    d = enc["day_ix"].values
    band = persons["band"].values[enc["person_ix"].values]
    admitted = enc["admitted"].values
    ed = enc["encounter_class"].values != "outpatient"
    rows = []
    # pathogen attribution for background respiratory illness
    pathogen = enc["truth_pathogen"].values.copy()
    resp = np.isin(syn, ["ILI", "RESP"]) & (pd.isna(pathogen))
    ri = np.nonzero(resp)[0]
    fluage = np.array([1.3, 1.4, 1.2, 0.9, 0.85, 0.9, 1.0, 1.1])
    rsvage = np.array([1.6, 0.3, 0.1, 0.05, 0.05, 0.1, 0.3, 0.5])
    wA = 0.8 * cal.flu[d[ri]] * fluage[band[ri]]
    wB = cal.flu_late[d[ri]] * 0.9 + 0.05 * cal.flu[d[ri]]
    wR = cal.rsv[d[ri]] * rsvage[band[ri]] * 0.8
    wC = 0.5 * cal.covid[d[ri]] + 0.08
    wO = np.where(syn[ri] == "ILI", 0.55, 1.2) + 0.3 * cal.bts[d[ri]]
    W = np.vstack([wA, wB, wR, wC, wO]).T
    W = W / W.sum(1, keepdims=True)
    cum = W.cumsum(1)
    pick = (rng.random(len(ri))[:, None] > cum).sum(1)
    names = np.array(["Influenza A", "Influenza B", "RSV", "SARS-CoV-2", "other"], dtype=object)
    pathogen[ri] = names[np.minimum(pick, 4)]
    gi = (syn == "GI") & pd.isna(pathogen)
    gi_ix = np.nonzero(gi)[0]
    W = np.vstack([0.6 * cal.winter[d[gi_ix]] + 0.1, 0.15 * cal.summer_gi[d[gi_ix]] + 0.02,
                   0.10 * cal.summer_gi[d[gi_ix]] + 0.04, 0.02 * cal.summer_gi[d[gi_ix]] + 0.01,
                   np.full(len(gi_ix), 0.6)]).T
    W = W / W.sum(1, keepdims=True)
    pick = (rng.random(len(gi_ix))[:, None] > W.cumsum(1)).sum(1)
    gnames = np.array(["Norovirus", "Salmonella", "Campylobacter", "STEC", "other"], dtype=object)
    pathogen[gi_ix] = gnames[np.minimum(pick, 4)]
    enc["truth_pathogen"] = pathogen

    def emit(mask, panel_name, p_test, positive_fn):
        idx = np.nonzero(mask & (rng.random(n) < p_test))[0]
        analytes = C.PANELS[panel_name]
        for i in idx:
            order = f"LAB-{enc['encounter_id'].values[i]}-{len(rows):07d}"
            for analyte, loinc in analytes.items():
                pos = positive_fn(pathogen[i], analyte)
                rows.append((order, i, panel_name, loinc, analyte, "positive" if pos else "negative"))

    def pos_resp(path, analyte):
        if path == analyte:
            return rng.random() < 0.95
        return rng.random() < 0.002

    p_resp = np.where(admitted, 0.80, np.where(ed, 0.30, 0.07))
    emit(np.isin(syn, ["ILI", "RESP"]), "Respiratory pathogen panel (NAAT)", p_resp, pos_resp)
    p_gi = np.where(admitted, 0.60, np.where(ed, 0.15, 0.04))
    emit(syn == "GI", "Gastrointestinal pathogen panel", p_gi, pos_resp)
    leg_mask = np.isin(syn, ["ILI", "RESP"]) & admitted & (band >= 5)
    emit(leg_mask, "Legionella urinary antigen", np.where(pathogen == "Legionella", 0.75, 0.35),
         lambda path, a: (rng.random() < 0.85) if path == "Legionella" else (rng.random() < 0.005))
    emit(syn == "RASH", "Measles serology", np.where(pathogen == "Measles", 0.80, 0.12),
         lambda path, a: (rng.random() < 0.92) if path == "Measles" else (rng.random() < 0.002))
    if not rows:
        return pd.DataFrame()
    labs = pd.DataFrame(rows, columns=["order_id", "enc_row", "panel", "loinc", "analyte", "result"])
    er = labs["enc_row"].values
    labs["person_ix"] = enc["person_ix"].values[er]
    labs["day_ix"] = enc["day_ix"].values[er]
    labs["encounter_id"] = enc["encounter_id"].values[er]
    # one turnaround time per order
    order_tat = {o: rng.uniform(20, 72) for o in labs["order_id"].unique()}
    tat = labs["order_id"].map(order_tat).values
    labs["received_at"] = enc["event_dt"].values[er] + (tat * 3600).astype("timedelta64[s]")
    return labs.drop(columns=["enc_row"])


# --------------------------------------------------------------------------- claims
def make_claims(rng, persons: pd.DataFrame, enc: pd.DataFrame) -> pd.DataFrame:
    ins = persons["insurance"].values[enc["person_ix"].values]
    fac = enc["facility_id"].values
    billable = np.isin(ins, ["public", "private"]) & ~np.isin(fac, ["FAC-RSU", "FAC-HBC"])
    idx = np.nonzero(billable)[0]
    payer = np.where(ins[idx] == "public", "SRC-NHI", "SRC-MHI")
    med_days = np.where(payer == "SRC-NHI", 6.0, 10.0)
    lag = med_days * np.exp(0.45 * rng.standard_normal(len(idx)))
    claims = pd.DataFrame({
        "claim_id": [f"CLM-{p[4:]}-{k:09d}" for k, p in zip(range(len(idx)), payer)],
        "source_id": payer, "enc_row": idx,
        "received_at": enc["event_dt"].values[idx] + (lag * 86400).astype("timedelta64[s]"),
    })
    return claims


def make_death_claims(rng, persons: pd.DataFrame, deaths: pd.DataFrame, cal: Calendar) -> pd.DataFrame:
    life = persons["life_insured"].values[deaths["person_ix"].values]
    d = deaths.loc[life].copy()
    lag = 14 * np.exp(0.5 * rng.standard_normal(len(d)))
    base = np.array([cal.days[i].to_pydatetime() for i in d["day_ix"].values], dtype="datetime64[s]")
    d["received_at"] = base + (lag * 86400).astype("timedelta64[s]")
    d["claim_id"] = [f"DC-ALC-{k:07d}" for k in range(len(d))]
    d["cause_known"] = rng.random(len(d)) < 0.75
    return d.reset_index(drop=True)


# --------------------------------------------------------------------------- driver
def generate(config: SimConfig | None = None, progress=print) -> World:
    from . import scenarios

    cfg = config or SimConfig()
    rng = np.random.default_rng(cfg.seed)
    cal = Calendar(cfg.start, cfg.end)
    progress(f"[sim] building population for {len(NEIGHBOURHOODS)} neighbourhoods")
    persons, enrol = build_population(rng, cfg.reference_date)
    asm = Assembler(rng, cal, persons)
    progress(f"[sim] population {len(persons):,}; generating {cal.n} days of baseline activity")
    base = baseline_encounters(rng, cal, persons, asm)
    hosp_deaths, deaths = background_deaths(rng, cal, persons, asm)
    frames = [base, hosp_deaths]
    outbreaks = []
    for name in cfg.scenarios:
        fn = getattr(scenarios, name)
        enc, meta = fn(rng, cal, persons, asm)
        progress(f"[sim] scenario {name}: {len(enc)} care-seeking encounters")
        frames.append(enc)
        outbreaks.append(meta)
    enc = pd.concat([f for f in frames if len(f)], ignore_index=True)
    # Coding artefact: post-upgrade mapping of abdominal pain to infectious gastroenteritis at one hospital
    art = next((o for o in outbreaks if o["id"] == "coding_artefact_2026"), None)
    if art:
        start_ix = cal.index(date.fromisoformat(art["start"]))
        m = ((enc["facility_id"] == "FAC-SMM") & (enc["day_ix"] >= start_ix) &
             enc["icd10_billing"].astype(str).str.startswith("R10") & (enc["truth_syndrome"] == "OTHER"))
        enc.loc[m, "icd10"] = "A09"
        enc.loc[m, "icd10_billing"] = "A09"
        enc.loc[m, "truth_outbreak"] = "coding_artefact_2026"
        art["records"] = int(m.sum())
    progress(f"[sim] {len(enc):,} encounters; generating laboratory results")
    labs = lab_orders(rng, cal, persons, enc)
    progress(f"[sim] {len(labs):,} lab observations; generating insurer claims and deaths")
    claims = make_claims(rng, persons, enc)
    death_claims = make_death_claims(rng, persons, deaths, cal)
    truth = enc.loc[enc["truth_outbreak"].notna(), ["encounter_id", "truth_outbreak", "truth_syndrome", "truth_pathogen",
                                                     "day_ix"]].copy()
    truth["date"] = cal.days[truth["day_ix"].values].strftime("%Y-%m-%d")
    return World(cfg, persons, enc, labs, claims, death_claims, enrol, truth.drop(columns=["day_ix"]), outbreaks)
