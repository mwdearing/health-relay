import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parents[1] / ".github" / "scripts" / "attribution_check.py"
_SPEC = importlib.util.spec_from_file_location("attribution_check", _SCRIPT)
assert _SPEC is not None
assert _SPEC.loader is not None
attribution_check = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(attribution_check)


@pytest.mark.parametrize(
    "name", ["feature/x", "dependabot/pip/x", "ci/attribution-check"]
)
def test_allowed_branch_names_pass(name: str) -> None:
    assert attribution_check.branch_problem(name) is None


@pytest.mark.parametrize(
    "name", ["claude/x", "mwd/x", "feature/Bad", "feature/", "main"]
)
def test_other_branch_names_fail_with_the_message(name: str) -> None:
    assert attribution_check.branch_problem(name) == (
        f"branch '{name}' must be <type>/<slug> with type feature, bugfix, "
        "hotfix, docs, chore, refactor, ci or test"
    )
