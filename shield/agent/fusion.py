"""Stage 2: fuse signals into candidate public-health events.

Many detectors fire on the same underlying event (a school outbreak shows up
in the school, its neighbourhood, an age band, the clinic it uses and a
spatial cluster). Fusion groups them so the government sees one event with
all its corroborating evidence, while genuinely separate events (two
respiratory clusters in different parts of the city) stay separate.

Rules
-----
1. *Locus* signals (neighbourhood, school/university, spatial-scan zone) are
   processed strongest first. A locus signal joins an existing event of the
   same family when their case sets overlap (overlap coefficient >= 0.4);
   otherwise it starts a new event. Locus signals extend the event's case set.
2. *Context* signals (city-wide, age band, facility) attach to the event that
   accounts for the largest share of their excess cases (>= 40%). They add
   evidence but never widen the case set. A facility signal that no event
   explains becomes its own event (a facility-level cluster or a data artefact).
   Unexplained city-wide or age-band signals also become events.
3. Laboratory signals attach to the event sharing the most patients (>= 20%).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .detection import Signal

LOCUS = ("nb", "inst", "zone")
CONTEXT = ("all", "age", "facility", "age_group")


@dataclass
class CandidateEvent:
    family: str
    signals: list[Signal] = field(default_factory=list)
    case_ids: set = field(default_factory=set)
    nbs: set = field(default_factory=set)
    insts: set = field(default_factory=set)
    facilities: set = field(default_factory=set)
    kind: str = "locus"   # locus | facility | citywide

    @property
    def strength(self) -> float:
        return max((s.strength for s in self.signals), default=0)

    def add_locus(self, s: Signal) -> None:
        self.signals.append(s)
        self.case_ids |= set(s.case_ids.tolist())
        if s.stratum_type == "nb":
            self.nbs.add(int(s.stratum_value))
        elif s.stratum_type == "zone":
            self.nbs |= set(int(z) for z in s.stratum_value)
        elif s.stratum_type == "inst":
            self.insts.add(s.stratum_value)


def _overlap(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def fuse(signals: list[Signal], ev_pid_by_id: dict | None = None) -> list[CandidateEvent]:
    events: list[CandidateEvent] = []
    locus = sorted([s for s in signals if s.stratum_type in LOCUS], key=lambda s: -s.strength)
    for s in locus:
        cs = set(s.case_ids.tolist())
        best, best_ov = None, 0.0
        for e in events:
            if e.family != s.family:
                continue
            ov = _overlap(cs, e.case_ids)
            if ov > best_ov:
                best, best_ov = e, ov
        if best is not None and best_ov >= 0.4:
            best.add_locus(s)
        else:
            e = CandidateEvent(s.family)
            e.add_locus(s)
            events.append(e)
    context = sorted([s for s in signals if s.stratum_type in CONTEXT], key=lambda s: -s.strength)
    for s in context:
        cs = set(s.case_ids.tolist())
        excess = max(s.excess, 1.0)
        best, best_share = None, 0.0
        for e in events:
            if e.family != s.family:
                continue
            share = len(cs & e.case_ids) / excess
            if share > best_share:
                best, best_share = e, share
        if best is not None and best_share >= 0.4:
            best.signals.append(s)
            if s.stratum_type == "facility":
                best.facilities.add(s.stratum_value)
            continue
        # facility signal not explained by any locus event
        if s.stratum_type == "facility":
            same = next((e for e in events if e.kind == "facility" and s.stratum_value in e.facilities
                         and e.family == s.family), None)
            if same:
                same.signals.append(s)
                same.case_ids |= cs
                continue
            e = CandidateEvent(s.family, [s], set(cs), facilities={s.stratum_value}, kind="facility")
            events.append(e)
            continue
        # city-wide / age-band signal: attach to an existing city-wide event of the family or create one
        same = next((e for e in events if e.kind == "citywide" and e.family == s.family), None)
        if same:
            same.signals.append(s)
            same.case_ids |= cs
        else:
            events.append(CandidateEvent(s.family, [s], set(cs), kind="citywide"))
    # a facility-only event whose cases are mostly inside a locus event is merged into it
    for fe in [e for e in events if e.kind == "facility"]:
        for e in events:
            if e is fe or e.kind == "facility" or e.family != fe.family:
                continue
            if len(fe.case_ids & e.case_ids) / max(len(fe.case_ids), 1) >= 0.5:
                e.signals.extend(fe.signals)
                e.facilities |= fe.facilities
                events.remove(fe)
                break
    # laboratory evidence: attach to the event sharing the most patients; a strong laboratory signal that
    # matches no event becomes an event of its own (e.g. a cluster of positive tests before counts rise)
    if ev_pid_by_id is not None:
        for s in [s for s in signals if s.kind == "lab"]:
            best, best_share = None, 0.0
            for e in events:
                if e.family != s.family:
                    continue
                pids = {ev_pid_by_id.get(i) for i in e.case_ids}
                share = len(pids & s.pids) / max(len(s.pids), 1)
                if share > best_share:
                    best, best_share = e, share
            if best is not None and best_share >= 0.2:
                best.signals.append(s)
            elif s.nbs and len(s.case_ids) >= 3 and s.p_value < 1e-3:
                e = CandidateEvent(s.family, [s], set(s.case_ids.tolist()), nbs=set(s.nbs))
                events.append(e)
    # mortality signals stand alone
    events.sort(key=lambda e: -e.strength)
    return events
