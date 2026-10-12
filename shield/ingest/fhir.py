"""FHIR R4 connector.

Accepts Bundles (as delivered by FHIR Subscriptions or Bulk Data $export
NDJSON lines) containing Patient, Encounter, Condition, DiagnosticReport and
Observation resources. Only the fields needed for surveillance are read;
names, street addresses, telecom and other identifiers are ignored and never
stored outside the encrypted raw message.
"""
from __future__ import annotations

from .model import SourceRecord

NATIONAL_ID_SYSTEMS = ("urn:oid:national-id", "https://id.gov.example/national-id")
ICD10_SYSTEMS = ("http://hl7.org/fhir/sid/icd-10-cm", "http://hl7.org/fhir/sid/icd-10")
CLASS_MAP = {"EMER": "emergency", "AMB": "outpatient", "IMP": "inpatient", "ACUTE": "inpatient", "OBSENC": "emergency"}
POSITIVE = {"10828004", "POS", "positive", "Detected", "260373001"}


class FHIRError(ValueError):
    pass


def _ref_id(ref: str | None) -> str:
    return (ref or "").split("/")[-1]


def _patient_fields(p: dict) -> tuple[str, str | None, str | None, str | None]:
    nid = None
    for ident in p.get("identifier", []):
        if ident.get("system") in NATIONAL_ID_SYSTEMS or ident.get("type", {}).get("coding", [{}])[0].get("code") == "NI":
            nid = ident.get("value")
    if not nid:
        raise FHIRError("Patient has no national identifier")
    postcode = None
    for a in p.get("address", []):
        postcode = a.get("postalCode") or postcode
    gender = {"female": "F", "male": "M"}.get(p.get("gender", ""), None)
    return nid, p.get("birthDate"), gender, postcode


def parse_bundle(bundle: dict, source_id: str, received_at: str) -> list[SourceRecord]:
    if bundle.get("resourceType") != "Bundle":
        raise FHIRError("expected a FHIR Bundle")
    res = [e.get("resource", {}) for e in bundle.get("entry", [])]
    by_type: dict[str, list[dict]] = {}
    for r in res:
        by_type.setdefault(r.get("resourceType"), []).append(r)
    patients = {p.get("id"): _patient_fields(p) for p in by_type.get("Patient", [])}
    out: list[SourceRecord] = []
    conditions: dict[str, list[str]] = {}
    for c in by_type.get("Condition", []):
        enc = _ref_id(c.get("encounter", {}).get("reference"))
        for cd in c.get("code", {}).get("coding", []):
            if cd.get("system") in ICD10_SYSTEMS:
                conditions.setdefault(enc, []).append(cd.get("code"))
    for e in by_type.get("Encounter", []):
        pid_ref = _ref_id(e.get("subject", {}).get("reference"))
        if pid_ref not in patients:
            raise FHIRError("Encounter subject not in bundle")
        nid, dob, sex, pc = patients[pid_ref]
        klass = CLASS_MAP.get(e.get("class", {}).get("code", "AMB"), "outpatient")
        hosp = e.get("hospitalization", {})
        disp = hosp.get("dischargeDisposition", {}).get("coding", [{}])[0].get("code")
        admitted = klass == "inpatient"
        icu = any("ICU" in (l.get("location", {}).get("display", "")).upper() for l in e.get("location", []))
        reason = " ".join(rc.get("text", "") for rc in e.get("reasonCode", []))
        fac = e.get("serviceProvider", {}).get("identifier", {}).get("value") or ""
        start = e.get("period", {}).get("start", "")[:10]
        out.append(SourceRecord("encounter", source_id, e.get("id"), nid, dob, sex, pc, fac, start, received_at,
                                klass, reason, conditions.get(e.get("id"), []), admitted, icu, disp == "exp"))
    for dr in by_type.get("DiagnosticReport", []):
        pid_ref = _ref_id(dr.get("subject", {}).get("reference"))
        nid, dob, sex, pc = patients[pid_ref]
        panel = dr.get("code", {}).get("text") or dr.get("code", {}).get("coding", [{}])[0].get("display", "")
        when = (dr.get("effectiveDateTime") or "")[:10]
        obs_ids = {_ref_id(r.get("reference")) for r in dr.get("result", [])}
        for o in by_type.get("Observation", []):
            if o.get("id") not in obs_ids:
                continue
            code = o.get("code", {}).get("coding", [{}])[0]
            val = o.get("valueCodeableConcept", {}).get("coding", [{}])[0].get("code", "")
            out.append(SourceRecord("lab", source_id, dr.get("encounter", {}).get("reference", ""), nid, dob, sex, pc, "",
                                    when, received_at, order_id=dr.get("id"), panel=panel, loinc=code.get("code", ""),
                                    analyte=code.get("display", ""), result="positive" if val in POSITIVE else "negative"))
    return out
