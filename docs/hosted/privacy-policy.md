# HealthRelay Privacy Policy

> DRAFT. Not published. Pending Michael's decisions on the domain ({{SITE_URL}}) and the support contact (D4). Must be published at a stable HTTPS address before App Store submission.

**Effective date:** {{EFFECTIVE_DATE}}
**Applies to:** the HealthRelay iOS app and the HealthRelay receiver and command-line tools in this project.

## Summary

HealthRelay reads Apple Health data that you allow on your iPhone and sends it only to a receiver that you run and control. HealthRelay does not send your health data to its developer or to any third party.

## What HealthRelay reads

- HealthRelay requests read access only to the Apple Health data types that you choose to allow in the Health permission sheet.
- You can allow or deny each type, and you can change these choices at any time in the Health app.
- HealthRelay does not write to Apple Health.
- HealthRelay does not interpret your data. It relays the records it reads, with their source, time and unit information, so that the receiver you run can store them.
- Camera: the camera is used only to scan the setup QR code shown by your receiver. Camera images are not stored or sent.

## Where your data goes

- Records read from Apple Health are stored temporarily on your iPhone in a local outbox.
- The outbox delivers records only to the receiver you pair with during setup.
- HealthRelay has no server that receives your health data. The developer cannot see your records.
- {{MAILBOX_OPTION_NOTE}} (Placeholder: describe the iCloud Mailbox option here only if Michael decides to keep it in the App Store version. Until then, delete this line.)

## Retention and deletion

- **On the iPhone:** records leave the local outbox once the receiver confirms delivery. Deleting the app removes the outbox from the device.
- **On your receiver:** delivered records are stored in a database that you own. You control how long they are kept and how they are deleted. The receiver's data is not held by the developer.
- **Backups:** if you back up your receiver or its database, those copies are also yours to manage and to delete.

## How to stop sharing

- **Revoke Health access:** open the Health app, tap your profile picture, choose Privacy, then Apps, then HealthRelay. Turn off the data types you no longer want to share, or turn off all of them. This stops new reads from Apple Health.
- **Disconnect the receiver:** use the disconnect option inside HealthRelay to unpair the app from your receiver. This stops delivery to that receiver.
- **Delete the data:** delete the records from your receiver using the tools you run. Deleting HealthRelay from the iPhone removes the local outbox.

## Third parties, analytics and advertising

- HealthRelay contains no analytics, no advertising, no crash-reporting upload and no third-party AI service.
- HealthRelay does not share data with third parties.
- The app is open source under the Apache-2.0 license. Source code is available at https://github.com/mwdearing/health-relay.

## Security

Securing your receiver, its network path and its backups is your responsibility. Report security problems privately as described in the support page at {{SITE_URL}}/support.

## Medical information

HealthRelay is a data relay. It is not a medical device and does not provide medical advice. Talk to a qualified clinician before making any decision about your health based on your records.

## Contact

{{SUPPORT_EMAIL}} (Placeholder: the support mailbox is Michael's decision under D4. Do not publish a personal address.)

## Changes to this policy

When this policy changes, the new version is published at {{SITE_URL}}/privacy with a new effective date. The version of the policy in effect when you installed a build is the one that applies to that build. Material changes are also described in the release notes.
