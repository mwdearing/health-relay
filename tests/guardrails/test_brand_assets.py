"""HealthRelay brand guardrails (replaces the upstream Health Bridge asset checks)."""

import json
import struct
from pathlib import Path
from typing import cast

BRAND = Path("assets/brand")
APPICONSET = Path(
    "ios/HealthBridgeCompanion/App/Assets.xcassets/AppIcon.appiconset",
)
XCODE_PROJECT = Path(
    "ios/HealthBridgeCompanion/HealthBridgeCompanion.xcodeproj/project.pbxproj",
)

CANONICAL_BRAND_PNGS = {
    BRAND / "healthrelay-lockup.png": (720, 200),
    BRAND / "healthrelay-mark-1024.png": (1024, 1024),
    BRAND / "healthrelay-mark-512.png": (512, 512),
    BRAND / "healthrelay-mark-180.png": (180, 180),
    BRAND / "healthrelay-mark-48.png": (48, 48),
    BRAND / "healthrelay-mark-32.png": (32, 32),
    BRAND / "healthrelay-mark-16.png": (16, 16),
}

UPSTREAM_BRAND_PREFIXES = ("health-bridge-", "favicon", "apple-touch-icon")


def _png_header(path: Path) -> tuple[int, int, int, int]:
    data = path.read_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = cast("tuple[int, int]", struct.unpack(">II", data[16:24]))
    return width, height, data[24], data[25]


def test_canonical_brand_pngs_are_present_and_expected_size() -> None:
    for path, expected_size in CANONICAL_BRAND_PNGS.items():
        width, height, bit_depth, _color_type = _png_header(path)
        assert (width, height) == expected_size, path
        assert bit_depth == 8


def test_upstream_brand_assets_are_not_shipped() -> None:
    for path in BRAND.iterdir():
        assert not path.name.startswith(UPSTREAM_BRAND_PREFIXES), path
    assert not Path("tools/generate_brand_assets.py").exists()


def test_ios_app_icon_catalog_is_complete_opaque_and_bundled() -> None:
    contents = cast(
        "dict[str, object]",
        json.loads((APPICONSET / "Contents.json").read_text(encoding="utf-8")),
    )
    images = cast("list[dict[str, str]]", contents["images"])
    assert len(images) == 18
    for image in images:
        filename = image["filename"]
        point_size = float(image["size"].split("x", 1)[0])
        scale = int(image["scale"].rstrip("x"))
        expected_pixels = round(point_size * scale)
        width, height, bit_depth, color_type = _png_header(APPICONSET / filename)
        assert (width, height) == (expected_pixels, expected_pixels), filename
        assert bit_depth == 8
        assert color_type == 2  # opaque RGB: iOS applies its own corner mask
    project = XCODE_PROJECT.read_text(encoding="utf-8")
    assert "Assets.xcassets in Resources" in project
    assert "ASSETCATALOG_COMPILER_APPICON_NAME = AppIcon;" in project


def test_generator_is_pillow_only_and_documented() -> None:
    generator = Path("tools/generate_healthrelay_icon.py").read_text(encoding="utf-8")
    assert "playwright" not in generator.lower()
    assert "from PIL import" in generator
    assert "generate_healthrelay_icon.py" in Path("docs/brand.md").read_text(encoding="utf-8")
    assert "generate_healthrelay_icon.py" in (BRAND / "README.md").read_text(encoding="utf-8")


def test_readme_uses_healthrelay_lockup_and_brand_guide() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    assert '<img src="assets/brand/healthrelay-lockup.png"' in readme
    assert "health-bridge-lockup" not in readme
    assert "docs/brand.md" in readme


def test_public_release_audit_only_allows_healthrelay_visual_binaries() -> None:
    audit = Path("scripts/public-release-audit.py").read_text(encoding="utf-8")
    for path in CANONICAL_BRAND_PNGS:
        assert f'Path("{path.as_posix()}")' in audit
    assert "health-bridge-" not in audit
    assert "ios/HealthBridgeCompanion/App/Assets.xcassets/AppIcon.appiconset/" in audit
