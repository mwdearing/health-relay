"""Setup page refresh: iOS-style tokens, expiry countdown, accessibility, no network."""

import re
from pathlib import Path

import pytest

from health_bridge.receiver.pairing import (
    ReceiverPairingBundle,
    ReceiverPairingInvitationBundle,
    create_receiver_pairing_bundle,
    create_receiver_pairing_invitation_bundle,
    pairing_deep_link,
)
from health_bridge.receiver.pairing_setup_page import render_pairing_setup_page

LIGHT_TOKENS = {
    "--bg": "#F2F2F7",
    "--card": "#FFFFFF",
    "--inset": "#F2F2F7",
    "--ink": "#000000",
    "--muted": "#6C6C70",
    "--primary": "#5EEAD4",
    "--on-primary": "#07222E",
    "--accent": "#0F6B78",
    "--warn-bg": "#FFE6CC",
    "--warn-ink": "#7A3E00",
    "--fail-bg": "#FFDCD8",
    "--fail-ink": "#8A1C14",
}
DARK_TOKENS = {
    "--bg": "#000000",
    "--card": "#1C1C1E",
    "--inset": "#2C2C2E",
    "--ink": "#FFFFFF",
    "--muted": "#AEAEB2",
    "--primary": "#5EEAD4",
    "--on-primary": "#07222E",
    "--accent": "#5EEAD4",
    "--warn-bg": "#3A2610",
    "--warn-ink": "#FFCB94",
    "--fail-bg": "#3D1512",
    "--fail-ink": "#FFB3AB",
}
# (foreground token, background token) pairs that carry text.
TEXT_PAIRS = [
    ("--ink", "--bg"),
    ("--ink", "--card"),
    ("--ink", "--inset"),
    ("--muted", "--bg"),
    ("--muted", "--card"),
    ("--muted", "--inset"),
    ("--on-primary", "--primary"),
    ("--accent", "--bg"),
    ("--accent", "--card"),
    ("--accent", "--inset"),
    ("--warn-ink", "--warn-bg"),
    ("--fail-ink", "--fail-bg"),
]
EXPIRES_AT = "2026-06-10T10:20:00Z"


def _v2(tmp_path: Path) -> tuple[ReceiverPairingInvitationBundle, str]:
    bundle = create_receiver_pairing_invitation_bundle(
        tmp_path / "receiver.sqlite",
        label="ios-companion",
        receiver_url="https://health.example.test/v1/batches",
        invitation_secret="hbi_synthetic_secret",
        invitation_code="ABCDE-FGHJK-MNPQR",
    )
    return bundle, render_pairing_setup_page(bundle, pairing_deep_link(bundle))


def _v1(tmp_path: Path) -> tuple[ReceiverPairingBundle, str]:
    bundle = create_receiver_pairing_bundle(
        tmp_path / "receiver-v1.sqlite",
        label="maintainer-iphone",
        receiver_url="https://health-bridge.example.test/v1/batches",
        token="hb_setup_refresh_secret",
        created_at="2026-06-10T10:00:00Z",
    )
    return bundle, render_pairing_setup_page(bundle, pairing_deep_link(bundle))


@pytest.fixture(params=["v2", "v1"])
def any_page(request: pytest.FixtureRequest, tmp_path: Path) -> str:
    scheme = str(request.param)  # pyright: ignore[reportAny]
    return _v2(tmp_path)[1] if scheme == "v2" else _v1(tmp_path)[1]


def _hex_to_rgb(value: str) -> tuple[float, float, float]:
    value = value.lstrip("#")
    return (
        int(value[0:2], 16) / 255,
        int(value[2:4], 16) / 255,
        int(value[4:6], 16) / 255,
    )


def _luminance(value: str) -> float:
    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in _hex_to_rgb(value))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(foreground: str, background: str) -> float:
    a, b = _luminance(foreground), _luminance(background)
    high, low = max(a, b), min(a, b)
    return (high + 0.05) / (low + 0.05)


def _token_blocks(page: str) -> tuple[dict[str, str], dict[str, str]]:
    """Return (light, dark) token maps parsed from the stylesheet."""
    style = page.split("<style>", 1)[1].split("</style>", 1)[0]
    dark_start = style.index("prefers-color-scheme: dark")
    light_root = re.search(r":root\s*\{([^}]*)\}", style[:dark_start])
    dark_root = re.search(r":root\s*\{([^}]*)\}", style[dark_start:])
    assert light_root, "light :root token block missing"
    assert dark_root, "dark :root token block missing"

    def parse(block: str) -> dict[str, str]:
        return {
            match.group(1): match.group(2).upper()
            for match in re.finditer(r"(--[a-z-]+)\s*:\s*(#[0-9a-fA-F]{6})", block)
        }

    return parse(light_root.group(1)), parse(dark_root.group(1))


def test_both_pages_use_the_ios_token_palette(any_page: str) -> None:
    light, dark = _token_blocks(any_page)
    for name, value in LIGHT_TOKENS.items():
        assert light.get(name) == value, f"light {name}"
    for name, value in DARK_TOKENS.items():
        assert dark.get(name) == value, f"dark {name}"


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_every_text_pair_meets_wcag_aa(any_page: str, scheme: str) -> None:
    light, dark = _token_blocks(any_page)
    tokens = light if scheme == "light" else dark
    for foreground, background in TEXT_PAIRS:
        ratio = _contrast(tokens[foreground], tokens[background])
        assert ratio >= 4.5, f"{scheme} {foreground} on {background} is {ratio:.2f}:1"


def test_focus_ring_is_the_accent_never_mint(any_page: str) -> None:
    style = any_page.split("<style>", 1)[1].split("</style>", 1)[0]
    outlines = [m.group(0) for m in re.finditer(r"outline\s*:[^;]*;", style)]
    assert outlines, "no focus outline declared"
    assert all("var(--accent)" in outline for outline in outlines)
    assert not any("--primary" in outline for outline in outlines)


def test_shapes_follow_the_spec(any_page: str) -> None:
    assert "border-radius: 26px" in any_page
    assert "border-radius: 12px" in any_page
    assert "border-radius: 999px" in any_page


def test_page_makes_no_network_requests(any_page: str) -> None:
    resource_tags = [
        m.group(0)
        for m in re.finditer(r"<(?:link|script|img|iframe|source)\b[^>]*>", any_page)
    ]
    for tag in resource_tags:
        assert not re.search(
            r"(?:src|href)\s*=\s*[\"']https?://", tag, re.IGNORECASE
        ), tag
    assert "@import" not in any_page
    assert not re.search(r"url\(\s*[\"']?https?://", any_page, re.IGNORECASE)
    assert "fetch(" not in any_page
    assert "XMLHttpRequest" not in any_page


def test_v2_qr_container_is_an_accessible_image(tmp_path: Path) -> None:
    page = _v2(tmp_path)[1]
    assert re.search(r'role="img"[^>]*aria-label="[^"]+"', page) or re.search(
        r'aria-label="[^"]+"[^>]*role="img"', page
    )


def test_page_has_reduced_motion_and_print_rules(any_page: str) -> None:
    assert "prefers-reduced-motion" in any_page
    assert "@media print" in any_page


def test_v2_copy_actions_announce_a_visible_copied_state(tmp_path: Path) -> None:
    page = _v2(tmp_path)[1]
    assert 'aria-live="polite"' in page
    assert "Copied" in page
    assert "navigator.clipboard" in page
    assert "execCommand" in page


def test_v2_expiry_is_a_time_element_with_iso_fallback(tmp_path: Path) -> None:
    bundle, page = _v2(tmp_path)
    expires = bundle.expires_at
    pattern = (
        rf'<time datetime="{re.escape(expires)}"[^>]*>'
        rf"\s*{re.escape(expires)}\s*</time>"
    )
    assert re.search(pattern, page)


def test_v2_has_countdown_and_an_expired_state(tmp_path: Path) -> None:
    _, page = _v2(tmp_path)
    assert "Expires in" in page
    assert re.search(r'id="expired"[^>]*\bhidden\b', page)
    assert "This invitation has expired." in page
    assert "health-bridge receiver create-pairing" in page
    assert "--format setup-page" in page


def test_v2_orders_the_method_by_device(tmp_path: Path) -> None:
    _, page = _v2(tmp_path)
    assert "navigator.userAgent" in page
    assert re.search(r"iPhone\|iPad|iPad\|iPhone", page)
    assert "<details" in page


def test_v2_shows_numbered_steps_and_an_after_pairing_checklist(
    tmp_path: Path,
) -> None:
    _, page = _v2(tmp_path)
    assert "<ol" in page
    assert "Scan with iPhone Camera" in page
    assert "After pairing" in page
    assert "Copy code" in page
    assert page.index("After pairing") < page.index(
        "Delete this setup page after pairing."
    )


def test_v2_still_hides_secrets(tmp_path: Path) -> None:
    bundle, page = _v2(tmp_path)
    assert bundle.invitation_secret not in page
    assert "bearer-token" not in page
    assert "Token prefix" not in page


def test_v1_copy_and_structure_are_unchanged(tmp_path: Path) -> None:
    _, page = _v1(tmp_path)
    for phrase in (
        "Recommended pairing methods",
        "Best default",
        "Already on the iPhone",
        "Paste setup link in HealthRelay",
        "Direct link fallback",
        "Delete this setup page after pairing.",
    ):
        assert phrase in page
    assert "Expires in" not in page
