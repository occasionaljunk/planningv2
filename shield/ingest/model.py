"""Normalised record produced by every connector before pseudonymisation."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SourceRecord:
    record_type: str                 # encounter | claim | death | lab
    source_id: str
    encounter_id: str
    national_id: str
    dob: str | None
    sex: str | None
    postcode: str | None
    facility_id: str
    event_date: str                  # YYYY-MM-DD
    received_at: str                 # ISO timestamp
    encounter_class: str = "outpatient"
    chief_complaint: str = ""
    icd10: list[str] = field(default_factory=list)
    admitted: bool = False
    icu: bool = False
    died: bool = False
    # laboratory fields
    order_id: str = ""
    panel: str = ""
    loinc: str = ""
    analyte: str = ""
    result: str = ""
