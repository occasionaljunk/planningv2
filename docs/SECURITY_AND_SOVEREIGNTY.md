# Security, privacy and data sovereignty

Governments will only connect citizens' health records to a platform they can verify. This document says what the MVP already enforces, how to demonstrate it, and what a production deployment adds. Every "enforced" item below has an automated test in `tests/`.

## Sovereignty: data never leaves the country

| Control | MVP status | How to show it |
|---|---|---|
| Everything runs in-country (application, database, keys, AI) | Enforced by design. No external services, APIs, CDNs, fonts, analytics or AI providers. | Security page: residency checks. `python -m shield.cli check` |
| Approved hosting region | Start-up check. In `production` mode a failed critical check stops the platform. | Edit `hosting_region` in the config and restart in production mode: it refuses to start. |
| Process-level egress guard | Enforced. Wraps socket connect and DNS resolution. Only allow-listed in-country hosts and networks are reachable. Blocked attempts are recorded. | Security page, "Try to send data abroad". |
| Webhooks and the optional language model must be in-country | Checked against the allow-list before any delivery. | Residency checks. |
| Front-end loads nothing from third parties | Checked at runtime and enforced by Content-Security-Policy `default-src 'self'`. | Residency checks; browser developer tools. |
| AI runs locally | All detection and reasoning is in-process statistics. The optional LLM must be an in-country endpoint. | Code: `shield/agent/`; config `[narrative]`. |

The egress guard is defence in depth. In production, network firewalls, private subnets without internet routes, and in-country DNS remain the primary controls.

## Privacy by design

**Pseudonymisation at the door.**
- National IDs are replaced by HMAC-SHA256 keyed pseudonyms. The secret is held in the key-management service, so the same person links across institutions without the platform knowing who they are.
- Dates of birth become age and age band. Postcodes become neighbourhoods. Names, street addresses, phone numbers and medical-record numbers are discarded.

**Encrypted originals.**
- The original message is kept only for audit and clinical follow-up. It is encrypted with AES-256-GCM under a fresh per-message key, wrapped by a key-encryption key, and bound to its source institution.
- Only a data steward can decrypt one, after writing a justification. Every access is logged ("break-glass").

**Data minimisation.**
- Only the fields needed for surveillance are stored. The Security page lists exactly what is and is not stored.

**Disclosure control.**
- Decision-maker roles see aggregates with counts of 1–4 shown as "<5". Record-level views are limited to epidemiologists and data stewards, and every view is logged.
- Exports carry a classification header, the user and time, and are hashed into the audit trail.

**Role-based access.**

| Role | Can see |
|---|---|
| Decision-maker | Findings and aggregate data (small counts suppressed). Can record decisions and feedback. |
| Epidemiologist | Full findings and pseudonymised records. Runs the agent, exports, gives feedback. |
| Data steward | Manages institution connections; break-glass decryption. |
| Auditor | Audit trail and security posture only; no health data. |

## Integrity and accountability

**Tamper-evident audit trail.**
- Every login, view, export, break-glass access, ingestion, agent run, notification and piece of feedback is logged.
- Each entry is hash-chained to the previous one, so altering or deleting any entry is detected by `verify_chain`.
- In production, anchor the chain head daily to an external in-country write-once store, such as the national archive or a WORM bucket.

**Explainability.**
- Every alert shows the algorithms used, every detector result, the evidence ledger behind each hypothesis, each feature's contribution to confidence, quantified biases, and a step-by-step agent trace.

**Model change control.**
- Every model update caused by feedback is versioned with its trigger and visible on the Learning page.

## Application security in the MVP

- Passwords hashed with scrypt; HMAC-signed, expiring bearer tokens.
- Strict Content-Security-Policy (no inline scripts), `X-Frame-Options: DENY`, `nosniff`, `no-referrer`, and `no-store` on API responses.
- Institution endpoints require a per-source secret, stored only as a SHA-256 hash. Messages from a suspended source are rejected. Malformed messages are rejected and logged.
- Ingestion is idempotent: re-sent messages do not double count.
- All interface rendering escapes untrusted text from institutions.
- The container runs as a non-root user with a read-only filesystem and all Linux capabilities dropped (`docker-compose.yml`).

## Before production: required work

1. **Identity.** Replace password login with the government IdP (OIDC/SAML) and hardware-backed MFA. Add session revocation and least-privilege role assignment by an access committee.
2. **Keys.**
   - Move keys to an in-country HSM or sovereign-cloud KMS, with dual control and documented key ceremonies.
   - Rotate the key-encryption key and token key on a schedule.
   - Plan rotation of the pseudonym key carefully: it changes all pseudonyms, so re-key with a mapping job.
3. **Transport.** TLS 1.3 on every hop; mTLS client certificates per institution; private connectivity (VPN or dedicated links) to hospitals.
4. **Database.** PostgreSQL with encrypted volumes, row-level security, encrypted in-country backups, and tested restores.
5. **Hardening and assurance.**
   - Independent penetration test and code review.
   - Dependency pinning with a software bill of materials; signed container images; reproducible builds.
   - Vulnerability management and a security operations hand-over.
6. **Certification.** Typically ISO/IEC 27001 and 27701, plus national health-data security frameworks. Confirm which apply with the government's information-security authority.
7. **Re-identification governance.** Write down who may re-identify a patient (for example, clinical follow-up of a case through the treating institution) and how. The platform should never re-identify directly; it asks the source institution, which holds the identity.
8. **Data retention.** Set retention periods per table, such as encrypted raw messages for N months, with automated deletion and logged evidence.

## Legal basis

Real-time access to the health records of every citizen is the most sensitive part of this proposal. It must rest on explicit legal authority, not consent or contracts alone. Typical elements:

- **Statutory authority.** A public-health or communicable-disease law, or regulation, that authorises surveillance access, names the purposes, and limits secondary use. Examples include GDPR Article 9(2)(i) with national implementing law in the EU, or equivalent provisions elsewhere.
- **Data protection impact assessment** and consultation with the data-protection authority before go-live.
- **Data-sharing agreements** with each institution, covering scope, fields, security, breach notification and termination.
- **Proportionality.** The MVP's minimisation and pseudonymisation are designed to support the argument that only what is necessary is processed.
- **Independent oversight.** An auditor role, published transparency reports, and an ethics or oversight board.
- **Purpose limitation.** Written commitments, ideally in law, that the data cannot be used for law enforcement, immigration, insurance underwriting or any purpose beyond public health.

Engage the government's legal counsel and the data-protection authority early. This determines timelines more than the technology does.
