"""Epidemiological characterisation: reproduction number, growth and nowcasting.

* ``cori_rt``: instantaneous reproduction number (Cori et al., Am J Epidemiol
  2013; the EpiEstim method used by WHO and most national agencies) with a
  Gamma(1, 5) prior and a sliding 7-day window over excess incidence.
* ``growth``: exponential growth rate and doubling time from a Poisson
  log-linear regression over the most recent 14 days.
* ``delay_completeness``: empirical reporting-delay distribution per source,
  used to correct the most recent days for data still in transit (a simple
  multiplicative nowcast).
"""
from __future__ import annotations

import numpy as np
from scipy import stats

from .glm import fit_poisson


def discretised_gamma(mean: float, sd: float, max_days: int = 30) -> np.ndarray:
    shape = (mean / sd) ** 2
    scale = sd ** 2 / mean
    cdf = stats.gamma.cdf(np.arange(0, max_days + 1) + 0.5, shape, scale=scale)
    w = np.diff(np.concatenate([[0.0], cdf]))
    w[0] = 0.0
    return w / w.sum()


def cori_rt(incidence: np.ndarray, si_mean: float, si_sd: float, window: int = 7, a_prior: float = 1.0,
            b_prior: float = 5.0) -> dict | None:
    inc = np.maximum(np.asarray(incidence, float), 0)
    n = len(inc)
    if n < window + 3 or inc.sum() < 12:
        return None
    w = discretised_gamma(si_mean, si_sd, max_days=min(30, n - 1))
    # total infectiousness at t: sum over s >= 1 of inc[t - s] * w[s]
    lam = np.array([sum(inc[t - s] * w[s] for s in range(1, min(len(w), t + 1))) for t in range(n)])
    out = []
    for t in range(window, n):
        I = inc[t - window + 1:t + 1].sum()
        L = lam[t - window + 1:t + 1].sum()
        if L <= 0:
            continue
        shape = a_prior + I
        scale = 1 / (1 / b_prior + L)
        out.append({"t": t, "mean": shape * scale, "lo": stats.gamma.ppf(0.025, shape, scale=scale),
                    "hi": stats.gamma.ppf(0.975, shape, scale=scale)})
    if not out:
        return None
    last = out[-1]
    return {"rt": round(float(last["mean"]), 2), "rt_lo": round(float(last["lo"]), 2), "rt_hi": round(float(last["hi"]), 2),
            "series": [{"t": o["t"], "mean": round(float(o["mean"]), 2), "lo": round(float(o["lo"]), 2),
                        "hi": round(float(o["hi"]), 2)} for o in out],
            "serial_interval": {"mean": si_mean, "sd": si_sd}, "window_days": window}


def growth(counts: np.ndarray, days: int = 14) -> dict | None:
    y = np.asarray(counts[-days:], float)
    if len(y) < 7 or y.sum() < 10:
        return None
    t = np.arange(len(y), dtype=float)
    X = np.column_stack([np.ones_like(t), t])
    fit = fit_poisson(X, y)
    r = float(fit.beta[1])
    se = float(np.sqrt(fit.cov_unscaled[1, 1] * fit.phi))
    lo, hi = r - 1.96 * se, r + 1.96 * se
    res = {"daily_growth_rate": round(r, 4), "growth_ci": [round(lo, 4), round(hi, 4)], "phi": round(fit.phi, 2)}
    if lo > 0:
        res["doubling_time_days"] = round(np.log(2) / r, 1)
        res["doubling_ci"] = [round(np.log(2) / hi, 1), round(np.log(2) / lo, 1)]
    elif hi < 0:
        res["halving_time_days"] = round(np.log(2) / -r, 1)
    return res


def delay_completeness(delays_days: np.ndarray, horizon: int = 30) -> np.ndarray:
    """Return F[d] = share of records that have arrived within d days of the event (d = 0..horizon)."""
    if len(delays_days) == 0:
        return np.ones(horizon + 1)
    d = np.clip(np.floor(delays_days), 0, horizon).astype(int)
    counts = np.bincount(d, minlength=horizon + 1).astype(float)
    F = np.cumsum(counts) / counts.sum()
    return np.maximum(F, 1e-3)
