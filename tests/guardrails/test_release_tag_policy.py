from pathlib import Path

ROOT = Path(__file__).parents[2]
RELEASE_CRITERIA = ROOT / ".github/release/criteria.md"


def test_receiver_tag_namespace_has_a_separate_protected_creation_path() -> None:
    # Given: the release criteria for future Receiver/CLI artifacts.
    criteria = RELEASE_CRITERIA.read_text(encoding="utf-8")

    # When: governance evaluates the component-scoped tag path.
    receiver_ruleset = "active tag ruleset targeting `refs/tags/receiver-v*`"

    # Then: receiver creation is restricted independently from legacy denial.
    assert receiver_ruleset in criteria
    assert "Restrict creations" in criteria
    assert "release maintainer role needed to create a new signed tag" in criteria


def test_maintainer_tag_instructions_match_github_squash_merge_identity() -> None:
    criteria = RELEASE_CRITERIA.read_text(encoding="utf-8")

    assert '.committer.login == "web-flow"' in criteria
    assert '.author.login == "roian6"' in criteria
    assert "git verify-commit HEAD" not in criteria
