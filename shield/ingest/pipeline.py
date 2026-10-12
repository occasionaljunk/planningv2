"""Ingestion pipeline: parse -> encrypt raw -> pseudonymise -> minimise ->
classify -> de-duplicate -> store.

The same transformation functions are used for live messages (one at a time
through the connectors) and for the initial historical bulk load, so both
paths produce identical records.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date

import numpy as np
import pandas as pd

from ..db import get_conn, write_lock
from ..nlp.syndromes import classify
from ..security import crypto
from ..security.pseudonym import Pseudonymiser, age_band
from . import claims as claims_mod
from . import fhir, hl7v2
from .model import SourceRecord

EVENT_COLS = ["source_id", "raw_id", "record_type", "encounter_id", "encounter_key", "patient_pid", "facility_id",
              "event_date", "received_at", "age", "age_band", "sex", "neighbourhood_id", "encounter_class",
              "chief_complaint", "icd10", "syndrome", "severe_resp", "admitted", "icu", "died", "is_primary",
              "linked_event_id"]
LAB_COLS = ["source_id", "raw_id", "patient_pid", "order_id", "collected_date", "received_at", "panel", "loinc",
            "analyte", "result", "age_band", "neighbourhood_id"]


def _age(dob: str | None, on: str) -> int | None:
    if not dob:
        return None
    try:
        b, d = date.fromisoformat(dob[:10]), date.fromisoformat(on[:10])
    except ValueError:
        return None
    return d.year - b.year - ((d.month, d.day) < (b.month, b.day))


class IngestPipeline:
    def __init__(self, con: sqlite3.Connection | None = None):
        self.con = con or get_conn()
        self.ps = Pseudonymiser()
        self.postcodes = {r[0]: r[1] for r in self.con.execute("SELECT postcode_prefix, neighbourhood_id FROM geography")}

    # ------------------------------------------------------------------ helpers
    def neighbourhood(self, postcode: str | None) -> int | None:
        if not postcode:
            return None
        return self.postcodes.get(postcode.split("-")[0].split(" ")[0].upper())

    def store_raw(self, source_id: str, fmt: str, payload: bytes, received_at: str) -> int:
        env = crypto.encrypt(payload, aad=source_id.encode())
        cur = self.con.execute(
            "INSERT INTO raw_messages(source_id, received_at, format, key_id, wrapped_dek, nonce, ciphertext, sha256) "
            "VALUES (?,?,?,?,?,?,?,?)", (source_id, received_at, fmt, env.key_id, env.wrapped_dek, env.nonce,
                                         env.ciphertext, env.sha256))
        return cur.lastrowid

    def parse(self, source_id: str, fmt: str, payload: str, received_at: str) -> list[SourceRecord]:
        if fmt == "fhir":
            return fhir.parse_bundle(json.loads(payload), source_id, received_at)
        if fmt == "hl7v2":
            return hl7v2.parse_message(payload, source_id, received_at)
        if fmt == "claims_csv":
            return claims_mod.parse_claims(payload, source_id, received_at)
        if fmt == "death_csv":
            return claims_mod.parse_death_claims(payload, source_id, received_at)
        raise ValueError(f"unknown format {fmt}")

    # ------------------------------------------------------------------ live path
    def ingest(self, source_id: str, fmt: str, payload: str, received_at: str, commit: bool = True) -> dict:
        """Process one message. ``commit=False`` lets a batch loader commit once per batch."""
        if fmt == "registry_csv":
            return self.ingest_registry(source_id, payload, received_at)
        records = self.parse(source_id, fmt, payload, received_at)
        with write_lock():
            raw_id = self.store_raw(source_id, fmt, payload.encode(), received_at)
            n_ev = n_lab = n_dup = 0
            for rec in records:
                if rec.record_type == "lab":
                    self._store_lab(rec, raw_id)
                    n_lab += 1
                else:
                    if self._store_event(rec, raw_id):
                        n_ev += 1
                    else:
                        n_dup += 1
            self.con.execute("UPDATE sources SET records_received = records_received + ?, last_received_at = ? "
                             "WHERE id = ?", (len(records), received_at, source_id))
            if commit:
                self.con.commit()
        return {"raw_id": raw_id, "events": n_ev, "labs": n_lab, "duplicates_ignored": n_dup}

    def ingest_registry(self, source_id: str, payload: str, received_at: str) -> dict:
        pairs = claims_mod.parse_registry(payload)
        with write_lock():
            raw_id = self.store_raw(source_id, "registry_csv", payload.encode(), received_at)
            self.con.executemany("INSERT OR IGNORE INTO enrolment(patient_pid, institution_id) VALUES (?, ?)",
                                 [(self.ps.pid(n), i) for n, i in pairs])
            self.con.execute("UPDATE sources SET records_received = records_received + ?, last_received_at = ? "
                             "WHERE id = ?", (len(pairs), received_at, source_id))
            self.con.commit()
        return {"raw_id": raw_id, "enrolments": len(pairs)}

    def _event_row(self, rec: SourceRecord, raw_id: int | None) -> dict:
        pid = self.ps.pid(rec.national_id)
        age = _age(rec.dob, rec.event_date)
        icd = "|".join(rec.icd10)
        syndrome, severe = classify(rec.chief_complaint or "", icd, bool(rec.admitted))
        key = (self.ps.encounter_key(pid, "DEATH", "") if rec.record_type == "death"
               else self.ps.encounter_key(pid, rec.event_date, rec.facility_id))
        return {"source_id": rec.source_id, "raw_id": raw_id, "record_type": rec.record_type,
                "encounter_id": rec.encounter_id, "encounter_key": key, "patient_pid": pid,
                "facility_id": rec.facility_id, "event_date": rec.event_date, "received_at": rec.received_at,
                "age": age, "age_band": age_band(age), "sex": rec.sex, "neighbourhood_id": self.neighbourhood(rec.postcode),
                "encounter_class": rec.encounter_class, "chief_complaint": (rec.chief_complaint or "")[:200],
                "icd10": icd, "syndrome": syndrome, "severe_resp": int(severe), "admitted": int(rec.admitted),
                "icu": int(rec.icu), "died": int(rec.died), "is_primary": 1, "linked_event_id": None}

    def _store_event(self, rec: SourceRecord, raw_id: int | None) -> bool:
        row = self._event_row(rec, raw_id)
        con = self.con
        if rec.record_type == "claim":
            hit = con.execute("SELECT id FROM events WHERE encounter_key=? AND record_type='encounter' LIMIT 1",
                              (row["encounter_key"],)).fetchone()
            if hit:
                row["is_primary"], row["linked_event_id"] = 0, hit[0]
        elif rec.record_type == "death":
            hit = con.execute("SELECT id FROM events WHERE patient_pid=? AND died=1 AND record_type='encounter' LIMIT 1",
                              (row["patient_pid"],)).fetchone()
            if hit:
                row["is_primary"], row["linked_event_id"] = 0, hit[0]
        cur = con.execute(f"INSERT OR IGNORE INTO events({','.join(EVENT_COLS)}) VALUES ({','.join('?' * len(EVENT_COLS))})",
                          [row[c] for c in EVENT_COLS])
        if cur.rowcount == 0:
            return False
        new_id = cur.lastrowid
        if rec.record_type == "encounter":
            # a claim may have arrived first: the EHR record becomes the primary record
            con.execute("UPDATE events SET is_primary=0, linked_event_id=? WHERE encounter_key=? AND record_type='claim' "
                        "AND is_primary=1", (new_id, row["encounter_key"]))
            if rec.died:
                con.execute("UPDATE events SET is_primary=0, linked_event_id=? WHERE patient_pid=? AND record_type='death' "
                            "AND is_primary=1", (new_id, row["patient_pid"]))
        return True

    def _store_lab(self, rec: SourceRecord, raw_id: int | None) -> None:
        pid = self.ps.pid(rec.national_id)
        age = _age(rec.dob, rec.event_date)
        self.con.execute(f"INSERT INTO labs({','.join(LAB_COLS)}) VALUES ({','.join('?' * len(LAB_COLS))})",
                         (rec.source_id, raw_id, pid, rec.order_id, rec.event_date, rec.received_at, rec.panel, rec.loinc,
                          rec.analyte, rec.result, age_band(age), self.neighbourhood(rec.postcode)))

    # ------------------------------------------------------------------ bulk path
    def bulk_events(self, df: pd.DataFrame, progress=print) -> int:
        """Vectorised load of normalised source records (historical back-fill).

        ``df`` has the SourceRecord columns (national_id, dob, postcode, icd10 as
        a pipe-joined string, ...). The transformation matches the live path.
        """
        if df.empty:
            return 0
        ps = self.ps
        pid_cache: dict[str, str] = {}
        nid = df["national_id"].values
        pids = np.empty(len(df), dtype=object)
        for i, n in enumerate(nid):
            p = pid_cache.get(n)
            if p is None:
                p = pid_cache[n] = ps.pid(n)
            pids[i] = p
        out = pd.DataFrame({"source_id": df["source_id"].values, "raw_id": None,
                            "record_type": df["record_type"].values, "encounter_id": df["encounter_id"].values,
                            "patient_pid": pids, "facility_id": df["facility_id"].values,
                            "event_date": df["event_date"].values, "received_at": df["received_at"].values})
        dob = pd.to_datetime(df["dob"].values)
        ev = pd.to_datetime(df["event_date"].values)
        age = ev.year - dob.year - ((ev.month < dob.month) | ((ev.month == dob.month) & (ev.day < dob.day))).astype(int)
        out["age"] = np.asarray(age, dtype=int)
        out["age_band"] = np.digitize(out["age"].values, [5, 12, 18, 25, 45, 65, 75])
        out["sex"] = df["sex"].values
        pref = pd.Series(df["postcode"].values).str.split("-").str[0]
        out["neighbourhood_id"] = pref.map(self.postcodes).values
        out["encounter_class"] = df["encounter_class"].values
        out["chief_complaint"] = df["chief_complaint"].fillna("").values
        out["icd10"] = df["icd10"].fillna("").values
        out["admitted"] = df["admitted"].astype(int).values
        out["icu"] = df["icu"].astype(int).values
        out["died"] = df["died"].astype(int).values
        progress("[ingest] classifying syndromes")
        combos = out[["chief_complaint", "icd10", "admitted"]].drop_duplicates()
        res = {(a, b, c): classify(a, b, bool(c)) for a, b, c in combos.itertuples(index=False)}
        keys = list(zip(out["chief_complaint"], out["icd10"], out["admitted"]))
        out["syndrome"] = [res[k][0] for k in keys]
        out["severe_resp"] = [int(res[k][1]) for k in keys]
        progress("[ingest] computing de-duplication keys")
        out["encounter_key"] = [ps.encounter_key(p, "DEATH", "") if t == "death" else ps.encounter_key(p, d, f)
                                for p, t, d, f in zip(out["patient_pid"], out["record_type"], out["event_date"],
                                                      out["facility_id"])]
        out["is_primary"] = 1
        out["linked_event_id"] = None
        enc_keys = set(out.loc[out["record_type"] == "encounter", "encounter_key"])
        enc_keys |= {r[0] for r in self.con.execute("SELECT encounter_key FROM events WHERE record_type='encounter'")}
        out.loc[(out["record_type"] == "claim") & out["encounter_key"].isin(enc_keys), "is_primary"] = 0
        died_pids = set(out.loc[(out["record_type"] == "encounter") & (out["died"] == 1), "patient_pid"])
        out.loc[(out["record_type"] == "death") & out["patient_pid"].isin(died_pids), "is_primary"] = 0
        progress(f"[ingest] writing {len(out):,} records")
        with write_lock():
            self.con.executemany(
                f"INSERT OR IGNORE INTO events({','.join(EVENT_COLS)}) VALUES ({','.join('?' * len(EVENT_COLS))})",
                out[EVENT_COLS].astype(object).where(out[EVENT_COLS].notna(), None).itertuples(index=False, name=None))
            counts = out.groupby("source_id").agg(n=("encounter_id", "size"), last=("received_at", "max"))
            for sid, r in counts.iterrows():
                self.con.execute("UPDATE sources SET records_received = records_received + ?, last_received_at = "
                                 "MAX(COALESCE(last_received_at, ''), ?) WHERE id=?", (int(r["n"]), r["last"], sid))
            self.con.commit()
        return len(out)

    def bulk_labs(self, df: pd.DataFrame) -> int:
        if df.empty:
            return 0
        ps = self.ps
        cache: dict[str, str] = {}
        pids = [cache.setdefault(n, ps.pid(n)) if n not in cache else cache[n] for n in df["national_id"].values]
        dob = pd.to_datetime(df["dob"].values)
        ev = pd.to_datetime(df["event_date"].values)
        age = ev.year - dob.year - ((ev.month < dob.month) | ((ev.month == dob.month) & (ev.day < dob.day))).astype(int)
        bands = np.digitize(np.asarray(age), [5, 12, 18, 25, 45, 65, 75])
        nb = pd.Series(df["postcode"].values).str.split("-").str[0].map(self.postcodes).values
        rows = zip(df["source_id"].values, [None] * len(df), pids, df["order_id"].values, df["event_date"].values,
                   df["received_at"].values, df["panel"].values, df["loinc"].values, df["analyte"].values,
                   df["result"].values, [int(b) for b in bands], [None if pd.isna(x) else int(x) for x in nb])
        with write_lock():
            self.con.executemany(f"INSERT INTO labs({','.join(LAB_COLS)}) VALUES ({','.join('?' * len(LAB_COLS))})", rows)
            self.con.execute("UPDATE sources SET records_received = records_received + ?, last_received_at = ? WHERE id=?",
                             (len(df), df["received_at"].max(), "SRC-LAB"))
            self.con.commit()
        return len(df)
