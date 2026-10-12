# Architecture

Shield AI is a single deployable application that runs entirely inside the deploying country's infrastructure. This document describes the components, the data flow, and what changes between the MVP and a national production deployment.

## Components

```
 Institutions (in-country)                     Shield AI instance (in-country region)
 ┌──────────────────────────┐   mTLS / VPN   ┌────────────────────────────────────────────────────────────┐
 │ Hospitals (FHIR R4)      │ ─────────────▶ │ Connectors  /api/ingest/{fhir,hl7v2,claims,deaths,registry}│
 │ Clinics (HL7 v2.5.1)     │                │   • source authentication (per-source key; mTLS in prod)   │
 │ University health (FHIR) │                │   • parse → validate                                       │
 │ Public health lab (FHIR) │                ├────────────────────────────────────────────────────────────┤
 │ Health insurers (claims) │                │ Ingestion pipeline                                         │
 │ Life insurers (deaths)   │                │   • raw message → AES-256-GCM envelope → raw_messages      │
 │ Education registry       │                │   • national ID → HMAC pseudonym; DOB → age; postcode →    │
 └──────────────────────────┘                │     neighbourhood; names/addresses dropped                 │
                                             │   • syndrome classifier (codes + triage text, negation)    │
                                             │   • cross-source de-duplication (claims ↔ EHR, deaths)     │
                                             ├────────────────────────────────────────────────────────────┤
                                             │ Platform database (pseudonymised events, labs, enrolment,  │
                                             │ census, alerts, feedback, model state, audit log)          │
                                             ├────────────────────────────────────────────────────────────┤
                                             │ Surveillance agent (daily, or on demand)                   │
                                             │   load → detect → fuse → characterise → diagnose →         │
                                             │   confidence → biases → history → brief → persist/notify   │
                                             ├────────────────────────────────────────────────────────────┤
                                             │ API + web app (RBAC, small-cell suppression, audit)        │
                                             │ Egress guard + residency checks                            │
                                             └────────────────────────────────────────────────────────────┘
                                                        │ in-app inbox / in-country webhooks only
                                                        ▼
                                             Ministry of Health, public-health agency
```

## Code map

| Path | Purpose |
|---|---|
| `shield/ingest/` | Connectors (`fhir.py`, `hl7v2.py`, `claims.py`) and the normalisation pipeline (`pipeline.py`) |
| `shield/nlp/syndromes.py` | Syndrome classifier (ICD-10 rules, triage-text rules, NegEx-style negation) |
| `shield/detect/` | Statistical methods: GLM/IRLS, temporal detectors, scan statistics, Rt, growth, nowcasting |
| `shield/agent/` | The agent: data view, detection stage, fusion, characterisation, differential diagnosis, confidence and learning, biases, historical context, narrative, notifications, orchestrator |
| `shield/security/` | Key management and envelope encryption, pseudonymisation, audit chain, authentication/RBAC, disclosure control, residency checks and egress guard |
| `shield/knowledge/` | Pathogen profiles and the historical-outbreak knowledge base (with sources) |
| `shield/api/main.py` | HTTP API, security headers, web-app hosting |
| `shield/web/` | Single-page interface and SVG charts; no third-party code |
| `shield/synth/` | The Riverton simulator: population, encounters, labs, claims, deaths, outbreak scenarios, native-format feeds |
| `shield/demo.py` | Seed, replay through the connectors, rewind |
| `shield/evaluation.py` | Compares alerts with simulation ground truth |

## Data model (platform database)

| Table | Contents | Identifiability |
|---|---|---|
| `events` | One row per encounter, claim or death: pseudonym, age, age band, sex, neighbourhood, facility, date, encounter class, triage text, ICD-10 codes, syndrome, admission/ICU/death flags, de-duplication link | Pseudonymised |
| `labs` | Laboratory panel, analyte (LOINC), result, collection date, age band, neighbourhood | Pseudonymised |
| `enrolment` | Pseudonym → school/university | Pseudonymised |
| `raw_messages` | Original source message, AES-256-GCM encrypted with a per-message key | Identifiable, encrypted, break-glass only |
| `population`, `geography`, `institutions` | Census counts by neighbourhood/age/sex, polygons, enrolment totals | Aggregate |
| `alerts`, `alert_cases`, `agent_runs` | Reports, the records behind each alert, full agent traces | Pseudonymised |
| `feedback`, `model_state` | Government feedback and everything the model learned from it | — |
| `audit_log` | Hash-chained log of every access and action | — |

## The agent loop

The agent is a deterministic, auditable pipeline. Each run stores a trace of every step. See [ALGORITHMS.md](ALGORITHMS.md) for the methods.

1. **Load** the records received up to the analysis date. Records still in transit are invisible, exactly as in live operation.
2. **Detect.** About 280 syndrome × population series are tested: city-wide, 8 age bands, 12 neighbourhoods, 11 schools/universities and 8 facilities, across 8 syndromes. Each series runs through 4 temporal detectors. Spatial scans run over 6 syndromes, plus laboratory detectors. False discoveries are controlled across all series.
3. **Fuse** signals into candidate events, then **link** candidates that are one spreading event.
4. **Characterise** each event: the curve against the model baseline, growth and Rt, who, where, schools, severity, laboratory results, which institutions see it, and coding shifts.
5. **Diagnose** with an evidence ledger, **score confidence** with the feedback-trained model, **assess biases**, and **find historical analogues**.
6. **Brief, persist and notify.** Existing alerts are updated as evidence accumulates; new ones are created only for new events.

## MVP versus production

| Concern | MVP | Production |
|---|---|---|
| Database | SQLite file | PostgreSQL (in-country, encrypted volumes, replicas in a second in-country DC) |
| Keys | Files with 0600 permissions | In-country HSM or sovereign-cloud KMS; key ceremonies; rotation |
| Identity | Username/password, signed tokens | Government IdP via OpenID Connect, hardware MFA, short sessions |
| Transport | HTTP on localhost | TLS 1.3 everywhere; mTLS per institution; private network/VPN |
| Connectors | Push endpoints + simulated replay | FHIR Subscriptions and Bulk $export clients, MLLP listener, SFTP batch pickup, interface engine (e.g. Mirth/Rhapsody) where institutions need it |
| Scheduling | Run on demand / per simulated day | Scheduler (daily at minimum, hourly for high-risk periods), queue-based ingestion |
| Scale | ~200,000 residents, ~1.9 million records | Partition by region and time; columnar store for analytics; the agent parallelises per syndrome and region |
| Narrative | Deterministic templates | Templates plus optional in-country open-weights LLM for plain-language summaries |
