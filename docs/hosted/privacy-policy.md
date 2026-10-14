# HealthRelay Privacy Policy

**Effective date:** 14 October 2026
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
- The App Store version has no iCloud option. Beta builds can also offer an encrypted iCloud Mailbox for a receiver that runs on your Mac: in those builds, encrypted deliveries wait in your own iCloud Drive until your Mac collects them. The developer cannot read them.

## Retention and deletion

- **On the iPhone:** records leave the local outbox once the receiver confirms delivery. Deleting the app removes the outbox from the device.
- **On your receiver:** delivered records are stored in a database that you own. You control how long they are kept and how they are deleted. The receiver's data is not held by the developer.
- **Backups:** if you back up your receiver or its database, those copies are also yours to manage and to delete.

## How to stop sharing

- **Revoke Health access:** open the Health app, tap your profile picture, choose Privacy, then Apps, then HealthRelay. Turn off the data types you no longer want to share, or turn off all of them. This stops new reads from Apple Health.
- **Withdraw consent:** open Settings > Data Sharing in HealthRelay and withdraw. HealthRelay then stops sending and cancels uploads that are still queued.
- **Disconnect the receiver:** use the disconnect option inside HealthRelay to unpair the app from your receiver. This stops delivery to that receiver.
- **Delete the data:** delete the records from your receiver using the tools you run. Deleting HealthRelay from the iPhone removes the local outbox.

## Third parties, analytics and advertising

- HealthRelay contains no analytics, no advertising, no crash-reporting upload and no third-party AI service.
- HealthRelay does not share data with third parties.
- The app is open source under the Apache-2.0 license. Source code is available at https://github.com/mwdearing/health-relay.

## Security

Securing your receiver, its network path and its backups is your responsibility. Report security problems privately as described in the support page at https://github.com/mwdearing/health-relay/blob/main/docs/hosted/support.md.

## Medical information

HealthRelay is a data relay. It is not a medical device and does not provide medical advice. Talk to a qualified clinician before making any decision about your health based on your records.

## Contact

health-relay-support@technology.mozmail.com. Please do not include health data in your message.

## Changes to this policy

When this policy changes, the new version is published at https://github.com/mwdearing/health-relay/blob/main/docs/hosted/privacy-policy.md with a new effective date. The version of the policy in effect when you installed a build is the one that applies to that build. Material changes are also described in the release notes.
