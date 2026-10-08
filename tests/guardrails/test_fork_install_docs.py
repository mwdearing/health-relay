from pathlib import Path

FORK_INSTALL = "git+https://github.com/mwdearing/health-relay.git"
UPSTREAM_INSTALL = "roian6/apple-health-ai-bridge.git@receiver-v1.1.1"

INSTALL_DOCS = (Path("README.md"), Path("docs/setup.md"))

NO_TESTFLIGHT = (  # user-facing docs checked for how the beta is offered
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


BETA_REQUEST_FORM = "issues/new?template=beta_access.yml"


def test_user_docs_that_offer_testflight_point_at_the_request_form() -> None:
    # Since 2026-10-07 the fork ships signed beta builds. A user-facing doc may
    # name the channel only together with the way to ask for access (the issue
    # form, or the README section that links it); it must never present the
    # upstream project's beta as this fork's install path.
    for path in NO_TESTFLIGHT:
        text = path.read_text(encoding="utf-8")
        if "testflight" not in text.lower():
            continue
        assert BETA_REQUEST_FORM in text or "see the README" in text, path
        before = text.lower().split("test" + "flight")[0][-200:]
        assert "apple-health-ai-bridge" not in before, path


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
        assert "inherited from upstream" in path.read_text(encoding="utf-8").lower(), (
            path
        )


def test_signing_docs_require_a_healthkit_app_id() -> None:
    for path in (Path("README.md"), Path("SECURITY.md"), Path("docs/setup.md")):
        text = path.read_text(encoding="utf-8")
        assert "HealthKit" in text, path
        assert "App ID" in text, path
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "Privacy \u203a Apps" in readme
    assert "pairing again" in readme
    assert "BGTaskSchedulerPermittedIdentifiers" in readme
    assert "provisioning profile" in readme
