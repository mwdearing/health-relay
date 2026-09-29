# Privacy

HealthRelay reads Apple Health data on your iPhone and sends it only to a receiver that you run and control.

- **Where data goes.** Health records read from HealthKit are stored on the iPhone in a local outbox and delivered to the receiver you pair with (or to your own iCloud container if you choose the mailbox option). The project operator has no server that receives your data.
- **Read-only.** The app requests read access to the Health data types you allow. It does not write to Apple Health.
- **No telemetry.** There is no analytics, advertising, crash-reporting upload or third-party AI upload in the app or receiver.
- **Your receiver.** Once data reaches your receiver it is stored in a database you own. Securing that host, its network path and its backups is your responsibility.
- **Pairing material.** Setup links and tokens are private until they expire or are redeemed. Never post them publicly.
- **Control.** You can revoke Health access in the Health app (profile picture > Privacy > Apps > HealthRelay) and disconnect the app from the receiver inside the app.

Questions or concerns: open an issue at https://github.com/mwdearing/health-relay/issues (never include real health data or pairing links). Security reports: see [SECURITY.md](SECURITY.md).
