"""Jurisdiction reference data: geography, census population, institutions and
the registry of connected data sources.

For the demo the reference data comes from the Riverton city model. A real
deployment loads the same tables from national statistics (census population
by small area and age), the postcode or address register, the education
ministry's institution list and the signed data-sharing agreements.
"""
from __future__ import annotations

import hashlib
import json
import secrets

import pandas as pd

from .db import get_conn, kv_set
from .synth.city import INSTITUTIONS, NEIGHBOURHOODS, SOURCES


def load_city_reference(persons: pd.DataFrame | None = None) -> dict[str, str]:
    """Load geography, population, institutions and sources. Returns plaintext source API keys."""
    con = get_conn()
    con.execute("DELETE FROM geography")
    for n in NEIGHBOURHOODS:
        con.execute("INSERT INTO geography VALUES (?,?,?,?,?,?)",
                    (n.id, n.name, n.postcode, n.centroid[0], n.centroid[1], json.dumps(n.polygon)))
    con.execute("DELETE FROM population")
    if persons is not None:
        pop = persons.groupby(["nb", "band", "sex"]).size().reset_index(name="count")
        con.executemany("INSERT INTO population VALUES (?,?,?,?)",
                        [(int(a), int(b), c, int(d)) for a, b, c, d in pop.itertuples(index=False)])
    con.execute("DELETE FROM institutions")
    enrol = persons["institution"].value_counts().to_dict() if persons is not None else {}
    for i in INSTITUTIONS:
        con.execute("INSERT INTO institutions VALUES (?,?,?,?,?)", (i.id, i.name, i.kind, i.neighbourhood_id,
                                                                     int(enrol.get(i.id, 0))))
    keys = {}
    con.execute("DELETE FROM sources")
    for s in SOURCES:
        key = "sk_" + secrets.token_urlsafe(24)
        keys[s["id"]] = key
        endpoint = {"FHIR": "/api/ingest/fhir", "HL7 ": "/api/ingest/hl7v2", "Clai": "/api/ingest/claims",
                    "Deat": "/api/ingest/deaths", "Pseu": "/api/ingest/registry"}[s["protocol"][:4]]
        notes = {"SRC-NHI": "Also the only view of patients treated at Westpark Medical Group, which has no direct feed.",
                 "SRC-ALC": "Covers deaths of insured adults, including deaths outside hospital.",
                 "SRC-EDU": "Links pseudonymised pupils and students to schools and universities."}.get(s["id"], "")
        con.execute("INSERT INTO sources(id, name, institution_type, ownership, protocol, endpoint, neighbourhood_id, "
                    "connected, api_key_hash, notes) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (s["id"], s["name"], s["institution_type"], s["ownership"], s["protocol"], endpoint,
                     s["neighbourhood_id"], 1, hashlib.sha256(key.encode()).hexdigest(), notes))
    con.commit()
    kv_set("holidays", ["01-01", "05-01", "08-15", "12-25", "12-26"])
    kv_set("academic_breaks", [["06-20", "08-25"], ["12-20", "01-05"]])
    kv_set("jurisdiction", {"name": "City of Riverton", "population": int(len(persons)) if persons is not None else None})
    return keys
