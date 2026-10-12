"""Generalised linear models fitted by iteratively re-weighted least squares.

Implemented directly in NumPy so every step is inspectable and dependency
light; the test-suite checks the estimates against statsmodels.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import stats


@dataclass
class PoissonFit:
    beta: np.ndarray
    cov_unscaled: np.ndarray
    mu: np.ndarray
    phi: float
    df_resid: int
    converged: bool
    hat: np.ndarray


def fit_poisson(X: np.ndarray, y: np.ndarray, weights: np.ndarray | None = None, offset: np.ndarray | None = None,
                max_iter: int = 50, tol: float = 1e-7, ridge: float = 1e-8, start: np.ndarray | None = None) -> PoissonFit:
    n, p = X.shape
    w = np.ones(n) if weights is None else np.asarray(weights, float)
    off = np.zeros(n) if offset is None else offset
    if start is not None:
        beta = np.asarray(start, float)
        eta = X @ beta + off
    else:
        eta = np.log(np.maximum(y, 0) + 0.5)
        beta = np.zeros(p)
    converged = False
    for _ in range(max_iter):
        mu = np.exp(np.clip(eta, -30, 30))
        z = eta - off + (y - mu) / mu
        W = w * mu
        XtW = X.T * W
        A = XtW @ X + ridge * np.eye(p)
        new = np.linalg.solve(A, XtW @ z)
        eta_new = X @ new + off
        if np.max(np.abs(new - beta)) < tol * (1 + np.max(np.abs(beta))):
            beta, eta = new, eta_new
            converged = True
            break
        beta, eta = new, eta_new
    mu = np.exp(np.clip(eta, -30, 30))
    W = w * mu
    XtW = X.T * W
    cov = np.linalg.pinv(XtW @ X + ridge * np.eye(p))
    hat = np.einsum("ij,jk,ik->i", X * np.sqrt(W)[:, None], cov, X * np.sqrt(W)[:, None])
    df = max(int((w > 0).sum()) - p, 1)
    pearson = (w * (y - mu) ** 2 / np.maximum(mu, 1e-12)).sum()
    phi = max(1.0, pearson / df)
    return PoissonFit(beta, cov, mu, phi, df, converged, hat)


def nb_sf(x: np.ndarray | float, mu: np.ndarray | float, phi: np.ndarray | float) -> np.ndarray:
    """P(X >= x) for a quasi-Poisson count with mean mu and variance phi*mu (negative binomial)."""
    x = np.asarray(x, float)
    mu = np.maximum(np.asarray(mu, float), 1e-9)
    phi = np.asarray(phi, float)
    out = np.where(phi <= 1.0001, stats.poisson.sf(x - 1, mu),
                   stats.nbinom.sf(x - 1, mu / np.maximum(phi - 1, 1e-9), 1 / np.maximum(phi, 1.0001)))
    return out


def nb_quantile(q: float, mu: np.ndarray | float, phi: np.ndarray | float) -> np.ndarray:
    mu = np.maximum(np.asarray(mu, float), 1e-9)
    phi = np.asarray(phi, float)
    return np.where(phi <= 1.0001, stats.poisson.ppf(q, mu),
                    stats.nbinom.ppf(q, mu / np.maximum(phi - 1, 1e-9), 1 / np.maximum(phi, 1.0001)))


def anscombe_residuals(y: np.ndarray, mu: np.ndarray, phi: float, hat: np.ndarray) -> np.ndarray:
    mu = np.maximum(mu, 1e-9)
    r = 1.5 * (np.power(y, 2 / 3) * np.power(mu, -1 / 6) - np.sqrt(mu))
    return r / np.sqrt(phi * np.maximum(1 - hat, 1e-6))
