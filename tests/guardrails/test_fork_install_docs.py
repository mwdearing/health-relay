from pathlib import Path

FORK_INSTALL = "git+https://github.com/mwdearing/health-relay.git"
UPSTREAM_INSTALL = "roian6/apple-health-ai-bridge.git@receiver-v1.1.1"

INSTALL_DOCS = (Path("README.md"), Path("docs/setup.md"))

NO_TESTFLIGHT = (
    Path("docs/self-build.md"),
    Path("docs/agent-assisted-setup.md"),
    Path("docs/contribution-ideas.md"),
    Path("docs/roadmap.md"),
    Path("docs/versioning.md"),
    Path(".github/ISSUE_TEMPLATE/bug_report.yml"),
    Path(".github/ISSUE_TEMPLATE/setup_feedback.yml"),
)


def test_install_docs_use_this_repository() -> None:
    for path in INSTALL_DOCS:
        text = path.read_text(encoding="utf-8")
        assert FORK_INSTALL in text, path
        assert UPSTREAM_INSTALL not in text, path


def test_user_docs_do_not_offer_testflight() -> None:
    for path in NO_TESTFLIGHT:
        assert "testflight" not in path.read_text(encoding="utf-8").lower(), path


def test_roadmap_opens_with_healthrelay() -> None:
    text = Path("docs/roadmap.md").read_text(encoding="utf-8")
    assert "Apple Health AI Bridge 1.1.0 is a coordinated release" not in text


def test_versioning_describes_fork_release_process() -> None:
    text = Path("docs/versioning.md").read_text(encoding="utf-8")
    assert "app-v" in text
    assert "component-versions.json" in text
    assert "receiver-v*" in text  # states these tags are not pushed


def test_inherited_release_machinery_is_labelled() -> None:
    for path in (Path(".github/release/README.md"), Path("docs/maintainers/README.md")):
        assert "inherited from upstream" in path.read_text(encoding="utf-8").lower(), path
