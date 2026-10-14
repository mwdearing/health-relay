import re
from pathlib import Path
from typing import cast

import yaml

ROOT = Path(__file__).parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "app-store-screenshots.yml"
SCENES = ("overview", "sync", "ecg", "labs", "consent")
PINNED_ACTION = re.compile(r"^[^@\s]+@(?P<sha>[0-9a-f]{40})(?:\s+#.*)?$")


def _load() -> dict[str, object]:
    return cast(
        "dict[str, object]", yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    )


def _steps(data: dict[str, object]) -> list[dict[str, object]]:
    jobs = cast("dict[str, dict[str, object]]", data["jobs"])
    steps: list[dict[str, object]] = []
    for job in jobs.values():
        steps.extend(cast("list[dict[str, object]]", job["steps"]))
    return steps


def test_workflow_file_exists() -> None:
    assert WORKFLOW.is_file(), WORKFLOW


def test_workflow_is_manual_dispatch_only() -> None:
    # PyYAML (YAML 1.1) reads the bare key "on" as boolean True.
    raw = cast(
        "dict[object, object]", yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    )
    triggers = cast("dict[object, object]", raw[True] if True in raw else raw["on"])
    assert list(triggers) == ["workflow_dispatch"], triggers


def test_workflow_references_no_secrets() -> None:
    assert (
        re.search(r"\$\{\{\s*secrets\.", WORKFLOW.read_text(encoding="utf-8")) is None
    )


def test_workflow_requests_read_only_permissions() -> None:
    data = _load()
    assert data.get("permissions") == {"contents": "read"}


def test_all_actions_are_pinned_to_full_shas() -> None:
    uses = [str(step["uses"]) for step in _steps(_load()) if "uses" in step]
    assert uses
    for reference in uses:
        assert PINNED_ACTION.match(reference), reference


def test_scene_list_matches_spec() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    match = re.search(r"for scene in ([a-z ]+); do", text)
    assert match is not None
    assert tuple(match.group(1).split()) == SCENES
    assert "-HealthRelaySampleScene" in text
    assert "printf '%02d'" in text
    assert "screenshot" in text
    assert "screenshots/" in text


def test_simulator_build_is_unsigned_debug_iphonesimulator() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "CODE_SIGNING_ALLOWED=NO" in text
    assert "-sdk iphonesimulator" in text
    assert "-configuration Debug" in text
    assert "-target HealthBridgeCompanion" in text


def test_simulator_setup_matches_spec() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "iPhone 16 Pro Max" in text
    assert (
        "xcrun simctl status_bar booted override --time 9:41 --batteryState charged"
        " --batteryLevel 100 --cellularBars 4 --wifiBars 3"
    ) in text
    assert "xcrun simctl ui booted appearance dark" in text


def test_artifact_upload_is_named_and_kept_seven_days() -> None:
    steps = [
        step
        for step in _steps(_load())
        if str(step.get("uses", "")).startswith("actions/upload-artifact@")
    ]
    assert len(steps) == 1
    with_block = cast("dict[str, object]", steps[0]["with"])
    assert with_block["name"] == "app-store-screenshots"
    assert with_block["path"] == "screenshots/"
    assert with_block["retention-days"] == 7


def test_pixel_sizes_are_printed_with_sips() -> None:
    assert "sips -g pixelWidth -g pixelHeight" in WORKFLOW.read_text(encoding="utf-8")
