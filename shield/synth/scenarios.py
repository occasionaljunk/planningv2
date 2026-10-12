"""Injected outbreak scenarios with ground truth.

Each scenario returns the care-seeking encounters it causes (with
``truth_outbreak`` set) plus a metadata dict describing what really happened.
Ground truth is used only for evaluation and for scripted feedback in the
demo; the detection agent never reads it.

Scenarios
---------
measles_2024          Measles among under-vaccinated children in Cedar Hills (historical, Feb-Apr 2024).
novel_resp_2026       A novel respiratory pathogen introduced at Riverton State University
                      (stochastic SEIR metapopulation model, 96 age x neighbourhood groups).
                      Standard respiratory panels are negative, as with any new pathogen.
noro_school_2026      Norovirus point-source outbreak at Northgate Primary School with
                      secondary household cases.
legionella_2026       Legionnaires' disease from a contaminated cooling tower in Ironworks.
coding_artefact_2026  Not an outbreak: after an EHR upgrade St. Mary's Medical Center maps
                      abdominal-pain visits to infectious gastroenteritis (ICD-10 A09).
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from . import clinical as C

AB_N, NB_N = 8, 12


def _pick(rng, persons: pd.DataFrame, mask: np.ndarray, k: int) -> np.ndarray:
    idx = np.nonzero(mask)[0]
    if k <= 0 or len(idx) == 0:
        return np.array([], dtype=int)
    return rng.choice(idx, size=min(k, len(idx)), replace=False)


def _cc(rng, options, n):
    vals = np.array([o[0] for o in options], dtype=object)
    w = np.array([o[1] for o in options], float)
    return vals[rng.choice(len(vals), size=n, p=w / w.sum())]


# --------------------------------------------------------------------------- measles 2024
def measles_2024(rng, cal, persons, asm):
    start = date(2024, 2, 26)
    gens = [2, 5, 11, 14, 8, 3]
    used: set = set()
    rows_p, rows_d = [], []
    for g, k in enumerate(gens):
        mask = (np.isin(persons["nb"].values, [3, 0]) & np.isin(persons["band"].values, [0, 1, 2]) |
                (persons["nb"].values == 3) & (persons["band"].values == 4) & (rng.random(len(persons)) < 0.02))
        mask &= ~np.isin(np.arange(len(persons)), list(used))
        ppl = _pick(rng, persons, mask, k)
        used.update(ppl.tolist())
        onset = cal.index(start) + g * 11 + rng.integers(0, 9, len(ppl))
        rows_p.append(ppl)
        rows_d.append(onset + rng.integers(2, 5, len(ppl)))
    p = np.concatenate(rows_p)
    d = np.concatenate(rows_d)
    n = len(p)
    is_ed = rng.random(n) < 0.5
    cc = _cc(rng, [("fever and rash", 4), ("rash spreading from face, fever", 3), ("fever, cough, red eyes, rash", 3),
                   ("high fever and rash x2d", 2)], n)
    enc = asm.assemble(p, d, np.full(n, 4), is_ed, cc=cc, icd=np.where(rng.random(n) < 0.6, "B05.9", "R21|R50.9"),
                       admit_p=np.full(n, 0.22), icu_p=np.full(n, 0.05), death_p=np.zeros(n),
                       outbreak=np.full(n, "measles_2024", dtype=object), pathogen=np.full(n, "Measles", dtype=object))
    meta = {"id": "measles_2024", "name": "Measles outbreak among under-vaccinated children", "pathogen": "Measles virus",
            "hypothesis": "measles", "syndrome_family": "RASH", "start": str(start), "end": "2024-04-20",
            "location": "Cedar Hills and Northgate", "setting": "community and primary schools", "care_seeking_encounters": n,
            "description": "Propagated measles transmission in a neighbourhood with low MMR coverage."}
    return enc, meta


# --------------------------------------------------------------------------- novel respiratory pathogen
AGE_CONTACT = np.array([
    [2.0, 1.0, 0.5, 0.4, 1.4, 0.6, 0.4, 0.3],
    [1.0, 5.0, 1.2, 0.4, 1.2, 0.6, 0.4, 0.3],
    [0.5, 1.2, 6.0, 1.0, 0.9, 0.8, 0.3, 0.2],
    [0.4, 0.4, 1.0, 7.0, 1.2, 0.6, 0.3, 0.2],
    [1.4, 1.2, 0.9, 1.2, 3.0, 1.6, 0.6, 0.4],
    [0.6, 0.6, 0.8, 0.6, 1.6, 2.6, 1.0, 0.6],
    [0.4, 0.4, 0.3, 0.3, 0.6, 1.0, 1.8, 1.0],
    [0.3, 0.3, 0.2, 0.2, 0.4, 0.6, 1.0, 1.6],
])
NOVEL_CARE = np.array([0.55, 0.35, 0.30, 0.38, 0.35, 0.40, 0.50, 0.60])
NOVEL_HOSP = np.array([0.03, 0.01, 0.01, 0.02, 0.05, 0.12, 0.24, 0.36])
NOVEL_ICU = 0.28
NOVEL_DEATH = np.array([0.01, 0.005, 0.005, 0.01, 0.03, 0.08, 0.16, 0.26])
# Neighbourhood adjacency on the 4x3 grid plus the university as a hub.
_GRID = {n: (n // 4, n % 4) for n in range(NB_N)}


def _spatial_matrix(pop: np.ndarray) -> np.ndarray:
    M = np.zeros((NB_N, NB_N))
    for i in range(NB_N):
        for j in range(NB_N):
            (ri, ci), (rj, cj) = _GRID[i], _GRID[j]
            dist = abs(ri - rj) + abs(ci - cj)
            M[i, j] = 1.0 if i == j else (0.10 if dist == 1 else 0.025)
    M = M / M.sum(1, keepdims=True)
    return M


def novel_resp_2026(rng, cal, persons, asm, beta: float = 0.017, campus: float = 0.58,
                    seed_day: date = date(2026, 9, 6)):
    nb = persons["nb"].values
    band = persons["band"].values
    grp = persons["group"].values
    N = np.bincount(grp, minlength=NB_N * AB_N).astype(float)
    Nnb = N.reshape(NB_N, AB_N)
    spatial = _spatial_matrix(Nnb.sum(1))
    student = persons["institution"].isin(["UNI-RSU", "UNI-HBC"]).values
    student_share = np.bincount(grp, weights=student, minlength=NB_N * AB_N) / np.maximum(N, 1)
    S = N.copy()
    E = np.zeros_like(N)
    I = np.zeros_like(N)
    seed_g = 1 * AB_N + 3
    t0 = cal.index(seed_day)
    I[seed_g] = 8
    S[seed_g] -= 8
    onsets = []  # (day, group, count)
    sigma, gamma = 1 / 3.0, 1 / 6.0
    for t in range(t0, cal.n):
        Ig = I.reshape(NB_N, AB_N)
        prev = Ig / np.maximum(Nnb, 1)
        # force of infection: age contacts x spatial mixing, plus campus mixing among students
        foi = beta * np.einsum("ij,ab,jb->ia", spatial, AGE_CONTACT, prev)
        stud_prev = (I * student_share).sum() / max((N * student_share).sum(), 1)
        foi = foi.ravel() + campus * student_share * stud_prev
        new_inf = rng.binomial(S.astype(int), 1 - np.exp(-foi))
        new_sym = rng.binomial(E.astype(int), sigma)
        new_rec = rng.binomial(I.astype(int), gamma)
        S -= new_inf
        E += new_inf - new_sym
        I += new_sym - new_rec
        for g in np.nonzero(new_sym)[0]:
            onsets.append((t, g, int(new_sym[g])))
    # Symptomatic, care-seeking cases become encounters
    group_members = {g: np.nonzero(grp == g)[0] for g in range(NB_N * AB_N)}
    P, D, ED = [], [], []
    total_inf = 0
    for t, g, k in onsets:
        total_inf += k
        a = g % AB_N
        sym = rng.binomial(k, 0.70)
        seek = rng.binomial(sym, NOVEL_CARE[a])
        if seek == 0:
            continue
        ppl = rng.choice(group_members[g], size=seek, replace=False)
        delay = 1 + rng.poisson(1.5, seek)
        day = t + delay
        ok = day < cal.n
        P.append(ppl[ok])
        D.append(day[ok])
        ED.append(rng.random(ok.sum()) < np.where(a >= 6, 0.6, 0.33))
    p = np.concatenate(P) if P else np.array([], int)
    d = np.concatenate(D) if D else np.array([], int)
    is_ed = np.concatenate(ED) if ED else np.array([], bool)
    n = len(p)
    a = band[p]
    syn = np.where(rng.random(n) < 0.55, 0, 1)
    cc = _cc(rng, [("fever and dry cough x3 days", 5), ("fever, cough, fatigue", 4), ("fever chills body aches", 3),
                   ("SOB, fever", 3), ("cough, fever, loss of appetite", 2), ("flu like symptoms", 3),
                   ("fever 38.8, dry cough, headache", 2), ("difficulty breathing, fever", 1)], n)
    icd = np.where(syn == 0, _cc(rng, [("J11.1", 3), ("R50.9|R05.9", 4), ("J06.9", 2)], n),
                   _cc(rng, [("J06.9", 3), ("J20.9", 3), ("R05.9|R50.9", 3), ("J12.89", 1)], n))
    admit_p = NOVEL_HOSP[a] / np.where(is_ed, 0.6, 1.0)
    admitted_preview = None  # disposition sampled inside assemble
    enc = asm.assemble(p, d, syn, is_ed | (rng.random(n) < NOVEL_HOSP[a] * 0.5), cc=cc, icd=icd,
                       admit_p=np.clip(admit_p, 0, 0.9), icu_p=np.full(n, NOVEL_ICU),
                       death_p=NOVEL_DEATH[a] / 0.5 * 0.35,
                       outbreak=np.full(n, "novel_resp_2026", dtype=object),
                       pathogen=np.full(n, "Novel respiratory virus", dtype=object))
    adm = enc["admitted"].values
    enc.loc[adm & (rng.random(n) < 0.7), "icd10"] = "J12.89"
    enc.loc[enc["icd10"] == "J12.89", "icd10_billing"] = "J12.89"
    meta = {"id": "novel_resp_2026", "name": "Novel respiratory pathogen introduced at a university",
            "pathogen": "Novel respiratory virus (not detected by standard panels)", "hypothesis": "novel_respiratory",
            "syndrome_family": "RESPIRATORY", "start": str(seed_day), "end": None,
            "location": "University Heights (Riverton State University), spreading city-wide",
            "setting": "university then community", "infections_simulated": int(total_inf),
            "care_seeking_encounters": n,
            "description": "Stochastic SEIR metapopulation (latent 3d, infectious 6d) seeded with 8 infectious students. "
                           "Respiratory multiplex panels (influenza A/B, RSV, SARS-CoV-2) are negative."}
    return enc, meta


# --------------------------------------------------------------------------- norovirus at a primary school
def noro_school_2026(rng, cal, persons, asm):
    exposure = date(2026, 9, 22)
    t0 = cal.index(exposure)
    kids = _pick(rng, persons, (persons["institution"] == "SCH-NGP").values, 85)
    onset = t0 + rng.choice([1, 1, 1, 2, 2], len(kids))
    care = rng.random(len(kids)) < 0.40
    p1, d1 = kids[care], onset[care] + rng.integers(0, 2, care.sum())
    # secondary household / classroom cases (adults 25-44 and siblings in the catchment)
    sec_mask = np.isin(persons["nb"].values, [0, 1]) & np.isin(persons["band"].values, [0, 1, 4])
    sec = _pick(rng, persons, sec_mask, 45)
    s_on = t0 + 3 + rng.integers(0, 7, len(sec))
    care2 = rng.random(len(sec)) < 0.30
    p = np.concatenate([p1, sec[care2]])
    d = np.concatenate([d1, s_on[care2]])
    n = len(p)
    is_ed = rng.random(n) < 0.35
    cc = _cc(rng, [("vomiting since last night", 4), ("n/v/d", 4), ("vomiting and diarrhea", 4),
                   ("stomach bug, several kids sick at school", 1), ("diarrhea x1 day, cramps", 2), ("emesis x5", 1)], n)
    icd = _cc(rng, [("A08.4", 3), ("A09", 3), ("R11.2", 3), ("R19.7", 1)], n)
    enc = asm.assemble(p, d, np.full(n, 2), is_ed, cc=cc, icd=icd, admit_p=np.full(n, 0.06), icu_p=np.zeros(n),
                       death_p=np.zeros(n), outbreak=np.full(n, "noro_school_2026", dtype=object),
                       pathogen=np.full(n, "Norovirus", dtype=object))
    meta = {"id": "noro_school_2026", "name": "Norovirus outbreak at Northgate Primary School", "pathogen": "Norovirus GII",
            "hypothesis": "norovirus", "syndrome_family": "GI", "start": str(exposure), "end": "2026-10-04",
            "location": "Northgate Primary School (Northgate, University Heights catchment)",
            "setting": "primary school", "ill_students": 85, "care_seeking_encounters": n,
            "description": "Point-source exposure at a school lunch followed by secondary household cases."}
    return enc, meta


# --------------------------------------------------------------------------- Legionnaires' disease
def legionella_2026(rng, cal, persons, asm):
    t_start, t_end = cal.index(date(2026, 9, 24)), cal.index(date(2026, 10, 6))
    nbv = persons["nb"].values
    band = persons["band"].values
    sexv = persons["sex"].values
    eligible = (band >= 5) & ((sexv == "M") | (rng.random(len(persons)) < 0.5))
    ppl = np.concatenate([_pick(rng, persons, eligible & (nbv == 8), 32), _pick(rng, persons, eligible & (nbv == 9), 10)])
    exposure = rng.integers(t_start, t_end, len(ppl))
    onset = exposure + np.clip(np.round(rng.gamma(6.0, 1.0, len(ppl))), 2, 10).astype(int)
    d = onset + rng.integers(1, 3, len(ppl))
    n = len(ppl)
    is_ed = rng.random(n) < 0.9
    cc = _cc(rng, [("fever, cough, confusion", 3), ("SOB, fever, diarrhea", 2), ("high fever, muscle aches, cough", 3),
                   ("pneumonia, fever 39.5", 2), ("cough, SOB, weakness", 2)], n)
    enc = asm.assemble(ppl, d, np.full(n, 1), is_ed, cc=cc, icd=_cc(rng, [("J18.9", 6), ("J18.1", 1), ("R05.9|R50.9", 1)], n),
                       admit_p=np.full(n, 0.82), icu_p=np.full(n, 0.30), death_p=np.full(n, 0.11),
                       outbreak=np.full(n, "legionella_2026", dtype=object), pathogen=np.full(n, "Legionella", dtype=object))
    meta = {"id": "legionella_2026", "name": "Legionnaires' disease cluster linked to a cooling tower",
            "pathogen": "Legionella pneumophila serogroup 1", "hypothesis": "legionella", "syndrome_family": "RESPIRATORY",
            "start": "2026-09-24", "end": "2026-10-16", "location": "Ironworks (with spill-over to Southbank)",
            "setting": "community, environmental point source", "care_seeking_encounters": n,
            "description": "Older adults (mostly men) exposed to aerosol from a contaminated cooling tower."}
    return enc, meta


# --------------------------------------------------------------------------- coding artefact (applied in generator)
def coding_artefact_2026(rng, cal, persons, asm):
    meta = {"id": "coding_artefact_2026", "name": "EHR upgrade coding artefact (not a real outbreak)",
            "pathogen": None, "hypothesis": "reporting_artefact", "syndrome_family": "GI", "start": "2026-10-01",
            "end": None, "location": "St. Mary's Medical Center", "setting": "data pipeline",
            "description": "After an EHR upgrade, abdominal-pain visits are coded A09 (infectious gastroenteritis). "
                           "A false alarm the platform should recognise and down-weight."}
    return pd.DataFrame(), meta
