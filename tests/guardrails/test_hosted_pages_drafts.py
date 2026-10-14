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


def test_drafts_use_placeholders_not_contact_addresses() -> None:
    for path in DRAFTS:
        text = path.read_text(encoding="utf-8")
        assert EMAIL_ADDRESS.search(text) is None, path
        assert "{{SUPPORT_EMAIL}}" in text, path
    assert "{{SITE_URL}}" in PRIVACY.read_text(encoding="utf-8")
    assert "{{SITE_URL}}" in SUPPORT.read_text(encoding="utf-8")


def test_readme_marks_drafts_as_unpublished() -> None:
    text = README.read_text(encoding="utf-8")
    assert "DRAFT" in text
    assert "D4" in text
    assert "before App Store submission" in text
