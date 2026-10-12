# Deployment

## Local demo (laptop)

Requirements: Python 3.11+, about 1.5 GB free disk and about 4 GB RAM.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
make demo        # builds Riverton, back-fills history, replays 26 days through the connectors (about 10-15 minutes)
make serve       # http://127.0.0.1:8000  (users: minister / epi / steward / auditor, password shield-demo)
make test
```

`make demo-live` stops the clock at 2026-09-14, the last day of the historical back-fill and before any alert, so you can press **Next day** (or **Advance 7 days** on the Demo control page) during a presentation and watch the agent find the outbreaks.

## Container

```bash
docker compose build
docker compose run --rm shield python -m shield.cli seed     # one-off: build the demo data in the volume
docker compose up -d                                          # serves on 127.0.0.1:8000
```

Put an in-country TLS-terminating reverse proxy or load balancer in front of it.

## Configuration

Copy `config/shield.example.toml` to `config/shield.toml`, or set `SHIELD_CONFIG`. The keys that matter most:

| Key | Meaning |
|---|---|
| `deployment.environment` | `demo`, `staging` or `production`. Production disables demo controls and refuses to start if a critical sovereignty check fails. |
| `sovereignty.country_code`, `jurisdiction` | Shown everywhere and written on exports. |
| `sovereignty.hosting_region`, `allowed_storage_regions` | The platform checks it is running in an approved region. |
| `sovereignty.allowed_egress` | The only network destinations the process may contact (CIDR or hostname glob). |
| `security.min_cell_size` | Small-cell suppression threshold for aggregate views. |
| `agent.alert_alpha`, `fdr_q`, `scan_replicates`, `notify_min_confidence` | Detection sensitivity, false-discovery rate, scan precision and notification threshold. |
| `narrative.backend` | `template` (default) or `local_llm`, an OpenAI-compatible endpoint hosted in-country. |
| `notifications.webhooks` | In-country endpoints (for example the ministry incident system) to receive alert summaries. |

Environment overrides: `SHIELD_DATA_DIR`, `SHIELD_ENV`, `SHIELD_CONFIG`, `SHIELD_SCAN_REPLICATES`.

## Connecting real institutions (pilot city)

1. **Reference data.**
   - Load census population by neighbourhood, age band and sex.
   - Load national holidays and the school/university academic calendar (`kv` keys `holidays` and `academic_breaks`).
   - Load neighbourhood boundaries and the postcode-to-neighbourhood lookup, and the school and university list.
   - The demo loads these from `shield/synth/city.py` through `shield/reference.py`. Write the same tables from the national statistics office's files.
2. **Register each institution** in `sources` and issue it a credential. Production uses mTLS certificates.
3. **Historical back-fill.**
   - Ask each hospital for a FHIR Bulk Data `$export` of Encounter, Condition, Patient (identifiers, birth date, sex, postcode only) and Observation/DiagnosticReport covering at least 2 years, ideally 3.
   - Load it with `IngestPipeline.bulk_events` and `bulk_labs`.
   - Two years of history are needed for seasonal baselines; Farrington uses up to three.
4. **Live feeds.**
   - FHIR servers post Subscription notifications or bundles to `/api/ingest/fhir`.
   - HL7 v2 interfaces send ADT A04/A01/A03 to `/api/ingest/hl7v2`. Put an interface engine or MLLP listener in front if the hospital cannot do HTTPS.
   - Insurers drop daily CSV files that a scheduled job posts to `/api/ingest/claims` and `/api/ingest/deaths`.
   - The education registry posts enrolment to `/api/ingest/registry` each term.
5. **Schedule the agent** daily, for example cron or a systemd timer running `python -m shield.cli run-agent`. Run it more often during an emergency.
6. **Silent-running period.** Run 3–6 months alongside existing surveillance, give feedback on every alert, then switch on notifications.

## Sizing

The MVP comfortably handles a city of about 200,000, which is about 2 million records over three years. A daily agent run takes about 7 seconds on one CPU core and a full replay day about 10 seconds.

For a country of 10–50 million:
- Use PostgreSQL with time partitioning, or a columnar store for the analytical view.
- Run the agent per region in parallel.
- Use queue-based ingestion with many workers.

The methods themselves scale linearly with the number of series.
