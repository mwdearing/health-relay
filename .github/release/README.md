# Release files (inherited from upstream, not used by HealthRelay)

These files come from the upstream Apple Health AI Bridge project: its receiver release notes, release criteria, App Review notes template and distribution checklist. HealthRelay does not follow this process. The fork ships signed beta builds on request (GitHub Releases carry notes, not IPA files) and installs the receiver from `main`; see [docs/versioning.md](../../docs/versioning.md).

One exception is HealthRelay's own: `notes-healthrelay-receiver-<YYYY.MM.DD>.md` files are the notes for this fork's pinned receiver releases (tag `healthrelay-receiver-<YYYY.MM.DD>`, never marked Latest). The receiver can be installed from `main` or from such a pinned release; see [pinned receiver releases](../../docs/versioning.md#pinned-receiver-releases).

Do not push `receiver-v*` or `ios-v*` tags: they trigger upstream release workflows.
