"""The Shield AI surveillance agent.

Each run is a fixed, auditable sequence of steps. Every step is recorded in a
trace (what was done, with what inputs, how long it took and what it found)
that is stored with the run and shown in the interface:

    1. load        data available as of the run date (late records stay invisible)
    2. detect      temporal, spatial and laboratory detectors over all series
    3. fuse        group signals into candidate events
    4. characterise  case definition, curve, growth, people, places, severity, labs, sources
    5. diagnose    differential diagnosis with an evidence ledger
    6. confidence  calibrated, feedback-trained probability the event is real
    7. biases      quantified limitations
    8. history     past analogues and local history with scaled projections
    9. brief       title, summary, key findings, recommended actions
   10. persist + notify   create or update alerts; notify above the threshold
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timezone

import numpy as np

from ..config import get_settings
from ..db import get_conn, kv_get, write_lock
from ..security import audit
from . import biases, characterize, confidence, history, hypotheses, narrative, notify
from .data import AnalysisData
from .detection import run_detection
from .feedback import family_multiplier, learned_priors
from .fusion import fuse

ALGORITHMS = [
    {"name": "Robust quasi-Poisson regression baseline", "role": "Daily and 7-day exceedance vs expected counts",
     "reference": "Burkom et al. 2007 (ESSENCE regression); Farrington et al. 1996 (re-weighting)"},
    {"name": "Farrington Flexible (improved Farrington)", "role": "Weekly exceedance vs same season in previous years",
     "reference": "Noufaily et al., Statistics in Medicine 2013"},
    {"name": "EARS C1/C2/C3", "role": "Short-baseline detection of sharp onsets", "reference": "Hutwagner et al. 2003 (CDC EARS)"},
    {"name": "CUSUM", "role": "Accumulating small, sustained increases", "reference": "Page 1954; Rogerson & Yamada 2004"},
    {"name": "Space-time permutation scan", "role": "Clusters unusual in space and time together",
     "reference": "Kulldorff et al., PLoS Medicine 2005 (SaTScan)"},
    {"name": "Expectation-based Poisson scan", "role": "Localised excess vs model expectations",
     "reference": "Neill et al., NIPS 2005"},
    {"name": "Benjamini-Hochberg FDR", "role": "Controls false discoveries across hundreds of series",
     "reference": "Benjamini & Hochberg 1995"},
    {"name": "Instantaneous reproduction number (Cori)", "role": "Transmissibility from excess incidence",
     "reference": "Cori et al., American Journal of Epidemiology 2013 (EpiEstim)"},
    {"name": "Log-linear growth / doubling time", "role": "Speed of growth with confidence interval", "reference": "Standard"},
    {"name": "Reporting-delay nowcast", "role": "Corrects the latest days for records still in transit",
     "reference": "Empirical delay distribution per source"},
    {"name": "Syndromic classifier with negation", "role": "Codes + triage text to syndromes",
     "reference": "CDC NSSP/ESSENCE syndrome definitions; Chapman et al. 2001 (NegEx)"},
    {"name": "Bayesian differential diagnosis", "role": "Ranks causes with an explicit evidence ledger",
     "reference": "Pathogen profiles from WHO/CDC/ECDC fact sheets"},
    {"name": "Feedback-trained logistic confidence model", "role": "Probability the event is real; learns from feedback",
     "reference": "MAP logistic regression with expert prior"},
]


class Trace:
    def __init__(self) -> None:
        self.steps: list[dict] = []
        self._t = time.time()

    def step(self, name: str, detail: str, **data) -> None:
        now = time.time()
        self.steps.append({"step": name, "detail": detail, "seconds": round(now - self._t, 2), **data})
        self._t = now


def _priority(p: float, ch: dict, hyps: list[dict]) -> str:
    top = hyps[0] if hyps else {"id": ""}
    sev_kb = next((h for h in hypotheses.knowledge() if h["id"] == top["id"]), {}).get("severity", "moderate")
    growing = bool((ch.get("growth") or {}).get("doubling_time_days"))
    severe = ch["severity"]["admission_ratio"] >= 1.5 or sev_kb in ("high", "elevated")
    if top["id"] == "reporting_artefact" and top.get("probability", 0) >= 0.4:
        return "P3"
    if p >= 0.6 and (growing or severe):
        return "P1"
    if p >= 0.4:
        return "P2"
    return "P3"


SPREADING = {"novel_respiratory", "seasonal_influenza", "sars_cov_2", "rsv", "measles", "varicella", "meningococcal",
             "enteroviral_meningitis"}


def _adjacent(data, a: set, b: set) -> bool:
    if not a or not b:
        return False
    c = data.ref.centroids
    d = np.linalg.norm(c[:, None, :] - c[None, :, :], axis=2)
    nn = np.sort(d, axis=1)[:, 1]
    thr = 1.6 * float(np.median(nn))
    return bool(a & b) or any(d[i, j] <= thr for i in a for j in b)


def _place_nbs(e, ch) -> set:
    if e.nbs:
        return set(e.nbs)
    return {g["id"] for g in ch["geography"][:3]}


def _age_vec(ch) -> np.ndarray:
    v = np.array([a["cases"] if isinstance(a["cases"], (int, float)) else 0 for a in ch["demographics"]["age"]], float)
    return v / max(np.linalg.norm(v), 1e-9)


def _merge_spreading(assessed, data, det, as_of, priors, tr):
    """Link candidate events that are one spreading (person-to-person) event seen in several places.

    Two candidates of the same family are linked when both are best explained by a transmissible pathogen and
    they are geographically adjacent or share a university population, with a similar age profile. Linked
    candidates are merged and re-assessed as one event, so evidence (e.g. negative laboratory panels in one
    place) informs the whole event.
    """
    from .fusion import CandidateEvent

    def spreading(item):
        hyps = item[2]
        return bool(hyps) and hyps[0]["id"] in SPREADING and hyps[0]["probability"] >= 0.35

    groups: list[list] = []
    rest = []
    for item in sorted(assessed, key=lambda x: -x[0].strength):
        if not spreading(item):
            rest.append(item)
            continue
        e, ch, _ = item
        placed = False
        for g in groups:
            ae, ach, _ = g[0]
            if ae.family != e.family:
                continue
            uni = ch["settings"]["university"]["cases"] >= 0.25 and ach["settings"]["university"]["cases"] >= 0.25
            near = _adjacent(data, _place_nbs(ae, ach), _place_nbs(e, ch))
            similar = float(_age_vec(ach) @ _age_vec(ch)) >= 0.75
            if (uni or near) and similar:
                g.append(item)
                placed = True
                break
        if not placed:
            groups.append([item])
    out = list(rest)
    for items in groups:
        if len(items) == 1:
            out.append(items[0])
            continue
        m = CandidateEvent(items[0][0].family, kind="locus")
        for e, ch, _ in items:
            m.signals.extend(e.signals)
            m.case_ids |= e.case_ids
            m.nbs |= e.nbs if e.nbs else _place_nbs(e, ch)
            m.insts |= e.insts
            m.facilities |= e.facilities
        ch = characterize.characterize(m, data, det)
        hyps = hypotheses.score(ch, as_of, priors)
        names = "; ".join(f"{x[1]['place']} ({x[2][0]['name']})" for x in items)
        tr.step("merge", f"Linked {len(items)} candidate events as one spreading event: {names}.")
        out.append((m, ch, hyps))
    out.sort(key=lambda x: -x[0].strength)
    return out


def run_agent(as_of: date, actor: str = "agent", notify: bool = True) -> dict:
    s = get_settings()
    con = get_conn()
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tr = Trace()
    data = AnalysisData(as_of, con)
    tr.step("load", f"Loaded {len(data.ev):,} pseudonymised records and {len(data.labs):,} laboratory results available "
                    f"as of {as_of} ({data.start} to {as_of}). Records received later are not visible.",
            records=int(len(data.ev)), labs=int(len(data.labs)))
    fm = family_multiplier()
    det = run_detection(data, alpha=s.alert_alpha, fdr_q=s.fdr_q, replicates=s.scan_replicates, family_multiplier=fm)
    tr.step("detect", f"Evaluated {det.n_series} syndrome x population series with 4 temporal detectors, 2 spatial scan "
                      f"statistics over 6 syndromes and laboratory detectors; {len(det.signals)} signals fired.",
            series=det.n_series, signals=len(det.signals), timings=det.timings,
            threshold_multipliers=fm, signal_list=[x.summary() | {"results": None} for x in det.signals][:80])
    pid_by_id = dict(zip(data.a_id.tolist(), data.ev["pid"].tolist()))
    events = fuse(det.signals, pid_by_id)
    tr.step("fuse", f"Fused {len(det.signals)} signals into {len(events)} candidate events.", events=len(events))
    city_pop = int(data.ref.pop_by_nb().sum())
    priors = learned_priors()
    w = confidence.weights()
    # characterise and diagnose every candidate, then merge candidates that are the same spreading
    # (person-to-person) event seen in several places, and re-assess the merged event
    assessed = []
    locus_nbs: dict = {}
    for e in events:
        if e.kind == "locus":
            locus_nbs.setdefault(e.family, set()).update(e.nbs)
    for e in events:
        ch = characterize.characterize(e, data, det, exclude_nbs=locus_nbs.get(e.family, set()))
        if ch["observed"] < 3 or ch["excess"] < 2:
            tr.step("discard", f"Discarded weak candidate in {ch['place']} ({ch['observed']} cases, {ch['excess']} excess).")
            continue
        assessed.append((e, ch, hypotheses.score(ch, as_of, priors)))
    assessed = _merge_spreading(assessed, data, det, as_of, priors, tr)
    reports = []
    for e, ch, hyps in assessed:
        f = confidence.features(e, ch, hyps)
        p, contrib = confidence.predict(f, w)
        lvl = confidence.level(p)
        pr = _priority(p, ch, hyps)
        bias = biases.assess(ch, data, det.n_series, s.fdr_q)
        aff_pop = int(sum(data.ref.pop_by_nb()[g["id"]] for g in ch["geography"][:3])) if ch["geography"] else city_pop
        hist = history.context(hyps, ch, aff_pop, city_pop)
        sigs = [x.summary() for x in e.signals]
        title = narrative.title(ch, hyps)
        report = {
            "title": title, "as_of": as_of.isoformat(), "family": ch["family"], "family_label": ch["family_label"],
            "summary": narrative.summary(ch, hyps, p, lvl),
            "confidence": {"value": round(p, 3), "level": lvl, "features": f, "contributions": contrib,
                           "model_version": confidence.model_version()},
            "priority": pr, "priority_text": narrative.PRIORITY_TEXT[pr],
            "key_findings": narrative.key_findings(ch, hyps, sigs),
            "hypotheses": hyps, "characterisation": {k: v for k, v in ch.items() if k not in ("case_event_ids", "case_pids")},
            "signals": sigs, "algorithms": ALGORITHMS, "biases": bias, "historical_context": hist,
            "recommended_actions": narrative.recommended_actions(ch, hyps, pr),
            "evidence": {"case_count": len(ch["case_event_ids"]), "case_definition":
                         f"{ch['case_syndrome_label']} in {ch['place']}, {ch['window']['start']} to {ch['window']['end']}"},
        }
        llm = narrative.llm_rewrite({"title": title, "summary": report["summary"], "findings": report["key_findings"]})
        if llm:
            report["summary_plain_language"] = llm
        reports.append((e, ch, report))
        tr.step("assess", f"{title}: confidence {p:.0%} ({lvl}), priority {pr}; top hypothesis "
                          f"{hyps[0]['name'] if hyps else 'n/a'} ({hyps[0]['probability']:.0%})." if hyps else title,
                place=ch["place"], observed=ch["observed"], expected=ch["expected"])
    new_alerts, updated = persist(reports, as_of, tr, notify)
    trace = tr.steps
    finished = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with write_lock():
        cur = con.execute("INSERT INTO agent_runs(started_at, finished_at, as_of, n_series, n_signals, n_events, trace) "
                          "VALUES (?,?,?,?,?,?,?)", (started, finished, as_of.isoformat(), det.n_series, len(det.signals),
                                                     len(reports), json.dumps(trace, default=str)))
        run_id = cur.lastrowid
        con.execute("UPDATE alerts SET run_id=? WHERE as_of=?", (run_id, as_of.isoformat()))
        con.commit()
    audit.record(actor, "system", "agent.run", f"run:{run_id}", {"as_of": as_of.isoformat(), "signals": len(det.signals),
                                                                  "events": len(reports), "new_alerts": new_alerts})
    return {"run_id": run_id, "as_of": as_of.isoformat(), "signals": len(det.signals), "events": len(reports),
            "new_alerts": new_alerts, "updated_alerts": updated, "series": det.n_series}


def _loci(rep: dict) -> set:
    """Places that identify an event: the facility for facility-level events, otherwise neighbourhoods and
    schools/universities. Facility and place events never match each other through shared neighbourhoods."""
    ch = rep["characterisation"]
    if ch.get("kind") == "facility":
        return {f"src:{s['source_id']}" for s in ch.get("sources", [])[:1]}
    out = {f"nb:{g['id']}" for g in ch.get("geography", [])[:3]}
    out |= {f"inst:{i['id']}" for i in ch.get("institutions", []) if (i.get("relative_risk") or 0) >= 2}
    return out


def persist(reports, as_of: date, tr: Trace, do_notify: bool) -> tuple[int, int]:
    """Create or update alerts. An event continues an open alert of the same family when they share cases
    (overlap >= 30%) or their places (neighbourhoods, schools, facility) substantially overlap. If one event
    continues several open alerts, they are consolidated into the oldest one."""
    con = get_conn()
    s = get_settings()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    open_alerts = con.execute("SELECT id, syndrome_family, status, history, notified, confidence, report FROM alerts "
                              "WHERE status != 'closed' ORDER BY id").fetchall()
    case_sets, loci = {}, {}
    for a in open_alerts:
        case_sets[a["id"]] = {r[0] for r in con.execute("SELECT event_id FROM alert_cases WHERE alert_id=?", (a["id"],))}
        loci[a["id"]] = _loci(json.loads(a["report"]))
    new, upd = 0, 0
    claimed = set()
    for e, ch, rep in reports:
        ids = set(ch["case_event_ids"])
        my_loci = _loci(rep)
        scored = []
        for a in open_alerts:
            if a["syndrome_family"] != rep["family"] or a["id"] in claimed:
                continue
            last_seen = json.loads(a["history"])[-1].get("as_of", "") if a["history"] else ""
            if last_seen and (as_of - date.fromisoformat(last_seen[:10])).days > 7:
                continue  # stale alerts are not revived by a later, different event
            prev = case_sets.get(a["id"], set())
            ov = len(prev & ids) / min(len(prev), len(ids)) if prev and ids else 0.0
            same_kind = (a_kind := json.loads(a["report"])["characterisation"].get("kind")) == ch["kind"] or \
                {a_kind, ch["kind"]} <= {"locus", "citywide"}
            if ov >= 0.3 and same_kind:
                scored.append((ov, a))
        scored.sort(key=lambda x: (-x[0], x[1]["id"]))
        new_top = rep["hypotheses"][0]["id"] if rep["hypotheses"] else None
        if scored:
            old_top = json.loads(scored[0][1]["report"])["hypotheses"][0]["id"] if json.loads(scored[0][1]["report"])["hypotheses"] else None
            if old_top == "chance" and new_top != "chance" and rep["confidence"]["value"] >= s.notify_min_confidence:
                # a weak "chance" signal is superseded, not continued, when a real event emerges in the same place
                with write_lock():
                    con.execute("UPDATE alerts SET status='closed', updated_at=? WHERE id=?", (now, scored[0][1]["id"]))
                    con.commit()
                tr.step("supersede", f"Watch-list alert #{scored[0][1]['id']} (chance variation) superseded by a new event.")
                scored = []
        # the best match continues; further open alerts that share cases are consolidated into it
        matches = [a for _, a in scored]
        point = {"as_of": as_of.isoformat(), "confidence": rep["confidence"]["value"], "observed": ch["observed"],
                 "expected": ch["expected"], "level": rep["confidence"]["level"], "priority": rep["priority"],
                 "top_hypothesis": rep["hypotheses"][0]["name"] if rep["hypotheses"] else None}
        top = rep["hypotheses"][0]["id"] if rep["hypotheses"] else None
        with write_lock():
            if matches:
                match = matches[0]
                aid = match["id"]
                claimed.update(m["id"] for m in matches)
                own = json.loads(match["history"])
                hist = []
                for other in matches[1:]:
                    hist += json.loads(other["history"])
                    con.execute("UPDATE alerts SET status='closed', updated_at=?, history=? WHERE id=?",
                                (now, json.dumps(json.loads(other["history"]) + [{"as_of": as_of.isoformat(),
                                                                                  "merged_into": aid}]), other["id"]))
                    tr.step("consolidate", f"Alert #{other['id']} is the same event as #{aid}; consolidated into #{aid}.")
                hist = sorted({h["as_of"]: h for h in hist + own if "as_of" in h and "merged_into" not in h}.values(),
                              key=lambda h: h["as_of"])
                hist = [h for h in hist if h["as_of"] != as_of.isoformat()] + [point]
                con.execute("UPDATE alerts SET updated_at=?, as_of=?, title=?, top_hypothesis=?, confidence=?, level=?, "
                            "priority=?, report=?, history=? WHERE id=?",
                            (now, as_of.isoformat(), rep["title"], top, rep["confidence"]["value"], rep["confidence"]["level"],
                             rep["priority"], json.dumps(rep, default=str), json.dumps(hist), aid))
                upd += 1
                notified = any(m["notified"] for m in matches)
                should_notify = (not notified) and rep["confidence"]["value"] >= s.notify_min_confidence
                if notified:
                    con.execute("UPDATE alerts SET notified=1 WHERE id=?", (aid,))
            else:
                cur = con.execute("INSERT INTO alerts(fingerprint, created_at, updated_at, first_as_of, as_of, status, title, "
                                  "syndrome_family, top_hypothesis, confidence, level, priority, report, history) "
                                  "VALUES (?,?,?,?,?,'new',?,?,?,?,?,?,?,?)",
                                  (f"{rep['family']}|{ch['place']}", now, now, as_of.isoformat(), as_of.isoformat(),
                                   rep["title"], rep["family"], top, rep["confidence"]["value"], rep["confidence"]["level"],
                                   rep["priority"], json.dumps(rep, default=str), json.dumps([point])))
                aid = cur.lastrowid
                claimed.add(aid)
                new += 1
                should_notify = rep["confidence"]["value"] >= s.notify_min_confidence
            con.execute("DELETE FROM alert_cases WHERE alert_id=?", (aid,))
            con.executemany("INSERT OR IGNORE INTO alert_cases(alert_id, event_id) VALUES (?,?)", [(aid, int(i)) for i in ids])
            con.commit()
        if should_notify and do_notify:
            notify.notify(aid, rep["title"], rep["priority"], rep["confidence"]["level"], rep["confidence"]["value"],
                          rep["summary"])
            con.execute("UPDATE alerts SET notified=1 WHERE id=?", (aid,))
            con.commit()
        tr.step("persist", f"{'Updated' if matches else 'Created'} alert #{aid}: {rep['title']}", alert_id=aid)
    return new, upd
