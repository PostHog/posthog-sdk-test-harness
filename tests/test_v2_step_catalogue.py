"""The published phrase reference stays aligned with executable bindings."""

from pathlib import Path

from click.testing import CliRunner

from posthog_test_harness.v2.cli import main
from posthog_test_harness.v2.gherkin import compile_feature
from posthog_test_harness.v2.runner import STEPS
from posthog_test_harness.v2.step_catalogue import EXAMPLE, render_catalogue
from posthog_test_harness.v2.steps import Registry


def test_committed_catalogue_is_current():
    reference = Path(__file__).resolve().parents[1] / "docs" / "gherkin-steps.md"
    assert (
        reference.read_text() == render_catalogue()
    ), "Regenerate the phrase reference: posthog-test-harness-v2 steps > docs/gherkin-steps.md"


def test_steps_command_prints_complete_catalogue():
    result = CliRunner().invoke(main, ["steps"])
    assert result.exit_code == 0, result.output
    assert result.output == render_catalogue()
    assert result.output.count("```text\n") == len(STEPS.definitions)
    for regex, handler, argument in STEPS.definitions:
        assert f"```text\n{regex.pattern}\n```" in result.output
        assert f"Argument: `{argument or 'none'}`" in result.output
        assert f"Handler: [`{handler.__name__}`]" in result.output


def test_catalogue_uses_registered_requirements_and_stable_order():
    first, second = Registry(), Registry()

    async def json_handler(ctx, step):
        pass

    async def table_handler(ctx, step):
        pass

    first.step("z pattern", "docString", routes=("/setup", "/capture"), fixtures=("storage.empty.v1",))(json_handler)
    first.step("a pattern", "dataTable")(table_handler)
    second.step("a pattern", "dataTable")(table_handler)
    second.step("z pattern", "docString", routes=("/setup", "/capture"), fixtures=("storage.empty.v1",))(json_handler)
    rendered = render_catalogue(first)
    assert rendered == render_catalogue(second)
    assert rendered.index("```text\na pattern") < rendered.index("```text\nz pattern")
    assert "Argument: `docString`. Routes: `/capture`, `/setup`. Fixture capabilities: `storage.empty.v1`." in rendered
    assert "Argument: `dataTable`. Routes: None declared. Fixture capabilities: None declared." in rendered


def test_catalogue_example_binds_with_required_arguments():
    cases = compile_feature("Feature: Catalogue example\n\n" + EXAMPLE, "example.feature", "test")
    assert len(cases) == 1 and "@sdk:server" in cases[0].tags
    routes, fixtures, arguments = set(), set(), set()
    for step in cases[0].steps:
        handler, _ = STEPS.bind(step)
        requirements = STEPS.requirements[handler]
        routes.update(requirements["routes"])
        fixtures.update(requirements["fixtures"])
        arguments.update(step.argument)
    assert routes == {"/setup", "/capture", "/flush"}
    assert not fixtures
    assert arguments == {"docString", "dataTable"}
