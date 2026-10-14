# Hosted pages (DRAFTS)

These files are DRAFTS of the pages the App Store listing needs (App Review guideline 5.1.1(i): a privacy policy link in App Store Connect and inside the app).

- `privacy-policy.md`: privacy policy draft.
- `support.md`: support page draft (setup guide, FAQ, contact, redaction warning, security reports route).

## Status

- Not published. Nothing here is live, and no DNS or hosting has been changed.
- Pending Michael's decisions: the stable domain (`{{SITE_URL}}`) and the public support channel under D4 (`{{SUPPORT_EMAIL}}`). Until then, every contact detail and the domain are placeholders. Do not replace them with guessed values.
- The effective date (`{{EFFECTIVE_DATE}}`) and the iCloud Mailbox note (`{{MAILBOX_OPTION_NOTE}}`) are also placeholders.
- The app's Settings links still point at the GitHub pages. Switching them to the hosted URLs is a separate PR after the domain is chosen.

## Publishing

These pages must be published before App Store submission, at stable HTTPS addresses and linked from App Store Connect (Privacy Policy URL, Support URL) and from the app's Settings screen. Remove the DRAFT banners and fill every placeholder when publishing.

## Guardrails

The drafts must not contain the medical-claim words listed in `docs/guardrails/privacy-and-no-medical-claims.md` or any real contact details. `tests/guardrails/test_hosted_pages_drafts.py` checks these rules.
