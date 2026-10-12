import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(db, small_world):
    from shield.api.main import app
    from shield.db import kv_set
    kv_set("clock", "2026-02-28")
    with TestClient(app) as c:
        yield c


def _login(c, user):
    r = c.post("/api/auth/login", json={"username": user, "password": "shield-demo"})
    assert r.status_code == 200
    return {"Authorization": "Bearer " + r.json()["token"]}


def test_auth_and_security_headers(client):
    assert client.get("/api/overview").status_code == 401
    assert client.post("/api/auth/login", json={"username": "epi", "password": "bad"}).status_code == 401
    r = client.get("/")
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"


def test_role_based_access(client):
    aud = _login(client, "auditor")
    assert client.get("/api/overview", headers=aud).status_code == 403
    assert client.get("/api/audit", headers=aud).status_code == 200
    mini = _login(client, "minister")
    assert client.get("/api/overview", headers=mini).status_code == 200
    assert client.get("/api/alerts/1/cases", headers=mini).status_code == 403
    assert client.get("/api/raw/1?justification=checking+a+record", headers=_login(client, "epi")).status_code == 403


def test_institution_ingestion_requires_source_credentials(client, db, small_world):
    from shield.synth.feeds import render_fhir_encounter, source_frames
    fr = source_frames(small_world)
    row = fr["ehr"][fr["ehr"]["source_id"] == "SRC-RGH"].iloc[0]
    body = render_fhir_encounter(row)
    bad = client.post("/api/ingest/fhir", content=body, headers={"X-Source-Id": "SRC-RGH", "X-Source-Key": "wrong"})
    assert bad.status_code == 401
    ok = client.post("/api/ingest/fhir", content=body,
                     headers={"X-Source-Id": "SRC-RGH", "X-Source-Key": db["keys"]["SRC-RGH"]})
    assert ok.status_code == 200 and ok.json()["events"] == 1
    malformed = client.post("/api/ingest/fhir", content=json.dumps({"resourceType": "Patient"}),
                            headers={"X-Source-Id": "SRC-RGH", "X-Source-Key": db["keys"]["SRC-RGH"]})
    assert malformed.status_code == 422


def test_security_posture_and_egress_test(client):
    h = _login(client, "steward")
    p = client.get("/api/security/posture", headers=h).json()
    assert p["audit_chain"]["valid"]
    assert any(c["check"].startswith("Egress guard") for c in p["checks"])
    r = client.post("/api/security/egress-test", json={"url": "https://exfil.example.net"}, headers=h).json()
    assert r["blocked"]
    assert client.get("/api/audit/verify", headers=h).json()["valid"]
