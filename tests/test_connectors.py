import json

import pandas as pd

from shield.ingest import fhir, hl7v2
from shield.ingest.claims import parse_claims, parse_death_claims
from shield.ingest.pipeline import IngestPipeline
from shield.synth.feeds import (render_claims_csv, render_death_csv, render_fhir_encounter, render_hl7, render_lab_bundle,
                                source_frames)


def _frames(small_world):
    return source_frames(small_world)


def test_fhir_and_hl7_roundtrip(small_world):
    fr = _frames(small_world)
    ehr = fr["ehr"]
    for fmt, render, parse in (("fhir", render_fhir_encounter, lambda p: fhir.parse_bundle(json.loads(p), "S", "t")),
                               ("hl7v2", render_hl7, lambda p: hl7v2.parse_message(p, "S", "t"))):
        rows = ehr[ehr["format"] == fmt].head(50)
        for r in rows.itertuples():
            rec = parse(render(r))[0]
            assert rec.national_id == r.national_id and rec.event_date == r.event_date
            assert rec.facility_id == r.facility_id and rec.postcode == r.postcode and rec.dob == r.dob
            assert rec.encounter_class == r.encounter_class and rec.admitted == bool(r.admitted)
            assert "|".join(rec.icd10) == (r.icd10 or "")
            assert rec.chief_complaint == (r.chief_complaint or "")


def test_lab_claims_and_death_batches(small_world):
    fr = _frames(small_world)
    order = fr["labs"]["order_id"].iloc[0]
    grp = fr["labs"][fr["labs"]["order_id"] == order]
    recs = fhir.parse_bundle(json.loads(render_lab_bundle(grp)), "SRC-LAB", "t")
    assert len(recs) == len(grp) and {r.result for r in recs} <= {"positive", "negative"}
    cl = fr["claims"].head(20)
    parsed = parse_claims(render_claims_csv(cl), "SRC-NHI", "t")
    assert [p.encounter_id for p in parsed] == list(cl["encounter_id"])
    d = fr["deaths"].head(5)
    assert len(parse_death_claims(render_death_csv(d), "SRC-ALC", "t")) == len(d)


def test_pipeline_minimises_encrypts_and_deduplicates(db, small_world):
    con = db["con"]
    fr = _frames(small_world)
    pipe = IngestPipeline(con)
    enc = fr["ehr"][fr["ehr"]["format"] == "hl7v2"].iloc[0]
    res = pipe.ingest(enc.source_id, "hl7v2", render_hl7(enc), "2026-01-15T10:00:00")
    assert res["events"] == 1
    # same message twice is idempotent
    assert pipe.ingest(enc.source_id, "hl7v2", render_hl7(enc), "2026-01-15T10:05:00")["duplicates_ignored"] == 1
    row = con.execute("SELECT * FROM events WHERE encounter_id=?", (enc.encounter_id,)).fetchone()
    stored = " ".join(str(v) for v in dict(row).values())
    assert enc.national_id not in stored and enc.dob not in stored and enc.postcode not in stored
    raw = con.execute("SELECT ciphertext FROM raw_messages WHERE id=?", (row["raw_id"],)).fetchone()[0]
    assert enc.national_id.encode() not in raw
    # an insurer claim for the same visit is linked, not double counted
    claims = fr["claims"]
    match = claims[(claims["national_id"] == enc.national_id) & (claims["event_date"] == enc.event_date) &
                   (claims["facility_id"] == enc.facility_id)]
    if len(match):
        pipe.ingest(match.iloc[0].source_id, "claims_csv", render_claims_csv(match.head(1)), "2026-01-25T23:30:00")
        c = con.execute("SELECT is_primary, linked_event_id FROM events WHERE encounter_id=?",
                        (match.iloc[0].encounter_id,)).fetchone()
        assert c["is_primary"] == 0 and c["linked_event_id"] == row["id"]
