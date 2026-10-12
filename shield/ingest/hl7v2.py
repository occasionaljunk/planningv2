"""HL7 v2.5.1 connector for ADT^A04/A01/A03 messages (MLLP framing removed).

Reads MSH, PID (national ID in PID-3 with identifier type NI, DOB, sex,
postal code), PV1 (patient class, point of care, visit number, discharge
disposition), PV2-3 (admit reason / chief complaint) and DG1 (diagnoses).
"""
from __future__ import annotations

from .model import SourceRecord

CLASS_MAP = {"E": "emergency", "O": "outpatient", "I": "inpatient", "P": "outpatient", "R": "outpatient"}


class HL7Error(ValueError):
    pass


def _field(seg: list[str], i: int) -> str:
    return seg[i] if len(seg) > i else ""


def parse_message(message: str, source_id: str, received_at: str) -> list[SourceRecord]:
    text = message.replace("\r\n", "\r").replace("\n", "\r").strip("\x0b\x1c\r")
    segs = [s.split("|") for s in text.split("\r") if s]
    if not segs or segs[0][0] != "MSH":
        raise HL7Error("message must start with MSH")
    comp, rep = segs[0][1][0], segs[0][1][1]
    msg_type = _field(segs[0], 8)
    if not msg_type.startswith("ADT"):
        raise HL7Error(f"unsupported message type {msg_type}")
    sending_fac = _field(segs[0], 3)
    pid = next((s for s in segs if s[0] == "PID"), None)
    pv1 = next((s for s in segs if s[0] == "PV1"), None)
    pv2 = next((s for s in segs if s[0] == "PV2"), None)
    if not pid or not pv1:
        raise HL7Error("PID and PV1 are required")
    nid = None
    for ident in _field(pid, 3).split(rep):
        parts = ident.split(comp)
        if len(parts) >= 5 and parts[4] == "NI":
            nid = parts[0]
    if not nid:
        raise HL7Error("no national identifier (type NI) in PID-3")
    dob = _field(pid, 7)
    dob = f"{dob[:4]}-{dob[4:6]}-{dob[6:8]}" if len(dob) >= 8 else None
    sex = _field(pid, 8) or None
    addr = _field(pid, 11).split(comp)
    postcode = addr[4] if len(addr) > 4 else None
    klass = CLASS_MAP.get(_field(pv1, 2), "outpatient")
    poc = _field(pv1, 3).split(comp)[0]
    visit = _field(pv1, 19).split(comp)[0]
    disposition = _field(pv1, 36)
    admit_dt = _field(pv1, 44)
    event_date = f"{admit_dt[:4]}-{admit_dt[4:6]}-{admit_dt[6:8]}"
    cc = ""
    if pv2:
        reason = _field(pv2, 3).split(comp)
        cc = reason[1] if len(reason) > 1 else reason[0]
    codes = [_field(s, 3).split(comp)[0] for s in segs if s[0] == "DG1" and _field(s, 3)]
    fac = _field(pv1, 3).split(comp)[3] if len(_field(pv1, 3).split(comp)) > 3 else sending_fac
    return [SourceRecord("encounter", source_id, visit, nid, dob, sex, postcode, fac, event_date, received_at,
                         klass, cc, codes, klass == "inpatient", poc.upper() == "ICU", disposition == "20")]
