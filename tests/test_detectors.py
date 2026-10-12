import numpy as np
import pytest

from shield.detect import epi, scan, temporal
from shield.detect.glm import fit_poisson


def _series(n=900, seed=0, base=20.0):
    rng = np.random.default_rng(seed)
    t = np.arange(n, dtype=float)
    dow = t.astype(int) % 7
    season = 1 + 0.3 * np.cos(2 * np.pi * (t - 15) / 365.25)
    mu = base * season * np.where(dow == 6, 0.7, 1.0)
    return rng.poisson(mu).astype(float), t, dow, np.zeros(n, bool)


def test_irls_matches_statsmodels():
    sm = pytest.importorskip("statsmodels.api")
    rng = np.random.default_rng(3)
    X = np.column_stack([np.ones(400), rng.normal(size=400), rng.normal(size=400)])
    y = rng.poisson(np.exp(X @ np.array([1.0, 0.3, -0.2])))
    ours = fit_poisson(X, y)
    ref = sm.GLM(y, X, family=sm.families.Poisson()).fit()
    assert np.allclose(ours.beta, ref.params, atol=1e-5)
    assert np.allclose(np.sqrt(np.diag(ours.cov_unscaled)), ref.bse, rtol=1e-3)


def test_regression_detector_flags_outbreak_and_is_quiet_without_one():
    y, t, dow, hol = _series()
    T = len(y) - 1
    quiet = temporal.regression_detector(y, t, dow, hol, T)
    assert quiet.result7.p_value > 0.001
    y2 = y.copy()
    y2[T - 6:T + 1] += 15
    out = temporal.regression_detector(y2, t, dow, hol, T)
    assert out.result7.alarm and out.result7.p_value < 1e-6


def test_false_alarm_rate_is_controlled():
    alarms = 0
    for seed in range(60):
        y, t, dow, hol = _series(seed=seed)
        if temporal.regression_detector(y, t, dow, hol, len(y) - 1, alpha=0.01).result7.alarm:
            alarms += 1
    assert alarms <= 5  # nominal 1%; allow sampling noise


def test_farrington_flexible():
    y, *_ = _series(n=1200, seed=5, base=10)
    T = len(y) - 1
    assert not temporal.farrington_flexible(y, T).alarm
    y[T - 6:T + 1] += 12
    r = temporal.farrington_flexible(y, T)
    assert r.alarm and r.details["reference_weeks"] > 50


def test_ears_and_cusum():
    y = np.full(60, 10.0)
    y[::2] += 2
    y[-1] = 30
    r = temporal.ears(y, len(y) - 1)
    assert r["ears_c2"].alarm
    c = temporal.cusum(np.array([0.2, 1.5, 1.8, 2.0, 2.2]))
    assert c.alarm and c.observed > 4


def test_benjamini_hochberg():
    p = np.array([0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205, 0.212, 0.216])
    assert temporal.benjamini_hochberg(p, 0.05).sum() == 2


def test_space_time_permutation_scan_finds_cluster():
    rng = np.random.default_rng(1)
    cents = np.array([[x, y] for y in range(3) for x in range(4)], float)
    zones = scan.circular_zones(cents, 0.5)
    regions = rng.integers(0, 12, 3000)
    days = rng.integers(0, 56, 3000)
    regions = np.concatenate([regions, np.full(60, 5)])
    days = np.concatenate([days, rng.integers(50, 56, 60)])
    res = scan.space_time_permutation_scan(regions, days, 12, 56, zones, 14, 199, rng)
    assert 5 in res.zones and res.p_value <= 0.01
    null = scan.space_time_permutation_scan(rng.integers(0, 12, 3000), rng.integers(0, 56, 3000), 12, 56, zones, 14, 99, rng)
    assert null.p_value > 0.01


def test_expectation_based_scan():
    cents = np.array([[x, y] for y in range(3) for x in range(4)], float)
    zones = scan.circular_zones(cents, 0.5)
    exp = np.full(12, 20.0)
    obs = exp.copy()
    obs[[8, 9]] += 25
    res = scan.expectation_based_poisson_scan(obs, exp, zones, 199, np.random.default_rng(2))
    assert set(res.zones) >= {8} and res.p_value <= 0.01


def test_rt_and_growth_recover_known_dynamics():
    # incidence growing 10% per day -> doubling about 7 days
    inc = np.round(5 * np.exp(0.1 * np.arange(40)))
    g = epi.growth(inc, 14)
    assert abs(g["doubling_time_days"] - np.log(2) / 0.1) < 1.0
    rt = epi.cori_rt(inc, 5.0, 2.5)
    assert rt["rt_lo"] > 1.3
    flat = np.full(40, 20.0)
    assert abs(epi.cori_rt(flat, 5.0, 2.5)["rt"] - 1.0) < 0.15
