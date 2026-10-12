"""Syndromic classification of encounters.

Follows the approach of operational syndromic-surveillance systems (CDC
ESSENCE / NSSP syndrome definitions): diagnosis codes where present, otherwise
the free-text chief complaint, with NegEx-style negation handling so that
"rash itchy no fever" is not counted as fever-and-rash illness.

Syndromes
---------
ILI     influenza-like illness (fever plus cough or sore throat, or influenza codes)
RESP    acute respiratory illness without the ILI fever criterion
GI      gastrointestinal illness (vomiting, diarrhoea, infectious gastroenteritis codes)
FEVER   fever without a localising syndrome
RASH    fever and rash illness (measles, varicella, ...)
NEURO   meningitis / encephalitis-like illness
OTHER   everything else (injuries, chronic disease, ...), kept as the denominator
SEVERE_RESP is an additional flag: pneumonia/respiratory-failure codes or admission with ILI/RESP.
"""
from __future__ import annotations

import re
from functools import lru_cache

SYNDROMES = ["ILI", "RESP", "GI", "FEVER", "RASH", "NEURO", "OTHER"]
PRIORITY = ["NEURO", "RASH", "ILI", "GI", "RESP", "FEVER"]

ICD_RULES: list[tuple[str, str]] = [
    # (prefix, syndrome) — longest prefix wins
    ("J09", "ILI"), ("J10", "ILI"), ("J11", "ILI"),
    ("J00", "RESP"), ("J01", "RESP"), ("J02", "RESP"), ("J03", "RESP"), ("J04", "RESP"), ("J05", "RESP"),
    ("J06", "RESP"), ("J12", "RESP"), ("J13", "RESP"), ("J14", "RESP"), ("J15", "RESP"), ("J16", "RESP"),
    ("J17", "RESP"), ("J18", "RESP"), ("J20", "RESP"), ("J21", "RESP"), ("J22", "RESP"), ("J40", "RESP"),
    ("J45", "RESP"), ("J80", "RESP"), ("J96", "RESP"), ("R05", "RESP"), ("R06.0", "RESP"), ("R06.2", "RESP"),
    ("U07.1", "RESP"), ("A48.1", "RESP"), ("J44.1", "RESP"),
    ("A0", "GI"), ("R11", "GI"), ("R19.7", "GI"), ("K52.9", "GI"),
    ("R50", "FEVER"), ("B34.9", "FEVER"),
    ("B01", "RASH"), ("B02", "RASH"), ("B05", "RASH"), ("B06", "RASH"), ("B08", "RASH"), ("B09", "RASH"),
    ("A39", "NEURO"), ("A8", "NEURO"), ("G00", "NEURO"), ("G01", "NEURO"), ("G02", "NEURO"), ("G03", "NEURO"),
    ("G04", "NEURO"), ("G05", "NEURO"),
]
SEVERE_RESP_PREFIX = ("J12", "J13", "J14", "J15", "J16", "J17", "J18", "J80", "J96", "A48.1")
FEVER_COMPANION = ("R05", "J06", "J02", "R21", "R41.82", "R56")

NEGATION = re.compile(r"\b(no|denies|denied|without|w/o|negative for|not|neg)\b")
TERMS = {
    "fever": r"fever|febrile|feverish|pyrexia|high temp|temp 3[89](\.\d)?|temp 4[01]|chills|\b3[89]\.\d\b",
    "cough": r"cough|coughing",
    "throat": r"sore throat|pharyngitis",
    "flu": r"\bflu\b|influenza|\bili\b|flu like",
    "resp": r"\bsob\b|shortness of breath|difficulty breathing|wheez|congestion|\buri\b|bronch|pneumonia|croup|breath",
    "aches": r"body aches|myalgia|muscle aches",
    "gi": r"vomit|emesis|n/v|v/d|diarrh|stomach bug|gastro|nausea",
    "rash": r"rash|spots|blister|lesion",
    "neuro": r"stiff neck|neck pain|altered mental|confusion|seizure|letharg|meningitis|encephalitis",
}
_TERMS = {k: re.compile(v) for k, v in TERMS.items()}
_SPLIT = re.compile(r"[,;/+]| and | with | w ")


def _text_features(text: str) -> set[str]:
    text = (text or "").lower()
    found: set[str] = set()
    if not text.strip():
        return found
    # handle "n/v/d" and "v/d" before splitting on "/"
    for clause in _SPLIT.split(text.replace("n/v/d", "vomiting diarrhea").replace("n/v", "vomiting").replace("v/d", "vomiting diarrhea")):
        for name, rx in _TERMS.items():
            m = rx.search(clause)
            if not m:
                continue
            window = clause[max(0, m.start() - 25):m.start()]
            if NEGATION.search(window):
                continue
            found.add(name)
    return found


def _icd_syndromes(codes: tuple[str, ...]) -> set[str]:
    out = set()
    for c in codes:
        c = c.strip().upper()
        best = None
        for prefix, syn in ICD_RULES:
            if c.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
                best = (prefix, syn)
        if best:
            out.add(best[1])
    # fever plus a respiratory companion code is the ICD form of ILI
    if any(c.startswith("R50") for c in codes) and any(c.startswith(("R05", "J06", "J02")) for c in codes):
        out.add("ILI")
    if any(c.startswith("R50") for c in codes) and any(c.startswith("R21") for c in codes):
        out.add("RASH")
    if any(c.startswith("R50") for c in codes) and any(c.startswith(("R41.82", "R56")) for c in codes):
        out.add("NEURO")
    return out


def _text_syndromes(f: set[str]) -> set[str]:
    out = set()
    fever = "fever" in f
    if "flu" in f or (fever and ("cough" in f or "throat" in f or "aches" in f)):
        out.add("ILI")
    if "cough" in f or "resp" in f or "throat" in f:
        out.add("RESP")
    if "gi" in f:
        out.add("GI")
    if "rash" in f and fever:
        out.add("RASH")
    if "neuro" in f and (fever or "meningitis" in f):
        out.add("NEURO")
    if fever:
        out.add("FEVER")
    return out


def _pick(cands: set[str]) -> str:
    for s in PRIORITY:
        if s in cands:
            return s
    return "OTHER"


@lru_cache(maxsize=200_000)
def classify(chief_complaint: str | None, icd10: str | None, admitted: bool = False) -> tuple[str, bool]:
    """Return (primary_syndrome, severe_respiratory_flag)."""
    codes = tuple(c for c in (icd10 or "").split("|") if c.strip())
    feats = _text_features(chief_complaint or "")
    text_syn = _text_syndromes(feats)
    if codes:
        icd_syn = _icd_syndromes(codes)
        if icd_syn:
            cands = set(icd_syn)
            if ("RESP" in cands or "FEVER" in cands) and "ILI" in text_syn:
                cands.add("ILI")
            primary = _pick(cands)
        else:
            # coded as non-syndromic: trust the code unless the text describes a febrile syndrome
            febrile = text_syn & {"ILI", "RASH", "NEURO"}
            primary = _pick(febrile) if febrile else "OTHER"
    else:
        primary = _pick(text_syn)
    severe = (primary in ("ILI", "RESP")) and (admitted or any(c.startswith(SEVERE_RESP_PREFIX) for c in codes))
    return primary, bool(severe)
