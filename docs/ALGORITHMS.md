# Algorithms

Shield AI uses the statistical methods that public-health agencies already trust (CDC, UKHSA, ECDC, NYC DOHMH). They are combined so each method covers another's blind spots. Every number in an alert can be traced back to one of these methods, and the agent trace records which ones ran.

## 1. Syndromic classification (`shield/nlp/syndromes.py`)

- **Inputs:** ICD-10 codes and free-text triage chief complaints.
- **Syndromes:** influenza-like illness, respiratory, gastrointestinal, fever, fever-and-rash, meningitis/encephalitis-like, and other. The other category is kept as the denominator. A severe-respiratory flag marks pneumonia or respiratory-failure codes, or an admission with respiratory illness.
- **Rules:** diagnosis codes take precedence. Text upgrades a coded respiratory visit to ILI when the triage note says fever plus cough. A record coded as non-syndromic stays non-syndromic unless the text describes a febrile syndrome.
- **Negation:** NegEx-style. "rash itchy no fever" is not fever-and-rash illness.
- **Validation:** above 90% accuracy against the simulator's ground truth (`tests/test_syndromes.py`). A real deployment must re-validate against a chart-reviewed sample of local records, because coding and language differ.

## 2. Temporal detection (`shield/detect/temporal.py`)

| Method | What it catches | Notes |
|---|---|---|
| **Robust quasi-Poisson regression** | Daily and 7-day excess against an expectation | **Covariates:** annual harmonics, day of week, holidays, the academic calendar (an in-term indicator and a decaying back-to-school effect), and a leave-one-year-out same-season profile. **Training:** up to 2 years, ending before the 14-day assessment window, so a current outbreak cannot hide in its own baseline. **Level:** recalibrated to the last 4 weeks of training data, instead of a linear trend that one severe season could tilt. **Robustness:** past outbreaks are down-weighted with Anscombe residuals. **Thresholds:** negative-binomial quantile with estimated over-dispersion. |
| **Farrington Flexible** (Noufaily et al. 2013) | Weekly excess against the same season in up to 3 previous years | The standard at UKHSA and many European agencies. It uses a 10-period seasonal factor, a trend only when significant, re-weighting of past outbreaks, and a negative-binomial threshold. It needs more than 5 cases in 4 weeks. |
| **EARS C1/C2/C3** (CDC) | Sharp onsets against a 7-day baseline | Used only for series averaging at least 1 a day, and only together with CUSUM and a supporting regression p-value. On their own they are noisy. |
| **CUSUM** | Small, sustained increases | Runs on standardised regression residuals (k = 0.5, h = 4). |

**Multiple testing:** a city-wide run tests hundreds of series. Benjamini-Hochberg controls the false-discovery rate (default 5%) across all regression and Farrington tests together.

**Rare events:** some analytes are close to zero at baseline, such as Legionella antigen. They use an exact Poisson test against the previous year's rate.

## 3. Spatial and space-time detection (`shield/detect/scan.py`)

- **Space-time permutation scan** (Kulldorff et al. 2005, as in SaTScan). Needs only case counts. It finds clusters unusual in space and time together, adjusting for purely spatial and purely temporal variation. It uses prospective cylinders ending today, windows of 1–14 days, circular zones up to 50% of the population, and 999 Monte Carlo permutations.
- **Expectation-based Poisson scan** (Neill et al. 2005). Compares observed counts in circular zones with the regression expectations. It finds localised excess even during a city-wide rise. It uses a parametric bootstrap and also reports a secondary, non-overlapping cluster, as SaTScan does.
- Clusters must reach p ≤ 0.005, which is roughly one false cluster every 200 days per scan.

## 4. Laboratory detection

- **Positives per analyte:** a seasonal regression, or the exact Poisson test for rare analytes.
- **Unexplained respiratory illness:** the count of respiratory panels negative for every analyte, plus a binomial test of the negative share against the previous four weeks. This is the signature of a new pathogen: many sick people, and the usual viruses not found.

## 5. Fusion and linking (`shield/agent/fusion.py`, `orchestrator.py`)

- **Locus signals** are neighbourhoods, schools and universities, and scan zones. They are grouped when their case sets overlap (overlap coefficient ≥ 0.4).
- **Context signals** are city-wide, age-band and facility signals. They attach to the event that explains at least 40% of their excess.
- **Facility-only signals** become their own events, either facility-level clusters or data artefacts.
- **Linking:** candidates that are one spreading event are merged and re-assessed together. This applies when they are adjacent or share a university population, have similar age profiles, and are best explained by a transmissible pathogen.

## 6. Characterisation (`shield/agent/characterize.py`)

- **Epidemic curve:** against the same robust regression fitted to the exact case definition, with a 99% prediction band.
- **Nowcast:** per-source empirical reporting-delay distributions correct the last 7 days.
- **Growth:** a Poisson log-linear fit on excess incidence over 14 days gives the growth rate and doubling time with a 95% CI.
- **Rt:** Cori et al. (2013), the EpiEstim method, on excess incidence. It uses a Gamma(1, 5) prior, a 7-day window, and a family-specific serial interval.
- **Profiles:** age (chi-square against the previous 8 weeks), sex, neighbourhoods and schools with relative risks, and severity with a binomial test.
- **Laboratory:** results for the case-patients compared with the baseline.
- **Sources and coding:** the share of excess per institution, a cross-check against other institutions serving the same residents, diagnosis-code mix shifts, and free-text versus code disagreement.

## 7. Differential diagnosis (`shield/agent/hypotheses.py`)

- **Scoring:** a transparent Bayesian scoring over pathogen profiles (`knowledge/pathogens.json`), a data-artefact explanation and a chance-variation explanation.
- **Evidence ledger:** each piece of evidence adds a log-likelihood ratio, and the interface shows every term. Evidence covers age profile (a multinomial likelihood under a mixture of baseline and pathogen-specific excess), season, laboratory results (including all-negative panels for novel pathogens), transmission pattern (growth versus point source), setting, severity, sex ratio, source concentration, coding shifts and statistical significance.
- **Learned priors:** priors move with locally confirmed causes from feedback.
- **Limits:** probabilities are relative among listed explanations, not a clinical diagnosis.

## 8. Confidence and learning (`shield/agent/confidence.py`, `feedback.py`)

- **Model:** a logistic model over 12 interpretable features.
  - Raise confidence: statistical strength, agreement between detector families, number of institutions with a statistically meaningful excess, laboratory support, persistence, size, growth, the event's own significance, and relative increase.
  - Lower confidence: single-source concentration, coding anomaly, and the differential's probabilities of chance variation and of a data artefact.
- **Training:** it starts from expert weights. After every piece of feedback it is re-fitted as maximum a posteriori with a Gaussian prior around those weights, so a few local examples adjust it without destabilising it.
- **Feedback also updates:**
  - per-detector positive predictive value (Beta-Binomial);
  - per-family false-alarm rate, made stricter when a family keeps producing false alarms and never looser than configured;
  - local cause priors;
  - local history, which is cited in future alerts.

## 9. Notification policy

- **Persistence rule.** An alert is notified when its confidence is at least 50% on two consecutive daily assessments.
- **Immediate notification.** At 80% confidence or more, if more than one institution sees the rise, notification is immediate. A notifiable diagnosis recorded by clinicians also notifies immediately.
- **Single-source rises.** A rise seen by only one institution must always persist for a second day, because one source can change its systems.
- **Watch list.** Everything else stays on a watch list.
- **Why.** Operational syndromic-surveillance teams use similar persistence rules. They cut one-day statistical blips without delaying strong signals.

## 10. Historical context (`shield/agent/history.py`, `knowledge/historical_epidemics.json`)

- **Matching:** past outbreaks are matched to the leading explanations.
- **Content:** published cases, hospitalisations, deaths, economic and social impact, and lessons, each with sources.
- **Projections:** where the source supports per-capita rates (2009 H1N1 in the US, Milwaukee 1993, Samoa 2019, 1918), these are scaled to the affected neighbourhoods and to the whole city. They are illustrations, not forecasts.

## Known limitations

- **Simulated validation only.** Parameters were tuned on simulated data. A real deployment needs a validation period: run silently alongside existing surveillance for 3–6 months, compare against known events, then calibrate thresholds and the confidence model with local feedback.
- **Hand-set knowledge bases.** Pathogen profiles and their likelihood contributions were set by hand from public-health references. National experts should review them.
- **No forecasting.** The MVP describes what is happening; it does not project the epidemic forward.
- **Spatial resolution.** It is limited to neighbourhoods. Finer resolution means more statistical noise and more re-identification risk.
