"""Model of the demonstration jurisdiction: the fictional City of Riverton.

Riverton (population ~200,000) is modelled on a typical mid-sized city:
12 neighbourhoods with distinct demographic profiles, 2 hospitals, 5 clinics
(two of them university health centres), a public health laboratory, a
public health-insurance fund, a private insurer, a life insurer and the
municipal education registry. Coordinates are in kilometres on a local grid,
so the demo never implies a real place.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

AGE_BANDS = ["0-4", "5-11", "12-17", "18-24", "25-44", "45-64", "65-74", "75+"]
AGE_RANGES = [(0, 4), (5, 11), (12, 17), (18, 24), (25, 44), (45, 64), (65, 74), (75, 95)]
BASE_AGE_DIST = np.array([0.055, 0.080, 0.070, 0.090, 0.270, 0.250, 0.100, 0.085])

PROFILES = {
    "families":   np.array([1.40, 1.40, 1.35, 0.80, 1.20, 0.90, 0.70, 0.60]),
    "students":   np.array([0.35, 0.30, 0.40, 5.20, 0.95, 0.45, 0.30, 0.25]),
    "downtown":   np.array([0.80, 0.70, 0.70, 1.40, 1.45, 0.90, 0.80, 0.90]),
    "affluent":   np.array([0.80, 0.90, 1.00, 0.70, 0.85, 1.25, 1.40, 1.30]),
    "retirement": np.array([0.50, 0.50, 0.50, 0.50, 0.60, 1.10, 2.40, 2.80]),
    "industrial": np.array([0.90, 0.90, 0.90, 0.90, 0.95, 1.30, 1.20, 1.10]),
    "mixed":      np.array([1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00]),
}


@dataclass
class Neighbourhood:
    id: int
    name: str
    population: int
    profile: str
    uninsured: float
    private_insured: float
    care_seeking: float
    row: int
    col: int
    postcode: str = ""
    polygon: list = field(default_factory=list)
    centroid: tuple = (0.0, 0.0)


NEIGHBOURHOODS = [
    Neighbourhood(0, "Northgate", 19000, "families", 0.06, 0.25, 1.00, 0, 0),
    Neighbourhood(1, "University Heights", 21000, "students", 0.04, 0.15, 0.95, 0, 1),
    Neighbourhood(2, "Lakeview", 14000, "affluent", 0.02, 0.55, 1.10, 0, 2),
    Neighbourhood(3, "Cedar Hills", 15000, "families", 0.07, 0.20, 0.90, 0, 3),
    Neighbourhood(4, "Westpark", 17000, "mixed", 0.08, 0.22, 1.00, 1, 0),
    Neighbourhood(5, "Old Harbour", 22000, "downtown", 0.15, 0.12, 1.05, 1, 1),
    Neighbourhood(6, "Riverside", 18000, "mixed", 0.07, 0.24, 1.00, 1, 2),
    Neighbourhood(7, "Eastfield", 17000, "families", 0.06, 0.26, 1.00, 1, 3),
    Neighbourhood(8, "Ironworks", 15000, "industrial", 0.14, 0.10, 0.95, 2, 0),
    Neighbourhood(9, "Southbank", 16000, "mixed", 0.09, 0.20, 1.00, 2, 1),
    Neighbourhood(10, "Millbrook", 13000, "retirement", 0.03, 0.35, 1.10, 2, 2),
    Neighbourhood(11, "Greenhaven", 16000, "families", 0.04, 0.33, 1.00, 2, 3),
]


def _build_geometry(seed: int = 7) -> None:
    rng = np.random.default_rng(seed)
    cols, rows, size = 4, 3, 4.0
    vx = np.zeros((rows + 1, cols + 1))
    vy = np.zeros((rows + 1, cols + 1))
    for r in range(rows + 1):
        for c in range(cols + 1):
            jx = 0 if c in (0, cols) else rng.uniform(-0.8, 0.8)
            jy = 0 if r in (0, rows) else rng.uniform(-0.8, 0.8)
            vx[r, c] = c * size + jx
            vy[r, c] = r * size + jy
    for n in NEIGHBOURHOODS:
        r, c = n.row, n.col
        poly = [(vx[r, c], vy[r, c]), (vx[r, c + 1], vy[r, c + 1]),
                (vx[r + 1, c + 1], vy[r + 1, c + 1]), (vx[r + 1, c], vy[r + 1, c])]
        n.polygon = [(round(x, 3), round(y, 3)) for x, y in poly]
        n.centroid = (round(float(np.mean([p[0] for p in poly])), 3), round(float(np.mean([p[1] for p in poly])), 3))
        n.postcode = f"R{n.id + 1:02d}"


_build_geometry()


def age_distribution(nb: Neighbourhood) -> np.ndarray:
    w = BASE_AGE_DIST * PROFILES[nb.profile]
    return w / w.sum()


@dataclass
class Institution:
    id: str
    name: str
    kind: str          # primary_school | secondary_school | university
    neighbourhood_id: int
    serves: list[int]  # neighbourhoods whose residents attend
    enrolment: int = 0


INSTITUTIONS = [
    Institution("SCH-NGP", "Northgate Primary School", "primary_school", 0, [0, 1]),
    Institution("SCH-CHP", "Cedar Hills Primary School", "primary_school", 3, [3, 2]),
    Institution("SCH-HBP", "Harbour Primary School", "primary_school", 5, [5, 4]),
    Institution("SCH-EFP", "Eastfield Primary School", "primary_school", 7, [7, 6]),
    Institution("SCH-SBP", "Southbank Primary School", "primary_school", 9, [9, 8]),
    Institution("SCH-GHP", "Greenhaven Primary School", "primary_school", 11, [11, 10]),
    Institution("SCH-RNS", "Riverton North Secondary", "secondary_school", 1, [0, 1, 2, 3]),
    Institution("SCH-RCS", "Riverton Central Secondary", "secondary_school", 6, [4, 5, 6, 7]),
    Institution("SCH-RSS", "Riverton South Secondary", "secondary_school", 9, [8, 9, 10, 11]),
    Institution("UNI-RSU", "Riverton State University", "university", 1, list(range(12))),
    Institution("UNI-HBC", "Harbour College", "university", 5, list(range(12))),
]


@dataclass
class Facility:
    id: str
    name: str
    kind: str              # hospital | clinic | urgent_care | university_clinic
    ownership: str
    neighbourhood_id: int
    connected: bool        # sends EHR data directly to Shield
    protocol: str
    lag_hours: tuple       # (median, spread) reporting delay for EHR feed


FACILITIES = [
    Facility("FAC-RGH", "Riverton General Hospital", "hospital", "public", 5, True, "FHIR R4", (1.0, 0.8)),
    Facility("FAC-SMM", "St. Mary's Medical Center", "hospital", "private", 2, True, "FHIR R4", (2.0, 0.8)),
    Facility("FAC-NCH", "Northgate Community Health Clinic", "clinic", "public", 0, True, "HL7 v2.5.1", (14.0, 0.6)),
    Facility("FAC-EFP", "Eastfield Family Practice", "clinic", "private", 7, True, "HL7 v2.5.1", (20.0, 0.5)),
    Facility("FAC-SUC", "Southbank Urgent Care", "urgent_care", "private", 9, True, "FHIR R4", (3.0, 0.7)),
    Facility("FAC-RSU", "Riverton State University Student Health", "university_clinic", "public", 1, True, "FHIR R4", (6.0, 0.7)),
    Facility("FAC-HBC", "Harbour College Health Centre", "university_clinic", "private", 5, True, "HL7 v2.5.1", (30.0, 0.5)),
    Facility("FAC-WMG", "Westpark Medical Group", "clinic", "private", 4, False, "via insurer claims only", (0, 0)),
]

FACILITY_INDEX = {f.id: i for i, f in enumerate(FACILITIES)}

# Data sources that connect to the platform. Facilities with direct EHR feeds
# are sources; insurers, the lab and the registry are additional sources.
SOURCES = [
    {"id": "SRC-RGH", "name": "Riverton General Hospital", "institution_type": "hospital", "ownership": "public",
     "protocol": "FHIR R4 (Subscription + Bulk $export)", "facility": "FAC-RGH", "neighbourhood_id": 5},
    {"id": "SRC-SMM", "name": "St. Mary's Medical Center", "institution_type": "hospital", "ownership": "private",
     "protocol": "FHIR R4 (Subscription + Bulk $export)", "facility": "FAC-SMM", "neighbourhood_id": 2},
    {"id": "SRC-NCH", "name": "Northgate Community Health Clinic", "institution_type": "clinic", "ownership": "public",
     "protocol": "HL7 v2.5.1 ADT^A04 over MLLP/TLS", "facility": "FAC-NCH", "neighbourhood_id": 0},
    {"id": "SRC-EFP", "name": "Eastfield Family Practice", "institution_type": "clinic", "ownership": "private",
     "protocol": "HL7 v2.5.1 ADT^A04 over MLLP/TLS", "facility": "FAC-EFP", "neighbourhood_id": 7},
    {"id": "SRC-SUC", "name": "Southbank Urgent Care", "institution_type": "clinic", "ownership": "private",
     "protocol": "FHIR R4 (Subscription)", "facility": "FAC-SUC", "neighbourhood_id": 9},
    {"id": "SRC-RSU", "name": "Riverton State University Student Health", "institution_type": "university_clinic",
     "ownership": "public", "protocol": "FHIR R4 (Subscription)", "facility": "FAC-RSU", "neighbourhood_id": 1},
    {"id": "SRC-HBC", "name": "Harbour College Health Centre", "institution_type": "university_clinic",
     "ownership": "private", "protocol": "HL7 v2.5.1 ADT^A04 over MLLP/TLS", "facility": "FAC-HBC", "neighbourhood_id": 5},
    {"id": "SRC-LAB", "name": "Riverton Public Health Laboratory", "institution_type": "laboratory", "ownership": "public",
     "protocol": "FHIR R4 DiagnosticReport/Observation", "facility": None, "neighbourhood_id": 6},
    {"id": "SRC-NHI", "name": "National Health Insurance Fund", "institution_type": "health_insurer", "ownership": "public",
     "protocol": "Claims batch (CSV over SFTP, daily)", "facility": None, "neighbourhood_id": None},
    {"id": "SRC-MHI", "name": "Meridian Health Insurance", "institution_type": "health_insurer", "ownership": "private",
     "protocol": "Claims batch (CSV over SFTP, daily)", "facility": None, "neighbourhood_id": None},
    {"id": "SRC-ALC", "name": "Assurance Life Co.", "institution_type": "life_insurer", "ownership": "private",
     "protocol": "Death-claim notifications (CSV, daily)", "facility": None, "neighbourhood_id": None},
    {"id": "SRC-EDU", "name": "Riverton Education Registry", "institution_type": "registry", "ownership": "public",
     "protocol": "Pseudonymised enrolment list (CSV, termly)", "facility": None, "neighbourhood_id": None},
]
FACILITY_SOURCE = {s["facility"]: s["id"] for s in SOURCES if s["facility"]}

POSTCODE_TO_NB = {n.postcode: n.id for n in NEIGHBOURHOODS}
