# Demo script for a government audience (25-35 minutes)

**Setup.**
- Run `make demo-live` the day before. It stops the clock at 10 September 2026, before any alert.
- Run `make serve` and open http://127.0.0.1:8000.
- Use one browser window signed in as the Health Minister and one as the Chief Epidemiologist.
- If time is short, use `make demo` instead (clock at 10 October, all alerts already raised) and skip step 3.

## 1. The problem (2 min)

**Open on the Situation overview as the Minister.**
- COVID-19 was recognised weeks after it began spreading.
- The data that could have shown it already existed: emergency visits, clinic notes, lab results, insurance claims and deaths. It sat in separate institutions, and nobody looked at it together, daily, with the right statistics.

## 2. Sovereignty first (4 min)

**Open Security & sovereignty.**
- **Green pill in the header.** Data is resident in this country and this region.
- **Residency checks.** These are live tests the platform runs on itself, not promises.
- **"Try to send data abroad".** Type any foreign address and press the button: the connection is blocked inside the process and logged.
- **Data minimisation.** The platform never stores names, national IDs, addresses or dates of birth. It links people across hospitals with keyed pseudonyms. Originals are encrypted and opened only through audited break-glass access.
- **Audit trail.** Sign in as the auditor and press **Verify chain integrity**. Explain that changing a single historical entry breaks the chain.

## 3. Watch it find outbreaks (8 min)

**Open Data sources.**
- Twelve institutions: hospitals over FHIR, clinics over HL7 v2, university clinics, the public health laboratory, two health insurers, a life insurer and the education registry.
- **Delay column.** Hospitals report within hours; insurers take days. The platform corrects for that.

**Advance the clock.** Use **Next day**, or **Advance 7 days** on the Demo control page. Each simulated day takes about 10-25 seconds and does two things:
- Renders that day's real-format messages from each institution and sends them through the connectors.
- Runs the agent.

**What appears:**
- **15 September: university respiratory cluster.**
  - Only 13 patients have seen a doctor, mostly students at Riverton State University.
  - Their respiratory panels are negative for influenza, RSV and SARS-CoV-2, so the leading explanation is already **novel or emerging respiratory pathogen**.
  - Over the following days the same alert grows, gains a doubling time and Rt, and spreads to Harbour College and Old Harbour.
  - Open **How the assessment evolved**.
- **24 September: school norovirus.** A gastrointestinal cluster at Northgate Primary School the day after the first children presented, with norovirus-positive stool tests.
- **1 October: a hospital rise in gastrointestinal illness.**
  - Within its first days the agent explains it as a **probable data artefact**, keeps it on the watch list and does not notify.
  - Open it: one hospital, a jump in code A09, triage text saying "abd pain", and no other institution seeing it.
  - This is the credibility moment: the system tells you when not to panic.
- **6 October: Legionnaires' disease cluster in Ironworks.** Severe pneumonia in older men, Legionella urinary antigen positives, compact geography. The recommended action is to sample cooling towers.

## 4. Inside one alert (8 min)

**Open the novel-pathogen alert as the Minister.**
- **Briefing.** What could be happening, confidence, key findings, recommended next steps.
- **Evidence.**
  - Epidemic curve against what was expected.
  - Doubling time and reproduction number.
  - The 18–24 age group 2.5 times over-represented.
  - Riverton State University and Harbour College attack rates.
  - 93% of respiratory panels negative.
- **Possible causes.** Each explanation with evidence for and against, in plain language with its weight. Nothing is a black box.
- **Detection & algorithms.**
  - Which of the 13 methods fired.
  - Why the confidence is what it is, feature by feature.
  - The methods are the ones the CDC, the UK Health Security Agency and the European CDC use.
- **Biases & limitations.** What could make this wrong, quantified: coverage gaps, reporting delay, testing bias, small numbers.
- **Historical context.**
  - 2009 H1N1, COVID-19, SARS and MERS: their cases, hospitalisations, deaths and economic impact, with sources.
  - What similar rates would mean for these neighbourhoods and for the whole city.
- **Raw data** (as the epidemiologist).
  - The pseudonymised records behind the alert, exportable with a logged, classified CSV.
  - As the data steward, decrypt one original HL7 or FHIR message with a written justification, then show that the access appears in the audit trail.
- **Agent trace.** Every step the agent took, in order, with timing.

## 5. The government stays in charge (4 min)

**In the artefact alert, open Feedback** and record "False alarm" with the actions "Data issue raised with institution".
- Show **What the platform learned**: the confidence model was re-trained, the reliability of the detectors involved was updated, and the threshold may tighten.

**In the norovirus alert,** record "Confirmed" with the cause, the actions and the outcome.
- Show that the cause's prior rises and the event becomes local history that future alerts cite. The 2024 measles outbreak already appears this way.

**Open Learning & model** and show the weights before and after.

## 6. Close (2 min)

**Open Demo control → Ground truth → Reveal** to show what was simulated. The agent never sees it.

What the pilot looks like:
- One city.
- 3–6 months of silent running alongside existing surveillance.
- The legal basis and data-sharing agreements in parallel.
- Then notifications are switched on.

## Questions to expect

- **"Is this real data?"** No. It is a statistically realistic simulation of a 203,000-person city. It is delivered in the same formats real institutions use, and processed by the same code that would process real data.
- **"Do you send data to an AI company?"** No. The detection and reasoning are statistical methods running on this server. An optional language model, for plain-language summaries only, must also run in-country.
- **"How many false alarms?"** Show the evaluation table on Learning & model.
  - In the demo period, one chance respiratory blip in Ironworks and Southbank crossed the 50% notification threshold for one day (18 September, 51%), then fell back below 1%.
  - Every other chance signal stayed on the watch list.
  - Feedback tightens noisy alert types.
- **"What does it cost to run?"** A city pilot runs on a single server. National scale is regional instances on in-country infrastructure.
