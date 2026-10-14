"""Sample-data mode for App Review (R2).

Reviewers reach every screen without a receiver through an in-app sample mode. The
sample path is structural: its views never reference the view model, the HealthKit
readers, the receiver client or any mailbox or network type, and its fixtures are
invented values only.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

IOS = Path("ios/HealthBridgeCompanion")
CONTENT_VIEW = IOS / "App/ContentView.swift"
APP_ENTRY = IOS / "App/HealthBridgeCompanionApp.swift"
DELEGATE_FILE = IOS / "App/BackgroundURLSessionOutboxUploader.swift"
COLOR_ASSETS = IOS / "App/Assets.xcassets"
SAMPLE_VIEW = IOS / "App/SampleReviewView.swift"
SAMPLE_DATA = IOS / "Sources/HealthBridgeCompanionCore/SampleReviewData.swift"
XCODE_PROJECT = IOS / "HealthBridgeCompanion.xcodeproj/project.pbxproj"

ENTRY_LABEL = "Try with sample data"
BANNER = "Sample data. Nothing is read from Apple Health or sent anywhere."

FORBIDDEN_IN_SAMPLE_PATH = (
    "HealthKit",
    "HKHealthStore",
    "Reader(",
    "ReceiverClient",
    "URLSession",
    "ProductionMailbox",
    "MailboxTransport",
    "BackgroundURLSessionOutboxUploader",
    "FileOutbox",
    "HealthBridgeCompanionViewModel",
    "ReceiverPairing",
    "MailboxKeyStore",
    "Keychain",
    "http://",
    "https://",
)

STRING_LITERAL = re.compile(r'"((?:[^"\\\n]|\\.)*)"')
# Literal text in the data file must be invented: either explicitly "Sample"-prefixed,
# a neutral identifier, a fixed 2026-01 timestamp or a zero-filled UUID.
_ALLOWED_PARTS = (
    r"Sample\b.*",
    r"sample\.invalid",
    r"steps",
    r"sleep",
    r"ecg",
    r"medication",
    r"labs",
    r"overview",
    r"sync",
    r"-HealthRelaySampleScene",
    r"[0-9T:\-. Z]*",
    r"0{8}-0000-0000-0000-[0-9]{12}",
)
ALLOWED_LITERAL = re.compile("^(" + "|".join(_ALLOWED_PARTS) + ")$")
DATE_IN_FIXTURE = re.compile(r"\b(20\d\d)-(\d\d)-(\d\d)\b")


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _project_lines_with(needle: str) -> list[str]:
    return [line for line in _source(XCODE_PROJECT).splitlines() if needle in line]


def test_sample_files_exist() -> None:
    assert SAMPLE_DATA.is_file()
    assert SAMPLE_VIEW.is_file()


def test_entry_point_is_a_single_labelled_link_in_content_view() -> None:
    source = _source(CONTENT_VIEW)
    assert source.count(ENTRY_LABEL) == 1
    link = re.search(
        r'NavigationLink\s*\{\s*SampleReviewView\(\)\s*\}\s*label:\s*\{\s*Text\("'
        + re.escape(ENTRY_LABEL)
        + r'"\)',
        source,
    )
    assert link is not None, (
        "the sample entry must be a NavigationLink to SampleReviewView()"
    )


def test_entry_point_does_not_touch_the_view_model() -> None:
    source = _source(CONTENT_VIEW)
    link = re.search(
        r"NavigationLink\s*\{\s*SampleReviewView\(\)\s*\}\s*label:\s*\{.*?"
        + re.escape(ENTRY_LABEL),
        source,
        re.DOTALL,
    )
    assert link is not None
    assert "viewModel" not in link.group(0)


def test_sample_banner_is_shown_on_the_sample_root() -> None:
    assert BANNER in _source(SAMPLE_VIEW)


def test_sample_view_and_data_never_reach_healthkit_or_the_network() -> None:
    for path in (SAMPLE_VIEW, SAMPLE_DATA):
        source = _source(path)
        for token in FORBIDDEN_IN_SAMPLE_PATH:
            assert token not in source, f"{path.name} must not reference {token!r}"


def test_sample_data_literals_are_invented() -> None:
    source = _source(SAMPLE_DATA)
    literals = [match.group(1) for match in STRING_LITERAL.finditer(source)]
    assert literals, "the sample data file must carry literal fixtures"
    for literal in literals:
        assert ALLOWED_LITERAL.match(literal), (
            f"non-sample literal in fixtures: {literal!r}"
        )
    assert "@" not in source


def test_sample_dates_are_fixed_in_january_2026() -> None:
    for path in (SAMPLE_DATA, SAMPLE_VIEW):
        for match in DATE_IN_FIXTURE.finditer(_source(path)):
            year, month = str(match.group(1)), str(match.group(2))
            assert (year, month) == ("2026", "01"), f"{path.name} has a non-fixed date"


def test_sample_identifiers_are_zero_filled_uuids() -> None:
    uuid = re.compile(
        r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}"
    )
    for match in uuid.finditer(_source(SAMPLE_DATA)):
        value = str(match.group(0))
        assert value.startswith("00000000-0000-0000-0000-"), value


def test_sample_files_are_registered_in_the_xcode_project() -> None:
    expected = {
        SAMPLE_VIEW.name: "A7D8CE70846932FEE851CAD6",  # App group
        SAMPLE_DATA.name: "2291ECD343AC5D5C07D462BA",  # HealthBridgeCompanionCore group
    }
    for filename, group_id in expected.items():
        file_refs = _project_lines_with(f"/* {filename} */ = {{isa = PBXFileReference")
        assert len(file_refs) == 1, f"{filename} needs one PBXFileReference"
        file_ref_id = file_refs[0].strip().split()[0]

        build_refs = _project_lines_with(
            f"/* {filename} in Sources */ = {{isa = PBXBuildFile"
        )
        assert len(build_refs) == 1, f"{filename} needs one PBXBuildFile"
        build_id = build_refs[0].strip().split()[0]

        group_lines = [
            line
            for line in _project_lines_with(f"{group_id} /* ")
            if line.lstrip().startswith(f"{group_id} /* ")
        ]
        assert group_lines, f"group {group_id} missing"
        assert file_ref_id in group_lines[0], f"{filename} is not in its group"

        sources_lines = [
            line
            for line in _source(XCODE_PROJECT).splitlines()
            if "isa = PBXSourcesBuildPhase" in line
        ]
        assert any(build_id in line for line in sources_lines), (
            f"{filename} is not in the Sources phase"
        )


def test_dark_scheme_is_applied_once_at_the_root() -> None:
    # Given: every App source file in the target.
    hits = {
        path.name: _source(path).count(".preferredColorScheme(.dark)")
        for path in sorted((IOS / "App").glob("*.swift"))
    }
    # Then: exactly one application, at the root in the app entry file.
    assert sum(hits.values()) == 1, hits
    assert hits[APP_ENTRY.name] == 1
    # And the teal colour tokens keep their dark variants (asset values unchanged).
    for name in ("RelayMint", "RelayOnMint", "RelayAccentInk"):
        contents = (COLOR_ASSETS / f"{name}.colorset/Contents.json").read_text(
            encoding="utf-8"
        )
        assert '"value" : "dark"' in contents or '"value": "dark"' in contents, name


def test_sample_overview_carries_the_mockup_copy_and_palette() -> None:
    source = _source(SAMPLE_VIEW)
    for text in (
        "HealthRelay",
        "Connected",
        "Manage connection",
        "Your local ledger works on its own.",
        "Apple Health",
        "Supported nutrients",
        "Creatine & other compounds",
        "Optional direct connection",
        "Paired receiver",
        "Saved locally when offline",
        "Source details",
        "Energy",
        "Food & drinks",
        "Water",
        "Drink log",
        "Creatine",
        "Supplement log",
        "One drink. One calorie entry.",
    ):
        assert text in source, text
    # Mockup colours, defined locally in the sample view (asset colours unchanged).
    for hex_value in (
        "03141A",
        "0D2229",
        "192A31",
        "23F6C9",
        "4AF4E8",
        "04141A",
        "032C2E",
        "065B55",
        "6FF8EC",
        "052D2E",
        "0B5C55",
        "74F1E2",
        "FCFCFC",
        "3A4B52",
        "CFF6F8",
    ):
        assert f"0x{hex_value}" in source, hex_value


def test_launch_argument_is_parsed_into_sample_scenes_only() -> None:
    data = _source(SAMPLE_DATA)
    assert '"-HealthRelaySampleScene"' in data
    scene_block = re.search(
        r"enum SampleReviewScene: String[^{]*\{(.*?)\n\}", data, re.DOTALL
    )
    assert scene_block is not None
    cases = re.findall(r"case (\w+)", scene_block.group(1))
    assert cases == ["overview", "sync", "ecg", "labs"]
    assert "requestedScene(arguments:" in data
    # The app entry picks sample or live scenes from that parser.
    # The live root is only used in the else branch.
    entry = _source(APP_ENTRY)
    assert "SampleReviewLaunch.requestedScene(arguments:" in entry
    sample_branch = entry.index("if let sampleScene")
    live_root = entry.index("LiveRootView()", sample_branch)
    assert entry.index("} else {", sample_branch) < live_root
    # The app delegate bootstraps the live runtime only outside sample launches.
    delegate = _source(DELEGATE_FILE)
    launch = delegate[delegate.index("didFinishLaunchingWithOptions") :]
    guard = launch.index("SampleReviewLaunch.requestedScene(arguments:")
    assert guard < launch.index("applicationRuntime.prepareForBackgroundLaunch()")
    assert guard < launch.index("await applicationRuntime.bootstrap()")


@pytest.mark.skipif(shutil.which("swiftc") is None, reason="swiftc not installed")
def test_launch_parser_selects_scenes_when_compiled(tmp_path: Path) -> None:
    main = tmp_path / "main.swift"
    source = (
        "let scene = SampleReviewLaunch.requestedScene(\n"
        "    arguments: Array(CommandLine.arguments.dropFirst()))\n"
        'print(scene?.rawValue ?? "live")\n'
    )
    _ = main.write_text(source, encoding="utf-8")
    binary = tmp_path / "parser"
    _ = subprocess.run(
        ["swiftc", "-o", str(binary), str(SAMPLE_DATA), str(main)],
        check=True,
        capture_output=True,
        timeout=600,
    )
    cases = {
        (): "live",
        ("-HealthRelaySampleScene", "sync"): "sync",
        ("-HealthRelaySampleScene", "labs"): "labs",
        ("-HealthRelaySampleScene", "ecg"): "ecg",
        ("-HealthRelaySampleScene", "overview"): "overview",
        ("-HealthRelaySampleScene", "not-a-scene"): "overview",
        ("-HealthRelaySampleScene",): "overview",
        ("-SomeOtherFlag", "sync"): "live",
    }
    for args, expected in cases.items():
        run = subprocess.run(
            [str(binary), *args], check=True, capture_output=True, text=True, timeout=60
        )
        assert run.stdout.strip() == expected, (args, run.stdout)
