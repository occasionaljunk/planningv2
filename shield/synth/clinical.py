"""Clinical vocabularies used by the simulator.

Chief complaints are written the way triage nurses type them (abbreviations,
shorthand, occasional typos). ICD-10-CM codes are real codes. LOINC codes for
laboratory analytes are the commonly used ones; validate them against the
LOINC release adopted by the deploying country before production use.
"""
from __future__ import annotations

SYNDROMES = ["ILI", "RESP", "GI", "FEVER", "RASH", "NEURO", "OTHER"]

# (text, weight)
CHIEF_COMPLAINTS = {
    "ILI": [("fever and cough", 6), ("fever, cough, body aches", 5), ("flu like symptoms", 5), ("FLU SX x2 days", 3),
            ("fever chills myalgia", 3), ("cough fever sore throat", 4), ("fever 39.2 cough", 2),
            ("body aches, fever, headache", 2), ("ILI", 1), ("febrile w cough", 1), ("feverish + coughing x3d", 1)],
    "RESP": [("cough", 6), ("shortness of breath", 4), ("SOB", 3), ("sore throat", 5), ("congestion, cough", 4),
             ("wheezing", 2), ("difficulty breathing", 3), ("chest congestion productive cough", 2), ("URI sx", 2),
             ("cough x1 wk", 2), ("asthma flare", 2), ("bronchitis?", 1), ("croupy cough", 1), ("pneumonia f/u", 1)],
    "GI": [("vomiting", 5), ("diarrhea", 5), ("n/v/d", 4), ("nausea and vomiting", 4), ("vomiting and diarrhea", 4),
           ("stomach bug", 2), ("gastroenteritis", 2), ("abd cramps with diarrhea", 2), ("emesis x4", 1), ("V/D since last night", 1)],
    "FEVER": [("fever", 8), ("fever x2 days", 4), ("febrile infant", 2), ("high temp", 2), ("fever, fussy", 2), ("temp 38.9", 1)],
    "RASH": [("fever and rash", 5), ("rash with fever", 3), ("spots all over, fever", 2), ("rash + fever x2d", 2),
             ("blistering rash fever", 1)],
    "NEURO": [("headache stiff neck fever", 3), ("altered mental status", 3), ("confusion fever", 2), ("severe headache, neck pain, fever", 2),
              ("seizure with fever", 2), ("lethargic, fever, vomiting", 1)],
    "OTHER": [("abd pain", 10), ("chest pain", 8), ("back pain", 6), ("fall", 5), ("laceration", 4), ("ankle injury", 4),
              ("UTI sx", 4), ("anxiety", 3), ("headache", 4), ("dizziness", 3), ("med refill", 4), ("HTN follow up", 3),
              ("rash itchy no fever", 2), ("chest pain, no cough", 2), ("cough after eating, no fever", 1), ("MVC", 2),
              ("allergic reaction", 2), ("eye pain", 2), ("tooth pain", 2), ("SOB on exertion, hx CHF", 2), ("palpitations", 2),
              ("dysuria", 2), ("knee pain", 3), ("wound check", 2), ("diabetes f/u", 3), ("depression", 2)],
}

ICD10 = {
    "ILI": [("J11.1", 5), ("J10.1", 1), ("R50.9|R05.9", 3), ("J06.9", 1), ("J11.89", 1)],
    "RESP": [("J06.9", 8), ("J20.9", 5), ("J02.9", 5), ("J18.9", 2), ("J21.9", 1), ("J45.901", 2), ("R05.9", 3),
             ("R06.02", 2), ("J01.90", 1)],
    "GI": [("A09", 6), ("A08.4", 3), ("R11.2", 3), ("R19.7", 3), ("K52.9", 2), ("A08.11", 1)],
    "FEVER": [("R50.9", 8), ("B34.9", 3)],
    "RASH": [("R21|R50.9", 4), ("B09", 2), ("B01.9", 1)],
    "NEURO": [("G03.9", 2), ("A87.9", 2), ("R41.82", 3), ("G04.90", 1), ("R56.00", 1)],
}

OTHER_ICD = {
    "abd pain": ["R10.9", "R10.84", "R10.13", "R10.31"], "chest pain": ["R07.9", "R07.89"], "back pain": ["M54.50"],
    "fall": ["W19.XXXA"], "laceration": ["S61.419A", "S01.81XA"], "ankle injury": ["S93.401A"], "UTI sx": ["N39.0"],
    "anxiety": ["F41.9"], "headache": ["R51.9"], "dizziness": ["R42"], "med refill": ["Z76.0"], "HTN follow up": ["I10"],
    "rash itchy no fever": ["L30.9"], "chest pain, no cough": ["R07.9"], "cough after eating, no fever": ["R13.10"],
    "MVC": ["V43.52XA"], "allergic reaction": ["T78.40XA"], "eye pain": ["H57.10"], "tooth pain": ["K08.89"],
    "SOB on exertion, hx CHF": ["I50.9"], "palpitations": ["R00.2"], "dysuria": ["R30.0"], "knee pain": ["M25.569"],
    "wound check": ["Z48.00"], "diabetes f/u": ["E11.9"], "depression": ["F32.A"],
}

PNEUMONIA_CODES = ["J18.9", "J12.89", "J12.9", "J18.1"]

# Annual surveillance-relevant visit rate per person by syndrome and age band
VISIT_RATES = {
    "ILI":   [0.25, 0.20, 0.12, 0.08, 0.07, 0.06, 0.06, 0.07],
    "RESP":  [1.00, 0.45, 0.30, 0.25, 0.25, 0.30, 0.45, 0.60],
    "GI":    [0.35, 0.15, 0.08, 0.10, 0.08, 0.07, 0.09, 0.14],
    "FEVER": [0.30, 0.10, 0.04, 0.03, 0.02, 0.02, 0.03, 0.04],
    "RASH":  [0.06, 0.04, 0.02, 0.01, 0.01, 0.01, 0.01, 0.01],
    "NEURO": [0.010, 0.005, 0.005, 0.005, 0.004, 0.005, 0.008, 0.012],
    "OTHER": [1.20, 0.60, 0.60, 0.80, 0.90, 1.10, 1.50, 2.00],
}
# Share of visits that happen in an emergency department
ED_SHARE = {"ILI": 0.25, "RESP": 0.30, "GI": 0.30, "FEVER": 0.40, "RASH": 0.20, "NEURO": 0.80, "OTHER": 0.35}
# P(admitted | ED visit) by age band
ADMIT_P = {
    "ILI":   [0.03, 0.01, 0.01, 0.01, 0.02, 0.04, 0.08, 0.14],
    "RESP":  [0.06, 0.02, 0.02, 0.02, 0.04, 0.08, 0.18, 0.28],
    "GI":    [0.04, 0.02, 0.02, 0.02, 0.03, 0.05, 0.09, 0.14],
    "FEVER": [0.08, 0.03, 0.02, 0.02, 0.03, 0.05, 0.08, 0.12],
    "RASH":  [0.03, 0.02, 0.02, 0.02, 0.02, 0.03, 0.04, 0.06],
    "NEURO": [0.45, 0.35, 0.35, 0.35, 0.35, 0.40, 0.50, 0.60],
    "OTHER": [0.05, 0.03, 0.04, 0.05, 0.08, 0.14, 0.22, 0.30],
}
ICU_GIVEN_ADMIT = {"ILI": 0.10, "RESP": 0.12, "GI": 0.05, "FEVER": 0.06, "RASH": 0.05, "NEURO": 0.30, "OTHER": 0.08}
DEATH_GIVEN_ADMIT = [0.004, 0.002, 0.002, 0.004, 0.008, 0.02, 0.04, 0.08]

# Background mortality, deaths per person-year, by age band
MORTALITY = [0.0009, 0.0001, 0.0002, 0.0006, 0.0012, 0.0050, 0.0160, 0.0650]
DEATH_CAUSES = [("J18.9", "resp", 6), ("J44.1", "resp", 4), ("I21.9", "cvd", 10), ("I25.10", "cvd", 8),
                ("I50.9", "cvd", 7), ("I63.9", "cvd", 5), ("C34.90", "cancer", 8), ("C18.9", "cancer", 5),
                ("C50.919", "cancer", 4), ("C61", "cancer", 3), ("G30.9", "other", 6), ("E11.9", "other", 3),
                ("N18.6", "other", 3), ("K70.30", "other", 2), ("W19.XXXA", "injury", 3), ("V89.2XXA", "injury", 2)]

# Laboratory panels: analyte -> LOINC
RESP_PANEL = {"Influenza A": "92142-9", "Influenza B": "92141-1", "RSV": "92131-2", "SARS-CoV-2": "94500-6"}
GI_PANEL = {"Norovirus": "60271-4", "Salmonella": "625-4", "Campylobacter": "6331-3", "STEC": "16890-6"}
LEGIONELLA_UAT = {"Legionella pneumophila sg1 Ag": "32781-7"}
MEASLES_IGM = {"Measles IgM": "7965-7"}
PANELS = {
    "Respiratory pathogen panel (NAAT)": RESP_PANEL,
    "Gastrointestinal pathogen panel": GI_PANEL,
    "Legionella urinary antigen": LEGIONELLA_UAT,
    "Measles serology": MEASLES_IGM,
}
