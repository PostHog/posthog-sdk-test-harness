"""Direct official Gherkin parsing, case isolation, and CLI helpers."""

import asyncio
import os
import sys
from pathlib import Path

import aiohttp
import pytest

from posthog_test_harness.v2.contracts import BoundaryError, decode_json
from posthog_test_harness.v2.data import doc_string
from posthog_test_harness.v2.fixtures import CaseServer
from posthog_test_harness.v2.gherkin import compile_feature
from posthog_test_harness.v2.steps import contains_events, table
from tests.v2_flush_host import PROFILE

SPECS = Path(os.environ.get("SDK_V2_SPECS", Path(__file__).resolve().parents[2] / "specs"))


async def cli_run(tmp_path, url, *extra, specs=SPECS):
    report_path = tmp_path / "report.json"
    process = await asyncio.create_subprocess_exec(
        str(Path(sys.executable).with_name("posthog-test-harness-v2")),
        "run",
        "--specs",
        str(specs),
        "--adapter-url",
        url,
        "--profile",
        PROFILE["id"],
        "--timeout-ms",
        "5000",
        "--report",
        str(report_path),
        *extra,
        cwd=tmp_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), 180)
    except BaseException:
        process.kill()
        await process.wait()
        raise
    assert report_path.exists(), (stdout.decode(), stderr.decode())
    report = decode_json(report_path.read_bytes())
    diagnostics = decode_json(report_path.with_name("report.json.diagnostics.json").read_bytes())
    return process.returncode, report, diagnostics, stdout.decode()


def test_official_outlines_backgrounds_rules_tags_and_typed_arguments():
    text = '''@feature
Feature: Types
  Background:
    Given background
  @rule
  Rule: One
    Background:
      Given rule background
    @outline
    Scenario Outline: Typed <label>
      When a table:
        | string | json   |
        | false  | <json> |
      Then a document:
        """application/json
        {"value": <json>, "label": "<label>"}
        """
      @examples
      Examples:
        | label | json  |
        | zero  | 0     |
        | false | false |
        | null  | null  |
'''
    cases = compile_feature(text, "typed.feature", "test-revision")
    assert len(cases) == 3
    for case, value in zip(cases, [0, False, None]):
        assert case.tags == ["@feature", "@rule", "@outline", "@examples"]
        assert [s.text for s in case.steps[:2]] == ["background", "rule background"]
        row = table(case.steps[2], {"string": "string", "json": "json"})[0]
        assert row["string"] == "false" and type(row["json"]) is type(value) and row["json"] == value
        arg = case.steps[3].argument["docString"]
        assert type(doc_string(arg["content"], arg["mediaType"])["value"]) is type(value)
        assert case.steps[2].source["line"] == 11 and ":example-L" in case.id
    assert len({c.id for c in cases}) == 3


def test_table_does_not_silently_coerce_or_drop_columns():
    text = "Feature: Data\n  Scenario: One\n    Given table:\n      | json | json |\n      | 0 | false |\n"
    step = compile_feature(text, "data.feature", "test")[0].steps[0]
    with pytest.raises(BoundaryError, match="duplicate"):
        table(step, {"json": "json"})
    assert not contains_events([{"event": "One"}], [{"event": "One"}, {"event": "One"}])
    assert not contains_events([{"event": False}], [{"event": 0}])


async def test_retired_mock_url_cannot_leak_into_next_case():
    first, second = CaseServer(), CaseServer()
    try:
        first.fail_next_ingestion(503)
        async with aiohttp.ClientSession() as session:
            async with session.post(first.url + "/flags", json={}) as response:
                assert response.status == 200
            async with session.post(first.url + "/batch", json={"batch": [{"event": "Old"}]}) as response:
                assert response.status == 503
            first.retire()
            async with session.post(first.url + "/batch", json={"batch": [{"event": "Late"}]}) as response:
                assert response.status == 410
            async with session.post(second.url + "/batch", json={"batch": [{"event": "New"}]}) as response:
                assert response.status == 200
        assert len(first.requests()) == 2
        assert len(second.requests()) == 1
        assert second.state.get_requests()[0].parsed_events == [{"event": "New"}]
    finally:
        await asyncio.to_thread(first.close)
        await asyncio.to_thread(second.close)
