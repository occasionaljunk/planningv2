"""Spatial and space-time scan statistics.

* ``space_time_permutation_scan``: Kulldorff et al. (PLoS Med 2005), the
  prospective space-time permutation scan used by SaTScan and by the NYC
  Department of Health for daily syndromic surveillance. Needs only case
  counts (no population at risk), and adjusts for purely spatial and purely
  temporal variation, so it finds clusters that are unusual *in space and
  time together*. Significance by Monte Carlo permutation of case dates.
* ``expectation_based_poisson_scan``: Neill et al. (2005). Compares observed
  counts in circular zones over the recent window with model-based
  expectations (from the regression baselines), detecting localised
  increases even when they coincide with a city-wide rise. Significance by
  parametric bootstrap from the expected counts.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class ScanResult:
    method: str
    zones: list[int]
    centre: int
    window_days: int
    observed: float
    expected: float
    relative_risk: float
    llr: float
    p_value: float
    replicates: int
    details: dict = field(default_factory=dict)


def circular_zones(centroids: np.ndarray, max_fraction: float = 0.5, weights: np.ndarray | None = None) -> list[list[int]]:
    """All circles centred on a region centroid growing by nearest neighbours up to ``max_fraction`` of regions
    (or of total weight when ``weights`` is given)."""
    n = len(centroids)
    zones, seen = [], set()
    d = np.linalg.norm(centroids[:, None, :] - centroids[None, :, :], axis=2)
    for c in range(n):
        order = np.argsort(d[c], kind="stable")
        acc = 0.0
        for k in range(1, n + 1):
            members = tuple(sorted(order[:k].tolist()))
            if weights is not None:
                acc = weights[list(members)].sum() / weights.sum()
                if acc > max_fraction:
                    break
            elif k > max(1, int(max_fraction * n)):
                break
            if members not in seen:
                seen.add(members)
                zones.append(list(members))
    return zones


def _poisson_llr(c, e, total_c=None):
    """Kulldorff LLR (conditioned on total) when totals given, otherwise expectation-based Poisson LLR."""
    c = np.asarray(c, float)
    e = np.maximum(np.asarray(e, float), 1e-12)
    if total_c is None:
        llr = np.where(c > e, c * np.log(np.maximum(c, 1e-12) / e) + e - c, 0.0)
        return llr
    C = total_c
    out_c = C - c
    out_e = np.maximum(C - e, 1e-12)
    llr = np.where(c > e, c * np.log(np.maximum(c, 1e-12) / e) + np.where(out_c > 0, out_c * np.log(np.maximum(out_c, 1e-12) / out_e), 0.0), 0.0)
    return llr


def space_time_permutation_scan(case_region: np.ndarray, case_day: np.ndarray, n_regions: int, n_days: int,
                                zones: list[list[int]], max_window: int = 14, replicates: int = 999,
                                rng: np.random.Generator | None = None) -> ScanResult | None:
    """case_day: 0..n_days-1 where n_days-1 is the analysis day (prospective windows end there)."""
    rng = rng or np.random.default_rng(0)
    C = len(case_region)
    if C < 10:
        return None
    M = np.zeros((len(zones), n_regions))
    for i, z in enumerate(zones):
        M[i, z] = 1
    windows = np.arange(1, max_window + 1)

    def stats_for(days):
        counts = np.bincount(case_region * n_days + days, minlength=n_regions * n_days).reshape(n_regions, n_days)
        # cases in each region within the last L days
        rev = counts[:, ::-1].cumsum(axis=1)[:, :max_window]       # (regions, L)
        cz = M @ rev                                                 # (zones, L) observed in cylinder
        region_tot = counts.sum(1)                                   # n_z
        day_tot_rev = counts.sum(0)[::-1].cumsum()[:max_window]      # cases in last L days citywide
        zone_tot = M @ region_tot
        ez = zone_tot[:, None] * day_tot_rev[None, :] / C            # expected under no space-time interaction
        llr = _poisson_llr(cz, ez, C)
        return llr, cz, ez

    llr, cz, ez = stats_for(case_day)
    i, j = np.unravel_index(np.argmax(llr), llr.shape)
    best = float(llr[i, j])
    if best <= 0:
        return ScanResult("space_time_permutation", zones[i], zones[i][0], int(windows[j]), float(cz[i, j]),
                          float(ez[i, j]), 1.0, 0.0, 1.0, 0)
    exceed = 0
    for _ in range(replicates):
        perm = rng.permutation(case_day)
        l, _, _ = stats_for(perm)
        if l.max() >= best:
            exceed += 1
    p = (exceed + 1) / (replicates + 1)
    return ScanResult("space_time_permutation", zones[i], zones[i][0], int(windows[j]), float(cz[i, j]), float(ez[i, j]),
                      float(cz[i, j] / max(ez[i, j], 1e-9)), best, float(p), replicates)


def expectation_based_poisson_scan(observed: np.ndarray, expected: np.ndarray, zones: list[list[int]],
                                   replicates: int = 999, rng: np.random.Generator | None = None) -> ScanResult | None:
    """observed/expected: per-region counts over the recent window."""
    rng = rng or np.random.default_rng(1)
    if observed.sum() < 5:
        return None
    M = np.zeros((len(zones), len(observed)))
    for i, z in enumerate(zones):
        M[i, z] = 1
    c, e = M @ observed, M @ expected
    llr = _poisson_llr(c, e)
    i = int(np.argmax(llr))
    best = float(llr[i])
    if best <= 0:
        return None
    sims = rng.poisson(np.maximum(expected, 1e-9), size=(replicates, len(expected)))
    sim_llr = _poisson_llr(sims @ M.T, np.broadcast_to(e, (replicates, len(e)))).max(1)
    p = (int((sim_llr >= best).sum()) + 1) / (replicates + 1)
    return ScanResult("expectation_based_poisson", zones[i], zones[i][0], 7, float(c[i]), float(e[i]),
                      float(c[i] / max(e[i], 1e-9)), best, float(p), replicates)
