"""Temporal aberration-detection algorithms.

* ``regression_detector``: robust quasi-Poisson regression with linear trend,
  annual harmonics, day-of-week and holiday terms fitted on up to two years of
  history with a guard band, in the spirit of CDC ESSENCE's regression models.
  Past outbreaks are down-weighted with Anscombe residuals (as in Farrington).
  Produces daily expected counts, prediction bounds and a 7-day exceedance test.
* ``farrington_flexible``: the improved Farrington algorithm of Noufaily et al.
  (2013), the standard at UKHSA and many European agencies, on 7-day totals.
* ``ears``: CDC Early Aberration Reporting System C1, C2, C3.
* ``cusum``: one-sided CUSUM on standardised regression residuals for
  gradual, sustained increases.

References: Farrington et al. JRSS-A 1996; Noufaily et al. Stat Med 2013;
Hutwagner et al. J Urban Health 2003 (EARS); Burkom et al. 2007 (ESSENCE
regression); Page 1954 (CUSUM).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import stats

from .glm import anscombe_residuals, fit_poisson, nb_quantile, nb_sf


@dataclass
class DetectorResult:
    detector: str
    alarm: bool
    p_value: float
    score: float               # >1 means above threshold
    observed: float
    expected: float
    upper: float
    window_days: int
    details: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- regression
def seasonal_profile(y: np.ndarray, doy: np.ndarray, year: np.ndarray, exclude_from: int, window: int = 15) -> np.ndarray | None:
    """Leave-one-year-out same-season baseline.

    For each day, the average of the centred ``window``-day moving average at the
    same day of year in *other* years. Days from ``exclude_from`` onward (the
    evaluation window) never contribute, so a current outbreak cannot leak in.
    Captures recurring features that smooth harmonics miss, such as the
    back-to-school rise in respiratory illness or holiday dips.
    """
    yy = y.astype(float).copy()
    yy[exclude_from:] = np.nan
    valid = ~np.isnan(yy)
    k = np.ones(window)
    num = np.convolve(np.where(valid, yy, 0.0), k, mode="same")
    den = np.convolve(valid.astype(float), k, mode="same")
    ma = np.where(den >= window / 2, num / np.maximum(den, 1), np.nan)
    d = np.minimum(doy, 365) - 1
    years = np.unique(year)
    if len(years) < 2:
        return None
    S = np.zeros(365)
    N = np.zeros(365)
    ok = ~np.isnan(ma)
    np.add.at(S, d[ok], ma[ok])
    np.add.at(N, d[ok], 1)
    own = np.where(ok, ma, 0.0)
    cnt = N[d] - ok
    prof = np.where(cnt > 0, (S[d] - own) / np.maximum(cnt, 1), np.nan)
    if np.isnan(prof).all():
        return None
    fill = np.nanmean(prof)
    return np.where(np.isnan(prof), fill, prof)


def _design(t: np.ndarray, dow: np.ndarray, holiday: np.ndarray, harmonics: int = 2,
            profile: np.ndarray | None = None, term: np.ndarray | None = None) -> np.ndarray:
    cols = [np.ones_like(t, dtype=float), t / 365.25]
    if term is not None:
        # academic calendar: in-term indicator and a decaying return-to-school effect
        for k in range(term.shape[1]):
            if term[:, k].std() > 0:
                cols.append(term[:, k])
    if profile is not None:
        lp = np.log(profile + 0.5)
        cols.append((lp - lp.mean()) / (lp.std() + 1e-9))
    for k in range(1, harmonics + 1):
        cols.append(np.sin(2 * np.pi * k * t / 365.25))
        cols.append(np.cos(2 * np.pi * k * t / 365.25))
    for d in range(1, 7):
        cols.append((dow == d).astype(float))
    cols.append(holiday.astype(float))
    return np.column_stack(cols)


@dataclass
class RegressionOutput:
    expected: np.ndarray         # for evaluation window
    upper: np.ndarray
    phi: float
    daily_p: np.ndarray
    z: np.ndarray
    result7: DetectorResult
    fitted_days: int
    mu_all: np.ndarray | None = None   # fitted/predicted mean for every day up to T


def rare_event_test(y: np.ndarray, T: int, alpha: float = 0.01, window: int = 7, baseline_days: int = 365,
                    min_count: int = 3) -> DetectorResult:
    """Exact Poisson test for rare events whose baseline is (almost) zero, e.g. Legionella positives."""
    o = float(y[T - window + 1:T + 1].sum())
    base = y[max(0, T - baseline_days - 14):T - 13]
    rate = (base.sum() + 0.5) / max(len(base), 1)
    e = rate * window
    p = float(stats.poisson.sf(o - 1, e))
    ub = float(stats.poisson.ppf(1 - alpha, e))
    return DetectorResult("rare_event_poisson", bool(p < alpha and o >= min_count), p, (o - e) / max(ub - e, 1e-9), o, e, ub,
                          window, {"baseline_rate_per_day": round(rate, 4)})


def regression_detector(y: np.ndarray, t: np.ndarray, dow: np.ndarray, holiday: np.ndarray, T: int,
                        eval_days: int = 14, history: int = 730, alpha: float = 0.01,
                        min_history: int = 84, doy: np.ndarray | None = None,
                        year: np.ndarray | None = None, term: np.ndarray | None = None) -> RegressionOutput | None:
    """y, t, dow, holiday are full-length daily arrays; T is the analysis-day index."""
    # Fit on history that ends before the evaluation window, so a current
    # outbreak cannot inflate its own baseline (the window acts as guard band).
    train_end = T - eval_days
    train_start = max(0, train_end - history)
    if train_end - train_start < min_history:
        return None
    tr = slice(train_start, train_end + 1)
    ev = slice(T - eval_days + 1, T + 1)
    profile = None
    if doy is not None and year is not None and train_end - train_start >= 400:
        profile = seasonal_profile(y[:T + 1], doy[:T + 1], year[:T + 1], T - eval_days + 1)
    harmonics = (1 if profile is not None else 2) if train_end - train_start >= 540 else (1 if train_end - train_start >= 300 else 0)
    X = _design(t[:T + 1], dow[:T + 1], holiday[:T + 1], harmonics, profile,
                term[:T + 1] if term is not None else None)
    Xtr, ytr = X[tr], y[tr]
    if ytr.sum() == 0:
        return None
    fit = fit_poisson(Xtr, ytr)
    res = anscombe_residuals(ytr, fit.mu, fit.phi, fit.hat)
    w = np.where(res > 2.58, 1.0 / np.maximum(res, 1e-6) ** 2, 1.0)
    w = w * len(w) / w.sum()
    fit = fit_poisson(Xtr, ytr, weights=w, start=fit.beta)
    # Long-run level. With a same-season profile, a linear trend is dropped: one unusually severe season can
    # tilt it and the tilt would be extrapolated into the evaluation window. Level drift is handled instead by
    # recalibrating to the last four weeks of training data (shrunk towards no change).
    trend_kept = profile is None
    if not trend_kept:
        X = np.delete(X, 1, axis=1)
        Xtr = X[tr]
        fit = fit_poisson(Xtr, ytr, weights=w)
    Xev = X[ev]
    recent = slice(max(train_start, train_end - 27), train_end + 1)
    o_r, e_r = float(y[recent].sum()), float(np.exp(np.clip(X[recent] @ fit.beta, -30, 30)).sum())
    level = float(np.clip((o_r + 10) / (e_r + 10), 0.67, 1.5))
    mu = np.exp(np.clip(Xev @ fit.beta, -30, 30)) * level
    phi = fit.phi
    yev = y[ev]
    upper = nb_quantile(1 - alpha, mu, phi)
    daily_p = nb_sf(yev, mu, phi)
    z = (yev - mu) / np.sqrt(phi * np.maximum(mu, 1e-9))
    o7, e7 = yev[-7:].sum(), mu[-7:].sum()
    p7 = float(nb_sf(o7, e7, phi))
    u7 = float(nb_quantile(1 - alpha, e7, phi))
    r7 = DetectorResult("regression_7d", bool(o7 > u7 and o7 >= 3), p7, float((o7 - e7) / max(u7 - e7, 1e-9)),
                        float(o7), float(e7), u7, 7, {"phi": round(phi, 3), "harmonics": harmonics,
                                                       "seasonal_profile": profile is not None,
                                                       "trend": bool(trend_kept), "recalibration": round(level, 3),
                                                       "training_days": int(train_end - train_start + 1)})
    out = RegressionOutput(mu, upper, phi, daily_p, z, r7, int(train_end - train_start + 1))
    out.mu_all = np.exp(np.clip(X @ fit.beta, -30, 30))
    out.mu_all[T - eval_days + 1:] *= level
    return out


# --------------------------------------------------------------------------- Farrington flexible
def farrington_flexible(daily: np.ndarray, T: int, b: int = 3, w: int = 3, alpha: float = 0.01,
                        no_periods: int = 10, past_weeks_not_included: int = 26) -> DetectorResult | None:
    """Improved Farrington (Noufaily 2013) on 7-day blocks ending at day T."""
    n_weeks = (T + 1) // 7
    if n_weeks < 52 + w + 2:
        return None
    weekly = np.array([daily[T - 7 * k - 6:T - 7 * k + 1].sum() for k in range(n_weeks)])  # weekly[0] = current
    b_eff = min(b, (n_weeks - w - 1) // 52)
    ks = np.arange(past_weeks_not_included, min(52 * b_eff + w + 1, n_weeks))
    if len(ks) < 30:
        return None
    dist = ((ks + 26) % 52) - 26
    period = np.where(np.abs(dist) <= w, 0, 0)
    outside = np.abs(dist) > w
    # split the off-season weeks into (no_periods - 1) equal blocks by position in the year
    pos = (dist[outside] + 26) % 52
    period[outside] = 1 + np.minimum((pos * (no_periods - 1)) // 52, no_periods - 2)
    y = weekly[ks].astype(float)
    tt = -ks.astype(float)
    P = np.zeros((len(ks), no_periods - 1))
    for j in range(1, no_periods):
        P[:, j - 1] = (period == j)

    def fit(with_trend: bool):
        cols = [np.ones(len(ks))] + ([tt] if with_trend else []) + [P[:, j] for j in range(P.shape[1]) if P[:, j].any()]
        X = np.column_stack(cols)
        f = fit_poisson(X, y)
        res = anscombe_residuals(y, f.mu, f.phi, f.hat)
        wts = np.where(res > 2.58, 1.0 / np.maximum(res, 1e-6) ** 2, 1.0)
        wts = wts * len(wts) / wts.sum()
        f = fit_poisson(X, y, weights=wts)
        x0 = np.zeros(X.shape[1])
        x0[0] = 1.0  # t = 0, period 0 (reference level)
        eta0 = float(x0 @ f.beta)
        var_eta = float(x0 @ f.cov_unscaled @ x0) * f.phi
        return f, eta0, var_eta, X

    f, eta0, var_eta, X = fit(True)
    trend_used = True
    se_trend = np.sqrt(f.cov_unscaled[1, 1] * f.phi)
    if abs(f.beta[1] / max(se_trend, 1e-12)) < 1.96 or np.exp(eta0) > y.max():
        f, eta0, var_eta, X = fit(False)
        trend_used = False
    mu0 = float(np.exp(eta0))
    phi = f.phi
    ub = float(nb_quantile(1 - alpha, mu0, phi))
    y0 = float(weekly[0])
    last4 = weekly[:4].sum()
    alarm = bool(y0 > ub and last4 > 5)
    p = float(nb_sf(y0, mu0, phi))
    return DetectorResult("farrington_flexible", alarm, p, float((y0 - mu0) / max(ub - mu0, 1e-9)), y0, mu0, ub, 7,
                          {"phi": round(phi, 3), "trend": trend_used, "reference_weeks": int(len(ks)),
                           "years_back": int(b_eff)})


# --------------------------------------------------------------------------- EARS
def ears(daily: np.ndarray, T: int) -> dict[str, DetectorResult]:
    """C1 (no guard), C2 (2-day guard), C3 (sum of recent C2 exceedances)."""
    out = {}

    def c_stat(t, guard):
        base = daily[t - guard - 7:t - guard]
        if len(base) < 7:
            return None, None, None
        m, s = base.mean(), base.std(ddof=1)
        s = max(s, 0.5)
        return (daily[t] - m) / s, m, s

    c1, m1, s1 = c_stat(T, 0)
    c2, m2, s2 = c_stat(T, 2)
    if c1 is None or c2 is None:
        return out
    out["ears_c1"] = DetectorResult("ears_c1", bool(c1 > 3 and daily[T] >= 3), float(stats.norm.sf(c1)), c1 / 3,
                                    float(daily[T]), float(m1), float(m1 + 3 * s1), 1)
    out["ears_c2"] = DetectorResult("ears_c2", bool(c2 > 3 and daily[T] >= 3), float(stats.norm.sf(c2)), c2 / 3,
                                    float(daily[T]), float(m2), float(m2 + 3 * s2), 1)
    c3 = 0.0
    for lag in range(3):
        c, _, _ = c_stat(T - lag, 2)
        if c is not None:
            c3 += max(0.0, c - 1)
    out["ears_c3"] = DetectorResult("ears_c3", bool(c3 > 2 and daily[T - 2:T + 1].sum() >= 4), float(np.exp(-c3)), c3 / 2,
                                    float(daily[T - 2:T + 1].sum()), float(3 * m2), float(3 * m2 + 2 * s2), 3)
    return out


# --------------------------------------------------------------------------- CUSUM
def cusum(z: np.ndarray, k: float = 0.5, h: float = 4.0) -> DetectorResult:
    s = 0.0
    path = []
    for zi in z:
        s = max(0.0, s + float(zi) - k)
        path.append(s)
    alarm = s > h
    return DetectorResult("cusum", bool(alarm), float(np.exp(-max(s - h, 0))) if alarm else 1.0, s / h, s, 0.0, h,
                          len(z), {"path": [round(x, 2) for x in path], "k": k, "h": h})


def benjamini_hochberg(p: np.ndarray, q: float) -> np.ndarray:
    p = np.asarray(p, float)
    n = len(p)
    if n == 0:
        return np.zeros(0, bool)
    order = np.argsort(p)
    thresh = q * (np.arange(1, n + 1)) / n
    passed = p[order] <= thresh
    k = np.max(np.nonzero(passed)[0]) + 1 if passed.any() else 0
    out = np.zeros(n, bool)
    out[order[:k]] = True
    return out
