"""Analytical view of the platform database used by the agent.

Loads pseudonymised primary records available *as of* a given day (records
received later are invisible, exactly as in live operation) and builds the
daily count matrices the detectors work on.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from ..db import get_conn
from ..security.pseudonym import AGE_BANDS

FAMILIES = {
    "RESPIRATORY": ["ARI", "ILI", "RESP", "SEVERE_RESP"],
    "GI": ["GI"],
    "FEVER": ["FEVER"],
    "RASH": ["RASH"],
    "NEURO": ["NEURO"],
    "MORTALITY": ["DEATHS"],
}
SYNDROME_FAMILY = {s: f for f, ss in FAMILIES.items() for s in ss}
SYNDROME_LABEL = {
    "ARI": "acute respiratory illness", "ILI": "influenza-like illness", "RESP": "respiratory illness",
    "SEVERE_RESP": "severe respiratory illness (pneumonia / admitted)", "GI": "gastrointestinal illness",
    "FEVER": "fever without localising signs", "RASH": "fever and rash illness", "NEURO": "meningitis/encephalitis-like illness",
    "DEATHS": "all-cause deaths",
}
FAMILY_LABEL = {"RESPIRATORY": "Respiratory", "GI": "Gastrointestinal", "FEVER": "Febrile", "RASH": "Fever & rash",
                "NEURO": "Neurological", "MORTALITY": "Mortality"}
DEFAULT_HOLIDAYS = ["01-01", "05-01", "08-15", "12-25", "12-26"]


@dataclass
class Reference:
    nb_names: dict[int, str]
    centroids: np.ndarray
    polygons: dict[int, list]
    institutions: pd.DataFrame
    sources: pd.DataFrame
    population: pd.DataFrame
    holidays: list[str] = field(default_factory=lambda: list(DEFAULT_HOLIDAYS))

    @property
    def n_nb(self) -> int:
        return len(self.nb_names)

    def pop_by_nb(self) -> np.ndarray:
        s = self.population.groupby("neighbourhood_id")["count"].sum()
        return s.reindex(range(self.n_nb), fill_value=0).values.astype(float)

    def pop_by_age(self) -> np.ndarray:
        s = self.population.groupby("age_band")["count"].sum()
        return s.reindex(range(len(AGE_BANDS)), fill_value=0).values.astype(float)

    def source_name(self, sid: str) -> str:
        m = self.sources.loc[self.sources["id"] == sid, "name"]
        return m.iloc[0] if len(m) else sid

    def facility_name(self, fid: str) -> str:
        names = {"COMMUNITY": "Deaths outside hospital", "HOSPITAL": "Hospital deaths (insurer-reported)"}
        if fid in names:
            return names[fid]
        from ..synth.city import FACILITIES
        for f in FACILITIES:
            if f.id == fid:
                return f.name
        return fid

    def institution_name(self, iid: str) -> str:
        m = self.institutions.loc[self.institutions["id"] == iid, "name"]
        return m.iloc[0] if len(m) else iid


def load_reference(con: sqlite3.Connection | None = None) -> Reference:
    con = con or get_conn()
    geo = pd.read_sql_query("SELECT * FROM geography ORDER BY neighbourhood_id", con)
    import json
    from ..db import kv_get
    return Reference(
        nb_names=dict(zip(geo["neighbourhood_id"], geo["name"])),
        centroids=geo[["centroid_x", "centroid_y"]].values,
        polygons={int(r.neighbourhood_id): json.loads(r.polygon) for r in geo.itertuples()},
        institutions=pd.read_sql_query("SELECT * FROM institutions", con),
        sources=pd.read_sql_query("SELECT * FROM sources", con),
        population=pd.read_sql_query("SELECT * FROM population", con),
        holidays=kv_get("holidays", DEFAULT_HOLIDAYS, con),
    )


_CACHE: dict = {}


def _load_events(con, as_of_end: str) -> pd.DataFrame:
    key = (str(con.execute("PRAGMA database_list").fetchone()[2]),)
    cached = _CACHE.get(key)
    cols = ("id, event_date, received_at, syndrome, severe_resp, age_band, sex, neighbourhood_id AS nb, facility_id, "
            "source_id, record_type, encounter_class, admitted, icu, died, patient_pid AS pid")
    if cached is None:
        df = pd.read_sql_query(f"SELECT {cols} FROM events WHERE is_primary=1", con)
        max_id = int(df["id"].max()) if len(df) else 0
    else:
        df, max_id = cached
        new = pd.read_sql_query(f"SELECT {cols} FROM events WHERE is_primary=1 AND id > ?", con, params=(max_id,))
        if len(new):
            df = pd.concat([df, new], ignore_index=True)
            max_id = int(new["id"].max())
        recent = (date.fromisoformat(as_of_end[:10]) - timedelta(days=90)).isoformat()
        flipped = {r[0] for r in con.execute("SELECT id FROM events WHERE event_date >= ? AND is_primary=0", (recent,))}
        if flipped:
            df = df[~df["id"].isin(flipped)]
    _CACHE[key] = (df, max_id)
    return df


def clear_cache() -> None:
    _CACHE.clear()


class AnalysisData:
    def __init__(self, as_of: date, con: sqlite3.Connection | None = None, ref: Reference | None = None):
        self.con = con or get_conn()
        self.ref = ref or load_reference(self.con)
        self.as_of = as_of
        end = f"{as_of.isoformat()}T23:59:59"
        ev = _load_events(self.con, end)
        ev = ev[(ev["received_at"] <= end) & (ev["event_date"] <= as_of.isoformat())]
        start = date.fromisoformat(ev["event_date"].min())
        self.start = start
        self.T = (as_of - start).days
        self.days = pd.date_range(start, as_of, freq="D")
        ev = ev.copy()
        ev["day"] = (pd.to_datetime(ev["event_date"]) - pd.Timestamp(start)).dt.days.astype(int)
        enrol = pd.read_sql_query("SELECT patient_pid AS pid, institution_id AS inst FROM enrolment", self.con)
        enrol = enrol.drop_duplicates("pid")
        ev = ev.merge(enrol, on="pid", how="left")
        ev["nb"] = ev["nb"].fillna(-1).astype(int)
        ev["age_band"] = ev["age_band"].fillna(-1).astype(int)
        self.ev = ev.reset_index(drop=True)
        ev = self.ev
        # integer-coded numpy views: masks over ~1M rows must be fast
        self.a_id = ev["id"].to_numpy()
        self.a_day = ev["day"].to_numpy()
        self.a_nb = ev["nb"].to_numpy()
        self.a_age = ev["age_band"].to_numpy()
        self.a_severe = ev["severe_resp"].to_numpy() == 1
        self.a_died = ev["died"].to_numpy() == 1
        self.a_visit = (ev["record_type"].to_numpy(dtype=object) != "death")
        syn = ev["syndrome"].to_numpy(dtype=object)
        self.a_syn = {k: (syn == k) for k in ("ILI", "RESP", "GI", "FEVER", "RASH", "NEURO", "OTHER")}
        self.facilities = sorted(ev["facility_id"].dropna().unique().tolist())
        fac_idx = {k: i for i, k in enumerate(self.facilities)}
        self.a_fac = np.array([fac_idx.get(x, -1) for x in ev["facility_id"].to_numpy(dtype=object)])
        self.institutions = self.ref.institutions["id"].tolist()
        inst_idx = {k: i for i, k in enumerate(self.institutions)}
        self.a_inst = np.array([inst_idx.get(x, -1) if isinstance(x, str) else -1 for x in ev["inst"].to_numpy(dtype=object)])
        labs = pd.read_sql_query(
            "SELECT patient_pid AS pid, order_id, collected_date, received_at, panel, analyte, result, age_band, "
            "neighbourhood_id AS nb FROM labs WHERE received_at <= ? AND collected_date >= ?", self.con,
            params=(end, start.isoformat()))
        labs["day"] = (pd.to_datetime(labs["collected_date"]) - pd.Timestamp(start)).dt.days.astype(int)
        self.labs = labs
        self.dow = self.days.dayofweek.values
        self.doy = self.days.dayofyear.values
        self.year = self.days.year.values
        hol = set(self.ref.holidays)
        self.holiday = np.array([d.strftime("%m-%d") in hol for d in self.days])
        self.t = np.arange(self.T + 1, dtype=float)

    # ------------------------------------------------------------------ masks and matrices
    def syndrome_mask(self, syn: str) -> np.ndarray:
        if syn == "DEATHS":
            return self.a_died.copy()
        if syn == "ARI":
            return (self.a_syn["ILI"] | self.a_syn["RESP"]) & self.a_visit
        if syn == "SEVERE_RESP":
            return self.a_severe & self.a_visit
        return self.a_syn[syn] & self.a_visit

    def strata_keys(self, stype: str) -> tuple[np.ndarray, list]:
        if stype == "all":
            return np.zeros(len(self.a_id), int), ["all"]
        if stype == "age":
            return self.a_age, list(range(len(AGE_BANDS)))
        if stype == "nb":
            return self.a_nb, list(range(self.ref.n_nb))
        if stype == "inst":
            return self.a_inst, list(self.institutions)
        if stype == "facility":
            return self.a_fac, list(self.facilities)
        raise ValueError(stype)

    def daily_matrix(self, mask: np.ndarray, keys: np.ndarray, n_keys: int) -> np.ndarray:
        n_days = self.T + 1
        ok = mask & (keys >= 0)
        flat = keys[ok] * n_days + self.a_day[ok]
        return np.bincount(flat, minlength=n_keys * n_days).reshape(n_keys, n_days).astype(float)

    def stratum_label(self, stype: str, value) -> str:
        if stype == "all":
            return "All residents"
        if stype == "age":
            return f"Age {AGE_BANDS[int(value)]}"
        if stype == "nb":
            return self.ref.nb_names.get(int(value), str(value))
        if stype == "inst":
            return self.ref.institution_name(value)
        if stype == "facility":
            return self.ref.facility_name(value)
        if stype == "zone":
            return " + ".join(self.ref.nb_names[int(z)] for z in value)
        if stype == "lab":
            return str(value)
        return str(value)

    def place_mask(self, stype: str, value) -> np.ndarray:
        if stype == "age":
            return self.a_age == int(value)
        if stype == "nb":
            return self.a_nb == int(value)
        if stype == "inst":
            return self.a_inst == self.institutions.index(value)
        if stype == "facility":
            return self.a_fac == (self.facilities.index(value) if value in self.facilities else -2)
        if stype == "zone":
            return np.isin(self.a_nb, list(value))
        return np.ones(len(self.a_id), bool)

    def case_ids(self, syn: str, stype: str, value, d0: int, d1: int) -> np.ndarray:
        m = self.syndrome_mask(syn) & (self.a_day >= d0) & (self.a_day <= d1) & self.place_mask(stype, value)
        return self.a_id[m]

    def date_of(self, day: int) -> str:
        return (self.start + timedelta(days=int(day))).isoformat()

    def delay_completeness(self, horizon: int = 30) -> dict[str, np.ndarray]:
        """Empirical share of records arrived within d days of the event, per source (last 180 days)."""
        if getattr(self, "_delay_cache", None) is not None:
            return self._delay_cache
        self._delay_cache = self._delay_completeness(horizon)
        return self._delay_cache

    def _delay_completeness(self, horizon: int) -> dict[str, np.ndarray]:
        from ..detect.epi import delay_completeness
        recent = self.ev[self.ev["day"] >= self.T - 180 - horizon]
        recent = recent[recent["day"] <= self.T - horizon]
        delay = (pd.to_datetime(recent["received_at"]) - pd.to_datetime(recent["event_date"])).dt.total_seconds() / 86400
        out = {}
        for sid, d in delay.groupby(recent["source_id"]):
            out[sid] = delay_completeness(d.values, horizon)
        return out
