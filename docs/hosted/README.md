# Hosted pages

These are the privacy policy and support page that the App Store listing links to (App Review guideline 5.1.1(i): a privacy policy link in App Store Connect and inside the app, and a support URL with real contact information).

- Privacy policy: https://github.com/mwdearing/health-relay/blob/main/docs/hosted/privacy-policy.md
- Support: https://github.com/mwdearing/health-relay/blob/main/docs/hosted/support.md

The app's Settings screen links to the same two addresses. The support mailbox is health-relay-support@technology.mozmail.com.

## Changing these pages

Update the effective date in the privacy policy whenever its meaning changes, and describe material changes in the release notes. The pages must not contain the medical-claim words listed in `docs/guardrails/privacy-and-no-medical-claims.md`, any health data, or any contact address other than the support mailbox. `tests/guardrails/test_hosted_pages_drafts.py` checks these rules.
