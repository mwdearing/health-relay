import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
HOSTED = ROOT / "docs" / "hosted"
PRIVACY = HOSTED / "privacy-policy.md"
SUPPORT = HOSTED / "support.md"
README = HOSTED / "README.md"
DRAFTS = (PRIVACY, SUPPORT)

# Banned claim words (docs/guardrails/privacy-and-no-medical-claims.md and plan R6).
# Word-boundary patterns so that "secure" does not trip "cure".
BANNED_CLAIM_PATTERNS = (
    re.compile(r"\bdiagnos", re.IGNORECASE),
    re.compile(r"\bdetect", re.IGNORECASE),
    re.compile(r"\btreat", re.IGNORECASE),
    re.compile(r"\bcure\b", re.IGNORECASE),
    re.compile(r"\bmonitor your heart", re.IGNORECASE),
)
EMAIL_ADDRESS = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def test_hosted_draft_files_exist() -> None:
    for path in (*DRAFTS, README):
        assert path.is_file(), path


def test_privacy_draft_covers_revocation_in_health_app() -> None:
    text = PRIVACY.read_text(encoding="utf-8")
    assert "revoke" in text.lower()
    assert "Health app" in text


def test_drafts_contain_no_banned_claim_words() -> None:
    for path in DRAFTS:
        text = path.read_text(encoding="utf-8")
        for pattern in BANNED_CLAIM_PATTERNS:
            assert pattern.search(text) is None, (path, pattern.pattern)


SUPPORT_ADDRESS = "health-relay-support@technology.mozmail.com"
REPO_BLOB = "https://github.com/mwdearing/health-relay/blob/main/docs/hosted"
PRIVACY_URL = f"{REPO_BLOB}/privacy-policy.md"
SUPPORT_URL = f"{REPO_BLOB}/support.md"


def test_pages_are_final_with_no_placeholders_or_draft_banner() -> None:
    for path in (*DRAFTS, README):
        text = path.read_text(encoding="utf-8")
        assert "{{" not in text, path
        assert "DRAFT" not in text, path


def test_the_only_contact_address_is_the_public_support_mailbox() -> None:
    for path in DRAFTS:
        text = path.read_text(encoding="utf-8")
        assert set(EMAIL_ADDRESS.findall(text)) == {SUPPORT_ADDRESS}, path


def test_pages_link_each_other_at_their_published_addresses() -> None:
    assert SUPPORT_URL in PRIVACY.read_text(encoding="utf-8")
    assert PRIVACY_URL in SUPPORT.read_text(encoding="utf-8")


def test_privacy_policy_covers_consent_withdrawal_and_beta_only_icloud() -> None:
    text = PRIVACY.read_text(encoding="utf-8")
    assert "Settings > Data Sharing" in text
    assert "The App Store version has no iCloud option." in text


def test_readme_lists_the_published_addresses() -> None:
    text = README.read_text(encoding="utf-8")
    assert PRIVACY_URL in text
    assert SUPPORT_URL in text


def test_app_settings_link_to_the_published_pages() -> None:
    content_view = (
        ROOT / "ios" / "HealthBridgeCompanion" / "App" / "ContentView.swift"
    ).read_text(encoding="utf-8")
    assert PRIVACY_URL in content_view
    assert SUPPORT_URL in content_view
