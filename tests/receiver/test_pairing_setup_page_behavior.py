"""Setup page v2: expiry, copy handlers, device-aware layout, renewal command."""

import re
from pathlib import Path

import pytest

from health_bridge.receiver.pairing import (
    create_receiver_pairing_invitation_bundle,
    pairing_deep_link,
)
from health_bridge.receiver.pairing_setup_page import render_pairing_setup_page


@pytest.fixture
def page(tmp_path: Path) -> str:
    bundle = create_receiver_pairing_invitation_bundle(
        tmp_path / "receiver.sqlite",
        label="ios-companion",
        receiver_url="https://health.example.test/v1/batches",
        invitation_secret="hbi_synthetic_secret",
        invitation_code="ABCDE-FGHJK-MNPQR",
    )
    return render_pairing_setup_page(bundle, pairing_deep_link(bundle))


def _script(page: str) -> str:
    return page.split("<script>", 1)[1].split("</script>", 1)[0]


def test_copy_code_is_a_real_button_with_its_own_handler(page: str) -> None:
    assert re.search(r'<button[^>]*id="copy-code"[^>]*>\s*Copy code\s*</button>', page)
    assert 'id="pairing-code"' in page
    assert "copy-code" in _script(page)
    assert "pairing-code" in _script(page)


def test_after_pairing_checklist_only_holds_real_follow_ups(page: str) -> None:
    checklist = page.split("After pairing", 1)[1]
    items = [
        str(m.group(1)) for m in re.finditer(r"<li>(.*?)</li>", checklist, re.DOTALL)
    ]
    assert items, "the After pairing list is missing"
    assert not any("Copy code" in item for item in items)
    assert "Delete this setup page after pairing." in items[-1]


def test_steps_match_the_pairing_flow(page: str) -> None:
    steps = page.split("Scan with iPhone Camera", 1)[1].split("</ol>", 1)[0]
    assert "banner" in steps.lower()
    assert "Settings" not in steps


def test_qr_lives_in_a_collapsible_details_block(page: str) -> None:
    assert re.search(r'<details[^>]*id="qr-details"[^>]*\bopen\b', page)
    assert 'id="open-link"' in page
    script = _script(page)
    assert "qr-details" in script
    assert "open-link" in script
    assert re.search(r"iPhone\|iPad", script)


def test_expiry_hides_the_qr_and_shows_the_local_time(page: str) -> None:
    script = _script(page)
    assert "toLocaleString" in script
    assert "qr-details" in script
    assert re.search(r"\.hidden\s*=\s*true", script)
    assert "Expires in ..." not in page
    assert 'id="countdown"' in page


def test_expired_state_shows_the_complete_command(page: str) -> None:
    expired = page.split('id="expired"', 1)[1].split("</div>", 1)[0]
    for part in (
        "--db",
        "--label",
        "--receiver-url",
        "--format setup-page",
        "--setup-page",
    ):
        assert part in expired


def test_clipboard_failure_falls_back_to_a_selection_copy(page: str) -> None:
    script = _script(page)
    assert "navigator.clipboard.writeText(" in script
    assert ".catch(" in script or ".then(" in script
    assert "execCommand('copy')" in script


def test_primary_actions_use_the_mint_button_tokens(page: str) -> None:
    style = page.split("<style>", 1)[1].split("</style>", 1)[0]
    match = re.search(r"a\.button\s*\{([^}]*)\}", style)
    assert match
    assert "var(--primary)" in match.group(1)
    assert "var(--on-primary)" in match.group(1)


def test_expiry_hides_every_pairing_action(page: str) -> None:
    script = _script(page)
    assert 'id="fallback"' in page
    assert 'id="link-details"' in page
    assert "fallback" in script
    assert "link-details" in script


def test_expired_state_is_announced_and_failures_are_visible(page: str) -> None:
    assert re.search(r'id="expired"[^>]*role="alert"', page)
    assert re.search(r'id="copy-status"[^>]*aria-live="polite"', page)
    assert "sr-only" not in page.split('id="copy-status"', 1)[0].rsplit("<", 1)[1]


def test_iphone_path_leads_with_the_open_button(page: str) -> None:
    assert 'id="scan-heading"' in page
    assert "scan-heading" in _script(page)


def test_renewal_command_keeps_the_original_label_and_url(page: str) -> None:
    expired = page.split('id="expired"', 1)[1].split("</div>", 1)[0]
    assert "--label ios-companion" in expired
    assert "https://health.example.test/v1/batches" in expired
    assert "HEALTH_BRIDGE_RECEIVER_URL" not in expired


def test_dark_form_border_and_print_reset(page: str) -> None:
    style = page.split("<style>", 1)[1].split("</style>", 1)[0]
    dark = style.split("prefers-color-scheme: dark", 1)[1].split("}", 1)[0]
    match = re.search(r"--field:\s*(#[0-9A-Fa-f]{6})", dark)
    assert match
    assert match.group(1).upper() == "#8E8E93"
    print_block = style.split("@media print", 1)[1]
    assert "--card: #FFFFFF" in print_block
    assert "--ink: #000000" in print_block
