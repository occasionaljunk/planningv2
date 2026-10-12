# From MVP to national platform

## What the MVP proves

- **The full loop works.** Data flows from institutions in their native formats into a minimised, pseudonymised store, then to an agent that detects, characterises, explains and notifies, then to government feedback, and the system measurably adapts.
- **The methods are the right ones.** Public-health-grade algorithms, with evidence, uncertainty and biases shown, not hidden.
- **Sovereignty can be demonstrated, not just claimed.**

## What the MVP is not yet

- **Not validated on real data.** Every parameter was tuned on a simulation.
- **Not certified or penetration-tested.**
- **Not legally authorised.** Real deployments need a statutory basis and data-sharing agreements.
- **Not built for national scale.** It uses SQLite, a single process, and password login.

## Phase 1: pilot city (3-6 months)

1. **Legal.**
   - Legal basis confirmed.
   - Data-protection impact assessment completed.
   - Data-sharing agreements with 3-5 institutions: at least one hospital, the public health lab and one insurer.
2. **Infrastructure.**
   - In-country hosting with PostgreSQL, HSM/KMS, the government IdP with MFA, TLS/mTLS and private links.
3. **Connectors.** Real FHIR/HL7 interfaces, built and tested with each institution's IT team.
4. **Local configuration.**
   - Reference data: census, boundaries, postcode lookup, schools.
   - National holidays.
   - Local language and coding variants in the syndrome classifier.
5. **Silent running.**
   - The agent runs daily without notifications.
   - Epidemiologists review every alert and give feedback.
   - Measure sensitivity, timeliness and false-alarm rate against known events.
6. **Independent assurance.** Security review and penetration test.

## Phase 2: operational (6-12 months)

- **Notifications on**, with an agreed escalation protocol: who is told what, at which confidence and priority.
- **More sources:** all hospitals, primary-care networks, pharmacy sales (an early signal), school absenteeism, ambulance dispatch, and wastewater surveillance where it exists.
- **Forecasting:** short-term projections of cases and hospital-bed demand under scenarios.
- **Local language model** in-country, for plain-language briefings and question answering over findings, never over raw records.
- **Interoperability:** IHR reporting to the national focal point, export to the WHO/regional formats the ministry uses.
- **Certification:** ISO 27001/27701 and the national health-data frameworks.

## Phase 3: national

- **Regional instances** with a federated national view: aggregates move between regions, records do not.
- **Cross-border signal sharing**, aggregate and consent-based, with neighbouring countries.
- **Genomic surveillance integration:** sequencing results linked to clusters.

## Commercial and operating model

- **Licence and support** per jurisdiction, deployed on the government's own infrastructure or a sovereign cloud region in the country.
- **Training** for epidemiologists and decision-makers; a 24/7 on-call model for emergencies.
- **Source-code escrow and right to audit.** These are often required by governments and make the "trust" story concrete.

## Risks to manage

| Risk | Mitigation |
|---|---|
| Public trust and perception of mass surveillance | Minimisation, pseudonymisation, purpose limitation in law, independent oversight, transparency reports |
| Institutions unwilling or unable to connect | Start with the public lab and public hospitals; offer an interface engine; show the value back to institutions |
| Alert fatigue | Watch list below the notification threshold; feedback-driven thresholds; silent-running calibration |
| Over-reliance on the system | The interface always shows uncertainty, biases and alternatives; decisions stay with officials |
| Data quality (coding changes, outages) | Artefact detection, source-status monitoring, cross-source corroboration |
