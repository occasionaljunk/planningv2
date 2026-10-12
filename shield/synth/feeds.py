"""Turn simulator output into what each institution would actually send.

* Hospitals, urgent care and the state university clinic: FHIR R4 Bundles.
* Community clinics and Harbour College: HL7 v2.5.1 ADT^A04 messages.
* Public health laboratory: FHIR DiagnosticReport + Observation Bundles.
* Insurers: daily claims CSV batches. Life insurer: death-claim CSV batches.
* Education registry: enrolment CSV.

Messages deliberately contain direct identifiers (name, national ID, street
address, date of birth) because real feeds do. The platform must strip them.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json

import numpy as np
import pandas as pd

from .city import FACILITIES, FACILITY_INDEX, FACILITY_SOURCE
from .generator import World

FIRST = ["Amara", "Ben", "Chen", "Dara", "Elif", "Farid", "Grace", "Hugo", "Ines", "Jonas", "Kai", "Lena", "Mateo", "Nia",
         "Omar", "Priya", "Quinn", "Rosa", "Sami", "Tara", "Umar", "Vera", "Wen", "Yara", "Zoe", "Liam", "Maya", "Noah"]
LAST = ["Adeyemi", "Berg", "Costa", "Dubois", "Evans", "Fischer", "Garcia", "Haddad", "Ivanov", "Jensen", "Kim", "Lopez",
        "Moreau", "Nakamura", "Okafor", "Patel", "Quist", "Rossi", "Silva", "Tanaka", "Ueda", "Varga", "Wright", "Zhang"]
STREETS = ["Harbour Rd", "Mill Lane", "Cedar Ave", "Station St", "Park Row", "Lake Dr", "Iron St", "College Way"]
PROTOCOL = {f.id: ("fhir" if f.protocol.startswith("FHIR") else "hl7v2") for f in FACILITIES}


def _iso(ts) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%dT%H:%M:%S")


def source_frames(world: World) -> dict[str, pd.DataFrame]:
    """Flatten the simulated world into per-source record frames."""
    p = world.persons
    e = world.encounters
    pi = e["person_ix"].values
    common = pd.DataFrame({
        "national_id": p["national_id"].values[pi], "dob": [d.isoformat() for d in p["dob"].values[pi]],
        "sex": p["sex"].values[pi], "postcode": p["postcode"].values[pi], "facility_id": e["facility_id"].values,
        "event_date": pd.to_datetime(e["event_dt"].values).strftime("%Y-%m-%d"), "event_dt": e["event_dt"].values,
        "encounter_class": e["encounter_class"].values, "admitted": e["admitted"].values, "icu": e["icu"].values,
        "died": e["died"].values,
    })
    connected = np.array([FACILITIES[FACILITY_INDEX[f]].connected for f in e["facility_id"].values])
    ehr = common.loc[connected].copy()
    ehr["record_type"] = "encounter"
    ehr["source_id"] = [FACILITY_SOURCE[f] for f in ehr["facility_id"].values]
    ehr["format"] = [PROTOCOL[f] for f in ehr["facility_id"].values]
    ehr["encounter_id"] = e["encounter_id"].values[connected]
    ehr["chief_complaint"] = e["chief_complaint"].values[connected]
    ehr["icd10"] = e["icd10"].values[connected]
    ehr["received_at"] = pd.to_datetime(e["ehr_received_at"].values[connected]).strftime("%Y-%m-%dT%H:%M:%S")

    c = world.claims
    rows = c["enc_row"].values
    claims = common.iloc[rows].reset_index(drop=True)
    claims["record_type"] = "claim"
    claims["source_id"] = c["source_id"].values
    claims["format"] = "claims_csv"
    claims["encounter_id"] = c["claim_id"].values
    claims["chief_complaint"] = ""
    claims["icd10"] = e["icd10_billing"].values[rows]
    claims["received_at"] = pd.to_datetime(c["received_at"].values).strftime("%Y-%m-%dT%H:%M:%S")

    d = world.death_claims
    dpi = d["person_ix"].values
    from .generator import Calendar
    cal = Calendar(world.config.start, world.config.end)
    deaths = pd.DataFrame({
        "national_id": p["national_id"].values[dpi], "dob": [x.isoformat() for x in p["dob"].values[dpi]],
        "sex": p["sex"].values[dpi], "postcode": p["postcode"].values[dpi],
        "facility_id": np.where(d["in_hospital"].values, "HOSPITAL", "COMMUNITY"),
        "event_date": cal.days[d["day_ix"].values].strftime("%Y-%m-%d"),
        "event_dt": cal.days[d["day_ix"].values].values,
        "encounter_class": "death", "admitted": False, "icu": False, "died": True,
        "record_type": "death", "source_id": "SRC-ALC", "format": "death_csv", "encounter_id": d["claim_id"].values,
        "chief_complaint": "", "icd10": np.where(d["cause_known"].values, d["cause_icd10"].values, ""),
        "received_at": pd.to_datetime(d["received_at"].values).strftime("%Y-%m-%dT%H:%M:%S"),
    })

    l = world.labs
    lpi = l["person_ix"].values
    labs = pd.DataFrame({
        "record_type": "lab", "source_id": "SRC-LAB", "format": "fhir_lab", "national_id": p["national_id"].values[lpi],
        "dob": [x.isoformat() for x in p["dob"].values[lpi]], "sex": p["sex"].values[lpi],
        "postcode": p["postcode"].values[lpi], "encounter_id": l["encounter_id"].values, "order_id": l["order_id"].values,
        "event_date": cal.days[l["day_ix"].values].strftime("%Y-%m-%d"), "panel": l["panel"].values,
        "loinc": l["loinc"].values, "analyte": l["analyte"].values, "result": l["result"].values,
        "received_at": pd.to_datetime(l["received_at"].values).strftime("%Y-%m-%dT%H:%M:%S"),
    })
    registry = world.enrolment.rename(columns={"institution": "institution_id"})
    return {"ehr": ehr.reset_index(drop=True), "claims": claims, "deaths": deaths, "labs": labs, "registry": registry}


# --------------------------------------------------------------------------- renderers
def _name(nid: str) -> tuple[str, str]:
    h = int(hashlib.sha256(nid.encode()).hexdigest()[:8], 16)
    return FIRST[h % len(FIRST)], LAST[(h // 97) % len(LAST)]


def _street(nid: str) -> str:
    h = int(hashlib.sha256(nid.encode()).hexdigest()[8:16], 16)
    return f"{h % 200 + 1} {STREETS[h % len(STREETS)]}"


def render_fhir_encounter(r) -> str:
    given, family = _name(r.national_id)
    pid = "pat-" + hashlib.sha256(r.national_id.encode()).hexdigest()[:10]
    klass = {"emergency": "EMER", "outpatient": "AMB", "inpatient": "IMP"}[r.encounter_class]
    enc = {"resourceType": "Encounter", "id": r.encounter_id, "status": "finished",
           "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": klass},
           "subject": {"reference": f"Patient/{pid}"},
           "period": {"start": _iso(r.event_dt)},
           "serviceProvider": {"identifier": {"system": "urn:oid:facility-registry", "value": r.facility_id}},
           "reasonCode": [{"text": r.chief_complaint}] if r.chief_complaint else []}
    if r.encounter_class == "inpatient":
        enc["hospitalization"] = {"dischargeDisposition": {"coding": [{"code": "exp" if r.died else "home"}]}}
    if r.icu:
        enc["location"] = [{"location": {"display": "ICU"}}]
    entries = [
        {"resource": {"resourceType": "Patient", "id": pid,
                      "identifier": [{"system": "urn:oid:national-id", "value": r.national_id},
                                     {"system": "urn:oid:mrn", "value": "MRN" + r.national_id[-6:]}],
                      "name": [{"family": family, "given": [given]}], "gender": "female" if r.sex == "F" else "male",
                      "birthDate": r.dob, "telecom": [{"system": "phone", "value": "+000 555 0100"}],
                      "address": [{"line": [_street(r.national_id)], "city": "Riverton", "postalCode": r.postcode}]}},
        {"resource": enc},
    ]
    for k, code in enumerate([c for c in (r.icd10 or "").split("|") if c]):
        entries.append({"resource": {"resourceType": "Condition", "id": f"{r.encounter_id}-c{k}",
                                     "subject": {"reference": f"Patient/{pid}"},
                                     "encounter": {"reference": f"Encounter/{r.encounter_id}"},
                                     "code": {"coding": [{"system": "http://hl7.org/fhir/sid/icd-10-cm", "code": code}]}}})
    return json.dumps({"resourceType": "Bundle", "type": "collection", "entry": entries})


def render_hl7(r) -> str:
    given, family = _name(r.national_id)
    ts = pd.Timestamp(r.event_dt).strftime("%Y%m%d%H%M%S")
    fac = r.facility_id
    klass = {"emergency": "E", "outpatient": "O", "inpatient": "I"}[r.encounter_class]
    poc = "ICU" if r.icu else ("ED" if klass == "E" else "OPD")
    pv1 = ["PV1", "1", klass, f"{poc}^^^{fac}"] + [""] * 15 + [r.encounter_id] + [""] * 16 + ["20" if r.died else "01"] + \
          [""] * 7 + [ts]
    segs = [
        f"MSH|^~\\&|EHR|{fac}|SHIELD|RPHA|{ts}||ADT^A04^ADT_A01|{r.encounter_id}|P|2.5.1",
        f"EVN|A04|{ts}",
        f"PID|1||{r.national_id}^^^RVNID^NI~MRN{r.national_id[-6:]}^^^{fac}^MR||{family}^{given}||"
        f"{r.dob.replace('-', '')}|{r.sex}|||{_street(r.national_id)}^^Riverton^^{r.postcode}^RV",
        "|".join(pv1),
        f"PV2|||^{r.chief_complaint}",
    ]
    for k, code in enumerate([c for c in (r.icd10 or "").split("|") if c]):
        segs.append(f"DG1|{k + 1}||{code}^^I10C||{ts[:8]}|W")
    return "\r".join(segs)


def render_lab_bundle(rows: pd.DataFrame) -> str:
    r0 = rows.iloc[0]
    given, family = _name(r0.national_id)
    pid = "pat-" + hashlib.sha256(r0.national_id.encode()).hexdigest()[:10]
    obs = []
    for k, r in enumerate(rows.itertuples()):
        code = "10828004" if r.result == "positive" else "260385009"
        obs.append({"resourceType": "Observation", "id": f"{r.order_id}-o{k}", "status": "final",
                    "code": {"coding": [{"system": "http://loinc.org", "code": r.loinc, "display": r.analyte}]},
                    "subject": {"reference": f"Patient/{pid}"}, "effectiveDateTime": r.event_date,
                    "valueCodeableConcept": {"coding": [{"system": "http://snomed.info/sct", "code": code,
                                                         "display": "Positive" if r.result == "positive" else "Negative"}]}})
    report = {"resourceType": "DiagnosticReport", "id": r0.order_id, "status": "final", "code": {"text": r0.panel},
              "subject": {"reference": f"Patient/{pid}"}, "encounter": {"reference": f"Encounter/{r0.encounter_id}"},
              "effectiveDateTime": r0.event_date, "result": [{"reference": f"Observation/{o['id']}"} for o in obs]}
    patient = {"resourceType": "Patient", "id": pid, "identifier": [{"system": "urn:oid:national-id", "value": r0.national_id}],
               "name": [{"family": family, "given": [given]}], "gender": "female" if r0.sex == "F" else "male",
               "birthDate": r0.dob, "address": [{"postalCode": r0.postcode}]}
    return json.dumps({"resourceType": "Bundle", "type": "collection",
                       "entry": [{"resource": x} for x in [patient, report] + obs]})


def render_claims_csv(rows: pd.DataFrame) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["claim_id", "member_national_id", "member_name", "member_dob", "member_sex", "member_postcode",
                "provider_facility_id", "service_date", "place_of_service", "diagnosis_codes", "admitted",
                "discharge_status", "billed_amount"])
    for r in rows.itertuples():
        pos = {"emergency": "23", "inpatient": "21", "outpatient": "11"}[r.encounter_class]
        g, f = _name(r.national_id)
        w.writerow([r.encounter_id, r.national_id, f"{f}, {g}", r.dob, r.sex, r.postcode, r.facility_id, r.event_date, pos,
                    r.icd10, int(r.admitted), "20" if r.died else "01", f"{(hash(r.encounter_id) % 90000) / 100 + 80:.2f}"])
    return buf.getvalue()


def render_death_csv(rows: pd.DataFrame) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["claim_id", "policy_number", "insured_national_id", "dob", "sex", "postcode", "date_of_death",
                "cause_icd10", "place_of_death"])
    for r in rows.itertuples():
        w.writerow([r.encounter_id, "POL" + r.national_id[-7:], r.national_id, r.dob, r.sex, r.postcode, r.event_date,
                    r.icd10, "hospital" if r.facility_id == "HOSPITAL" else "community"])
    return buf.getvalue()


def render_registry_csv(rows: pd.DataFrame) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["national_id", "institution_id", "academic_year"])
    for r in rows.itertuples():
        w.writerow([r.national_id, r.institution_id, "2026/27"])
    return buf.getvalue()


def messages_for_window(frames: dict[str, pd.DataFrame], start: str, end: str):
    """Yield (received_at, source_id, format, payload) for everything received in (start, end]."""
    out = []
    ehr = frames["ehr"]
    m = (ehr["received_at"] > start) & (ehr["received_at"] <= end)
    for r in ehr.loc[m].itertuples():
        payload = render_fhir_encounter(r) if r.format == "fhir" else render_hl7(r)
        out.append((r.received_at, r.source_id, r.format, payload))
    labs = frames["labs"]
    m = (labs["received_at"] > start) & (labs["received_at"] <= end)
    for _, grp in labs.loc[m].groupby("order_id", sort=False):
        out.append((grp["received_at"].iloc[0], "SRC-LAB", "fhir", render_lab_bundle(grp)))
    # batch files are delivered once per day per source
    for key, fmt, renderer in (("claims", "claims_csv", render_claims_csv), ("deaths", "death_csv", render_death_csv)):
        df = frames[key]
        m = (df["received_at"] > start) & (df["received_at"] <= end)
        sub = df.loc[m]
        if sub.empty:
            continue
        sub = sub.assign(batch_day=sub["received_at"].str[:10])
        for (sid, day), grp in sub.groupby(["source_id", "batch_day"]):
            out.append((f"{day}T23:30:00" if f"{day}T23:30:00" <= end else end, sid, fmt, renderer(grp)))
    out.sort(key=lambda x: x[0])
    return out
