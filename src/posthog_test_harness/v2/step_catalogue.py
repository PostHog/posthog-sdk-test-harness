"""Agent-readable reference generated from the execution registry."""

from itertools import groupby

from .runner import STEPS

EXAMPLE = '''@sdk:server
Scenario: Capture delivers an event
  Given an isolated SDK instance
  And the SDK is initialized with token "test-token" and flush threshold 20
  When capture is called with JSON arguments:
    """application/json
    {"event":"catalogue-example","distinct_id":"catalogue-user","properties":{"area":"checkout"}}
    """
  And pending captures are flushed
  Then exactly 1 capture request should have been received
  And the first request should contain exactly 1 parsed events
  And the first received event field "event" should equal "catalogue-example"
  And the first received event field "distinct_id" should equal "catalogue-user"
  And the first received event property "area" should equal "checkout"
  And every capture request path should be one of:
    | path                   |
    | /batch                 |
    | /i/v0/e                |
    | /i/v1/analytics/events |
'''

HEADER = """# Gherkin step catalogue

Generated from the v2 runner's complete step registry. Regenerate from the harness
checkout used for validation:

```sh
posthog-test-harness-v2 steps > docs/gherkin-steps.md
```

This reference lists **registered harness bindings**, not SDK support or conformance.
The adapter must negotiate the required public routes and fixture capabilities.
Earlier steps must establish the state used by later assertions. Legacy bindings and
fixture controls remain listed; registration alone does not make them appropriate
for new black-box acceptance tests.

## Matching and arguments

- Patterns are Python regular expressions, matched against the entire step text,
  without the `Given`, `When`, `Then`, `And`, or `But` keyword. Matching is case-sensitive.
  Supply concrete values in place of regex capture groups; do not paste a pattern
  verbatim as a parameterized Gherkin step. Exactly one binding must match.
- `none` means no doc string or table is accepted. `docString` and `dataTable` require
  that exact Gherkin argument kind. The linked handler defines the content schema,
  including JSON fields, media types, table headers, and allowed values.
- Routes and fixture capabilities are declarations for that binding. `None declared`
  does not mean a step can run independently of SDK setup or earlier observations.
- Use `discover --require-ready` to check an authored feature's bindings. Discovery
  does not negotiate adapter support or execute the SDK. Execute the selected cases
  against the real SDK to establish behavior.

## Example

This server example uses JSON arguments and a data table. It requires `/setup`,
`/capture`, and `/flush`; the adapter owns mapping JSON arguments to its public SDK API.
The table allows the listed ingestion paths without requiring a particular transport.

```gherkin
"""


def render_catalogue(registry=STEPS):
    """Render every registered pattern and its declared execution requirements."""
    lines = [HEADER.rstrip(), EXAMPLE.rstrip(), "```", "", "## Registered bindings", ""]
    definitions = sorted(registry.definitions, key=lambda row: (row[1].__module__, row[0].pattern))
    for module, entries in groupby(definitions, key=lambda row: row[1].__module__):
        lines.extend([f"### {module.rsplit('.', 1)[-1]}", ""])
        for regex, handler, argument in entries:
            requirements = registry.requirements[handler]
            routes = ", ".join(f"`{route}`" for route in sorted(requirements["routes"])) or "None declared"
            fixtures = ", ".join(f"`{fixture}`" for fixture in sorted(requirements["fixtures"])) or "None declared"
            source = f"../src/{module.replace('.', '/')}.py"
            lines.extend(
                [
                    "```text",
                    regex.pattern,
                    "```",
                    f"Argument: `{argument or 'none'}`. Routes: {routes}. Fixture capabilities: {fixtures}.",
                    f"Handler: [`{handler.__name__}`]({source}).",
                    "",
                ]
            )
    return "\n".join(lines)
