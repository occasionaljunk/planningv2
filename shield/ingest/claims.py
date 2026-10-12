"""Batch-file connectors: health-insurance claims, life-insurance death claims
and the education registry. Files arrive daily over SFTP in production."""
from __future__ import annotations

import csv
import io

from .model import SourceRecord

CLAIM_COLUMNS = ["claim_id", "member_national_id", "member_dob", "member_sex", "member_postcode", "provider_facility_id",
                 "service_date", "place_of_service", "diagnosis_codes", "admitted", "discharge_status"]
DEATH_COLUMNS = ["claim_id", "insured_national_id", "dob", "sex", "postcode", "date_of_death", "cause_icd10",
                 "place_of_death"]
POS_CLASS = {"23": "emergency", "21": "inpatient", "11": "outpatient", "20": "outpatient", "22": "outpatient"}


class BatchError(ValueError):
    pass


def _reader(text: str, required: list[str]):
    rdr = csv.DictReader(io.StringIO(text))
    missing = [c for c in required if c not in (rdr.fieldnames or [])]
    if missing:
        raise BatchError(f"missing columns: {missing}")
    return rdr


def parse_claims(text: str, source_id: str, received_at: str) -> list[SourceRecord]:
    out = []
    for r in _reader(text, CLAIM_COLUMNS):
        klass = POS_CLASS.get(r["place_of_service"], "outpatient")
        admitted = r["admitted"] in ("1", "Y", "true")
        out.append(SourceRecord("claim", source_id, r["claim_id"], r["member_national_id"], r["member_dob"] or None,
                                r["member_sex"] or None, r["member_postcode"] or None, r["provider_facility_id"],
                                r["service_date"], received_at, "inpatient" if admitted else klass, "",
                                [c for c in r["diagnosis_codes"].split("|") if c], admitted, False,
                                r["discharge_status"] == "20"))
    return out


def parse_death_claims(text: str, source_id: str, received_at: str) -> list[SourceRecord]:
    out = []
    for r in _reader(text, DEATH_COLUMNS):
        out.append(SourceRecord("death", source_id, r["claim_id"], r["insured_national_id"], r["dob"] or None,
                                r["sex"] or None, r["postcode"] or None, "COMMUNITY" if r["place_of_death"] != "hospital"
                                else "HOSPITAL", r["date_of_death"], received_at, "death", "",
                                [r["cause_icd10"]] if r["cause_icd10"] else [], False, False, True))
    return out


def parse_registry(text: str) -> list[tuple[str, str]]:
    return [(r["national_id"], r["institution_id"]) for r in _reader(text, ["national_id", "institution_id"])]
