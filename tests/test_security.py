import socket

import pytest

from shield.security import audit, crypto
from shield.security.auth import can, issue_token, login, verify_password, hash_password, verify_token
from shield.security.disclosure import suppress
from shield.security.pseudonym import Pseudonymiser, age_band
from shield.security.residency import GUARD, EgressBlocked, run_checks, try_outbound


def test_envelope_encryption_roundtrip_and_tamper():
    env = crypto.encrypt(b"PID|1||RV123456789^^^RVNID^NI", aad=b"SRC-NCH")
    assert crypto.decrypt(env, aad=b"SRC-NCH").startswith(b"PID")
    assert b"RV123456789" not in env.ciphertext
    with pytest.raises(Exception):
        crypto.decrypt(env, aad=b"SRC-OTHER")  # bound to its source
    bad = crypto.Envelope(env.key_id, env.wrapped_dek, env.nonce, env.ciphertext[:-1] + bytes([env.ciphertext[-1] ^ 1]), env.sha256)
    with pytest.raises(Exception):
        crypto.decrypt(bad, aad=b"SRC-NCH")


def test_pseudonyms_are_stable_keyed_and_normalised():
    ps = Pseudonymiser()
    a = ps.pid("RV123456789")
    assert a == ps.pid("rv-123 456 789")
    assert a != ps.pid("RV123456788")
    assert "123456789" not in a and len(a) == 32
    assert Pseudonymiser(b"x" * 32).pid("RV123456789") != a  # depends on the secret key


def test_age_bands():
    assert [age_band(a) for a in (0, 4, 5, 11, 12, 17, 18, 24, 25, 44, 45, 64, 65, 74, 75, 99)] == \
        [0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7]


def test_audit_chain_detects_tampering(db):
    con = db["con"]
    audit.record("tester", "epidemiologist", "view.alert", "alert:1", {"x": 1})
    audit.record("tester", "epidemiologist", "export.line_list", "alert:1", {"rows": 3})
    assert audit.verify_chain(con)["valid"]
    last = con.execute("SELECT id FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()[0]
    con.execute("UPDATE audit_log SET details='{\"rows\": 0}' WHERE id=?", (last,))
    con.commit()
    res = audit.verify_chain(con)
    assert not res["valid"] and res["broken_at"] == last
    con.execute("DELETE FROM audit_log WHERE id=?", (last,))  # restore a valid chain for later tests
    con.commit()
    assert audit.verify_chain(con)["valid"]


def test_passwords_tokens_and_roles(db):
    h = hash_password("correct horse")
    assert verify_password("correct horse", h) and not verify_password("wrong", h)
    assert login("epi", "shield-demo")["role"] == "epidemiologist"
    assert login("epi", "nope") is None
    t = issue_token("epi", "epidemiologist")
    assert verify_token(t)["sub"] == "epi"
    assert verify_token(t[:-2] + "xx") is None
    assert can("epidemiologist", "view_line_list") and not can("decision_maker", "view_line_list")
    assert not can("auditor", "view_dashboard") and can("auditor", "view_audit")
    assert can("data_steward", "view_raw_message") and not can("epidemiologist", "view_raw_message")


def test_small_cell_suppression():
    assert suppress(3, "decision_maker") == "<5"
    assert suppress(0, "decision_maker") == 0
    assert suppress(7, "decision_maker") == 7
    assert suppress(3, "epidemiologist") == 3


def test_egress_guard_blocks_foreign_destinations():
    GUARD.install(["127.0.0.0/8", "localhost", "*.gov.example"])
    try:
        assert GUARD.host_allowed("127.0.0.1") and GUARD.host_allowed("api.health.gov.example")
        assert not GUARD.host_allowed("8.8.8.8") and not GUARD.host_allowed("storage.example-cloud.com")
        res = try_outbound("https://storage.example-cloud.com/upload")
        assert res["blocked"]
        with pytest.raises(EgressBlocked):
            socket.create_connection(("1.1.1.1", 443), timeout=1)
        assert any("1.1.1.1" in b["destination"] for b in GUARD.blocked)
    finally:
        GUARD.uninstall()


def test_residency_checks_pass_for_demo_config():
    checks = {c["check"]: c for c in run_checks()}
    assert checks["Hosting region is an approved in-country region"]["status"] == "pass"
    assert checks["User interface loads no third-party resources (no CDNs, fonts or analytics)"]["status"] == "pass"
