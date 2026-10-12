import numpy as np

from shield.nlp.syndromes import classify


def test_codes_text_and_negation():
    assert classify("rash itchy no fever", "L30.9")[0] == "OTHER"
    assert classify("fever and rash", "")[0] == "RASH"
    assert classify("n/v/d", "")[0] == "GI"
    assert classify("fever, cough, body aches", "")[0] == "ILI"
    assert classify("denies fever, cough x1 wk", "")[0] == "RESP"
    assert classify("chest pain, no cough", "R07.9")[0] == "OTHER"
    assert classify("headache stiff neck fever", "")[0] == "NEURO"
    assert classify("fever and cough", "J06.9")[0] == "ILI"     # text upgrades a URI code to ILI
    assert classify("", "J18.9", False) == ("RESP", True)      # pneumonia code -> severe respiratory
    assert classify("cough", "J06.9", True) == ("RESP", True)  # admitted respiratory -> severe
    assert classify("abd pain", "A09")[0] == "GI"              # codes win: why coding artefacts matter


def test_accuracy_on_simulated_encounters(small_world):
    e = small_world.encounters.sample(20000, random_state=1)
    pred = np.array([classify(c or "", i or "", bool(a))[0] for c, i, a in
                     zip(e["chief_complaint"], e["icd10"], e["admitted"])])
    truth = e["truth_syndrome"].values
    acc = (pred == truth).mean()
    assert acc > 0.9, acc
    for syn in ("ILI", "GI", "RESP"):
        m = truth == syn
        recall = (pred[m] == syn).mean() if syn != "RESP" else np.isin(pred[m], ["RESP", "ILI"]).mean()
        assert recall > 0.8, (syn, recall)
