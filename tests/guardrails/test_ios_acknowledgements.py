"""The iOS companion ships the NOTICE attribution in an Acknowledgements screen."""

import re
import textwrap
from pathlib import Path

CONTENT_VIEW = Path("ios/HealthBridgeCompanion/App/ContentView.swift")
NOTICE = Path("NOTICE")
NOTICE_BLOCK = re.compile(
    r'static let noticeText = """\n(?P<body>.*?)\n\s*"""',
    re.DOTALL,
)
SETTINGS_LINK = re.compile(
    r"NavigationLink\s*\{\s*AcknowledgementsView\(\)\s*\}\s*label:\s*\{\s*Text\(\"Acknowledgements\"\)"
)


def _embedded_notice() -> str:
    match = NOTICE_BLOCK.search(CONTENT_VIEW.read_text(encoding="utf-8"))
    assert match is not None
    return textwrap.dedent(match.group("body"))


def test_embedded_notice_matches_the_notice_file() -> None:
    assert _embedded_notice().strip() == NOTICE.read_text(encoding="utf-8").strip()


def test_embedded_notice_carries_the_upstream_attribution() -> None:
    notice = _embedded_notice()
    assert "fork of Apple Health AI Bridge" in notice
    assert "Apache License" in notice
    assert "\\" not in notice


def test_settings_links_to_the_acknowledgements_screen() -> None:
    source = CONTENT_VIEW.read_text(encoding="utf-8")
    assert "private struct AcknowledgementsView: View" in source
    assert SETTINGS_LINK.search(source)
    assert "https://www.apache.org/licenses/LICENSE-2.0" in source
