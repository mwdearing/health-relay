# HealthRelay Support

## Setup guide

The step-by-step setup guide for the receiver and the app is at https://github.com/mwdearing/health-relay/blob/main/docs/setup.md. The pairing notes are at https://github.com/mwdearing/health-relay/blob/main/docs/pairing.md.

## Frequently asked questions

**What does HealthRelay do?**
It reads the Apple Health data types you allow and delivers them to a receiver that you run. It does not write to Health and does not interpret your data.

**Does the developer see my health data?**
No. There is no developer server that receives your records. Records go from your iPhone to your own receiver.

**Why does the app need a receiver?**
The receiver is where your records are stored. You set it up and you own it. Without a receiver, there is nowhere for the records to go.

**Does the camera store photos?**
No. The camera scans the setup QR code only. Camera images are not stored or sent.

**How do I stop sharing?**
Revoke access in the Health app (profile picture, Privacy, Apps, HealthRelay), and disconnect the receiver inside HealthRelay. See the privacy policy at https://github.com/mwdearing/health-relay/blob/main/docs/hosted/privacy-policy.md.

**Does HealthRelay give medical advice?**
No. HealthRelay relays records. Consult a qualified clinician about your health.

**My records are missing or late. What do I check?**
Check that the data type is allowed in the Health app, and that the receiver is reachable from the iPhone. Missing records can have several causes, such as a permission limit, a source gap or a sync gap. HealthRelay cannot tell which one applies from the receiver side alone.

## How to contact us

- Setup and product questions: health-relay-support@technology.mozmail.com
- Bug reports and feature requests: use the forms at https://github.com/mwdearing/health-relay/issues/new/choose with synthetic data only.
- Beta access: open a Beta access request at https://github.com/mwdearing/health-relay/issues/new?template=beta_access.yml. Do not put your email address or any health data in the issue.

## Redact before you share anything

Health data is private. Before you send a log, screenshot, export or database excerpt to anyone, including us:

- remove names, dates of birth, addresses and any identifiers;
- remove values, units and timestamps from real Health records;
- remove receiver URLs, tokens, setup and pairing links, and any cursor values;
- remove medication names and doses if they appear.

Use synthetic data to reproduce a problem wherever you can.

## Security reports

Report security vulnerabilities privately with GitHub private vulnerability reporting: https://github.com/mwdearing/health-relay/security/advisories/new

Do not open a public issue for a vulnerability, and do not include real health values, tokens, pairing material or receiver databases. The full policy is in SECURITY.md at https://github.com/mwdearing/health-relay/blob/main/SECURITY.md.

## Response times

HealthRelay is a small, best-effort project. Security reports are acknowledged within 7 days.
