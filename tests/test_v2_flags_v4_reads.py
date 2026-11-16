"""Gherkin /flags?v=4 read features: generation from the contract, bindings and selection.

The features are generated into a temporary specs tree, so these tests run without
SDK_V2_SPECS. With it set, the checked-in features must match the generator.
"""

import importlib.util
import json
from pathlib import Path

import pytest

from posthog_test_harness import flags_v4
from posthog_test_harness.v2.contracts import Contracts
from posthog_test_harness.v2.gherkin import compile_feature, load_cases
from posthog_test_harness.v2.migration import selection
from posthog_test_harness.v2.report import strict_exit_code
from posthog_test_harness.v2.runner import run
from tests.v2_flags_v4_host import FlagsV4Host
from tests.v2_flush_host import serve

ROOT = Path(__file__).resolve().parents[1]
GETTER = "migration/yaml-parity-v1/remote-flags-v4-legacy-getter.feature"
PAYLOAD = "migration/yaml-parity-v1/remote-flags-v4-legacy-payload.feature"
ROUTES = ["/setup", "/get_feature_flag", "/get_feature_flag_payload"]
FAILED_STEP = "the public flag getter should return false or no value"
# The reader cases whose requested record has a boolean failed: true.
FAILED = ["readers.dependency_error", "readers.error", "readers.error_with_value", "readers.v1_failed"]


def _generator():
    spec = importlib.util.spec_from_file_location("update_flags_v4_features", ROOT / "bin/update-flags-v4-features.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GENERATOR = _generator()


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    root = tmp_path_factory.mktemp("specs")
    for path, content in GENERATOR.rendered().items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return root


def case_id(path, name):
    return f"{path}::{name}"


def scenario_names(path):
    return [case.name for case in compile_feature(GENERATOR.rendered()[path], path, "revision")]


def test_scenarios_are_the_yaml_suite_cases():
    getter = [f"Legacy getter reads reader fixture {case}" for case in flags_v4.case_ids("readers")]
    getter += [f"Legacy getter reads typed value {case}" for case in flags_v4.case_ids("values")]
    payload = [f"Legacy payload getter reads reader fixture {case}" for case in flags_v4.case_ids("reader_payloads")]
    payload += [f"Legacy payload getter reads typed value {case}" for case in flags_v4.case_ids("values")]
    assert scenario_names(GETTER) == getter
    assert scenario_names(PAYLOAD) == payload
    assert (len(getter), len(payload)) == (75, 75)
    # The contract's legacy_payload expectation decides the payload of a failed record that carries one.
    assert "Legacy payload getter reads reader fixture readers.v1_failed" in payload


def test_each_scenario_serves_the_fixture_response_and_expects_its_reader_result():
    for path, source in ((GETTER, "readers"), (PAYLOAD, "reader_payloads")):
        for case in compile_feature(GENERATOR.rendered()[path], path, "revision"):
            fixture_case = case.name.rsplit(" ", 1)[1]
            response_source = "values" if fixture_case.startswith("v2_value.") else "readers"
            served = case.steps[3].argument["docString"]["content"]
            assert json.loads(served) == flags_v4.response(response_source, fixture_case)
            read = json.loads(case.steps[5].argument["docString"]["content"])
            assert read == {
                "distinct_id": "flags-v4-person",
                "key": flags_v4.expectation(response_source, fixture_case)["key"],
            }
            assert response_source == "values" or fixture_case in flags_v4.case_ids(source)


def test_only_failed_records_accept_false_or_no_value():
    """The getter feature applies the YAML suite's failed-record rule; every other result stays exact."""
    failed = []
    for case in compile_feature(GENERATOR.rendered()[GETTER], GETTER, "revision"):
        fixture_case = case.name.rsplit(" ", 1)[1]
        source = "values" if fixture_case.startswith("v2_value.") else "readers"
        value = flags_v4.expectation(source, fixture_case)["legacy_value"]
        if flags_v4.failed_record(source, fixture_case):
            failed.append(fixture_case)
            assert value is False
            assert case.steps[-1].text == FAILED_STEP
        elif value is None:
            assert case.steps[-1].text == "the public flag getter should return no value"
        else:
            assert case.steps[-1].text == f"the public flag getter should return exactly JSON {GENERATOR.dumps(value)}"
    assert failed == FAILED
    # A failed record has no payload, so the payload feature does not change.
    payload = compile_feature(GENERATOR.rendered()[PAYLOAD], PAYLOAD, "revision")
    assert all(step.text != FAILED_STEP for case in payload for step in case.steps)
    for case in payload:
        if case.name.rsplit(" ", 1)[1] in FAILED:
            assert case.steps[-1].text == "the public flag payload getter should return no payload"


def test_checked_in_features_match_the_contract(specs):
    for path, content in GENERATOR.rendered().items():
        assert GENERATOR.current(specs / path, content), f"Regenerate {path}"


def test_check_mode_reports_a_stale_feature(generated, tmp_path, monkeypatch, capsys):
    for path, content in GENERATOR.rendered().items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["update", "--specs", str(tmp_path), "--check"])
    assert GENERATOR.main() == 0
    # A feature generated from an earlier contract version differs only in its header.
    getter = GENERATOR.rendered()[GETTER]
    earlier = getter.replace(f"contract {flags_v4.contract_version()}:", "contract 0.0.0:", 1)
    assert earlier != getter
    (tmp_path / GETTER).write_text(earlier, encoding="utf-8")
    assert GENERATOR.main() == 0
    (tmp_path / GETTER).write_text("edited\n", encoding="utf-8")
    assert GENERATOR.main() == 1
    assert GETTER in capsys.readouterr().err


@pytest.mark.parametrize(
    "capabilities,selected",
    [
        (["flags_response_v4", "feature_flag_payloads"], {GETTER, PAYLOAD}),
        (["flags_response_v4"], {GETTER}),
        (["feature_flag_payloads"], set()),
        ([], set()),
    ],
)
def test_capabilities_select_the_getter_and_payload_features(generated, capabilities, selected):
    cases, _ = load_cases(generated, [GETTER, PAYLOAD])
    profile = {"sdk_type": "server", "sdk_capabilities": capabilities}
    chosen = {case.source["path"] for case in cases if selection(case, profile, ROUTES)["selected"]}
    assert chosen == selected


async def test_every_case_passes_against_the_contract_expectations(generated):
    contracts = Contracts()
    async with serve(contracts, host_type=FlagsV4Host) as (host, url):
        report, _ = await run(contracts, generated, [GETTER, PAYLOAD], url, host.profile["id"])
    statuses = [row["result"]["status"] for row in report["results"]]
    assert statuses == ["passed"] * 150, report
    assert strict_exit_code(contracts, report) == 0
    # Each body reached the SDK as written: no default featureFlags member was merged in.
    assert len(host.engines) == 150
    assert all(len(engine.parsed_responses) == 1 for engine in host.engines)
    assert not any("featureFlags" in engine.parsed_responses[0] for engine in host.engines)


@pytest.mark.parametrize(
    "path,fixture_case,defect,code",
    [
        (GETTER, "reader fixture readers.complete_split", "numeric_boolean", "flag_value"),
        (GETTER, "reader fixture readers.complete_split", "null_for_false", "flag_value"),
        (GETTER, "reader fixture readers.missing_flag", "missing_as_false", "flag_value"),
        # No value replaces false only for a failed record: not a healthy false, not another reader error.
        (GETTER, "reader fixture readers.old_server_false", "undefined_for_false", "flag_value"),
        (GETTER, "reader fixture readers.missing_group_key", "null_for_false", "flag_value"),
        (GETTER, "reader fixture readers.split_wrong_type_rule_id", "undefined_for_false", "flag_value"),
        # A failed record still accepts only false or no value, with strict JSON types.
        (GETTER, "reader fixture readers.error_with_value", "numeric_boolean", "flag_value"),
        (GETTER, "reader fixture readers.error_with_value", "string_for_false", "flag_value"),
        (GETTER, "reader fixture readers.error_with_value", "true_for_false", "flag_value"),
        (GETTER, "typed value v2_value.number.target", "numeric_boolean", "flag_value"),
        (PAYLOAD, "typed value v2_value.number.target", "serialized_payload", "flag_payload"),
        (PAYLOAD, "reader fixture readers.v1_at_v4", "serialized_payload", "flag_payload"),
        (PAYLOAD, "typed value v2_value.string.target", "payload_for_string", "flag_payload"),
    ],
)
async def test_wrong_answers_fail_the_read_assertion(generated, path, fixture_case, defect, code):
    contracts = Contracts()
    noun = "getter" if path == GETTER else "payload getter"
    selected = case_id(path, f"Legacy {noun} reads {fixture_case}")
    async with serve(contracts, host_type=FlagsV4Host, defect=defect) as (host, url):
        report, diagnostics = await run(contracts, generated, [path], url, host.profile["id"], case_ids=[selected])
    row = next(r for r in report["results"] if r["case_id"] == selected)["result"]
    assert row["status"] == "failed_assertion", row
    assert row["failure"]["code"] == code
    details = diagnostics["cases"][0]["failure"]["details"]
    assert details["operation"] == ("/get_feature_flag" if path == GETTER else "/get_feature_flag_payload")
    assert details["arguments"]["distinct_id"] == "flags-v4-person"
    assert strict_exit_code(contracts, report) == 1


@pytest.mark.parametrize(
    "path,fixture_case,defect",
    [
        # No value may be native undefined or JSON null.
        (GETTER, "reader fixture readers.missing_flag", "missing_as_null"),
        (PAYLOAD, "reader fixture readers.missing_flag", "missing_as_null"),
        (PAYLOAD, "typed value v2_value.string.target", "missing_as_null"),
        # Payloads compare as JSON values, so 0 and 0.0 are the same number.
        (PAYLOAD, "typed value v2_value.number.zero", "float_payload"),
        (PAYLOAD, "typed value v2_value.number.max_safe_integer", "float_payload"),
        # A failed record has no evaluated value: false, native undefined or JSON null.
        *[
            (GETTER, f"reader fixture {case}", defect)
            for case in FAILED
            for defect in ("null_for_false", "undefined_for_false")
        ],
    ],
)
async def test_equivalent_answers_pass(generated, path, fixture_case, defect):
    contracts = Contracts()
    noun = "getter" if path == GETTER else "payload getter"
    selected = case_id(path, f"Legacy {noun} reads {fixture_case}")
    async with serve(contracts, host_type=FlagsV4Host, defect=defect) as (host, url):
        report, _ = await run(contracts, generated, [path], url, host.profile["id"], case_ids=[selected])
    row = next(r for r in report["results"] if r["case_id"] == selected)["result"]
    assert row["status"] == "passed", row


@pytest.mark.parametrize(
    "path,fixture_case,defect,code",
    [
        # Each expects no value, so only the fixture-read check can catch them.
        (GETTER, "reader fixture readers.missing_flag", "no_request", "flag_fixture_read"),
        (PAYLOAD, "reader fixture readers.missing_flag", "no_request", "flag_fixture_read"),
        (PAYLOAD, "typed value v2_value.string.target", "no_request", "flag_fixture_read"),
        (GETTER, "reader fixture readers.missing_flag", "unserved_request", "flag_fixture_read"),
        (PAYLOAD, "reader fixture readers.error", "unserved_request", "flag_fixture_read"),
        (GETTER, "reader fixture readers.error", "no_request", "flag_fixture_read"),
        (GETTER, "reader fixture readers.v1_failed", "unserved_request", "flag_fixture_read"),
        # The fixture read with the wrong /flags version.
        (GETTER, "reader fixture readers.complete_split", "v2_request", "flag_fixture_read"),
        (PAYLOAD, "typed value v2_value.number.target", "v2_request", "flag_fixture_read"),
        # A read error is a thrown SDK call, never a missing value.
        (GETTER, "reader fixture readers.missing_flag", "thrown", "flag_getter_thrown"),
        (PAYLOAD, "reader fixture readers.missing_flag", "thrown", "flag_payload_getter_thrown"),
    ],
)
async def test_a_read_that_skips_the_fixture_fails(generated, path, fixture_case, defect, code):
    contracts = Contracts()
    noun = "getter" if path == GETTER else "payload getter"
    selected = case_id(path, f"Legacy {noun} reads {fixture_case}")
    async with serve(contracts, host_type=FlagsV4Host, defect=defect) as (host, url):
        report, diagnostics = await run(contracts, generated, [path], url, host.profile["id"], case_ids=[selected])
    row = next(r for r in report["results"] if r["case_id"] == selected)["result"]
    assert row["status"] == "failed_assertion", row
    assert row["failure"]["code"] == code
    if code == "flag_fixture_read":
        details = diagnostics["cases"][0]["failure"]["details"]
        assert details["fixture_reads"] == (1 if defect == "v2_request" else 0)
        assert details["fixture_read_versions"] == (["2"] if defect == "v2_request" else [])
    assert strict_exit_code(contracts, report) == 1


async def test_every_request_gets_the_body_when_a_getter_reads_twice(generated):
    contracts = Contracts()
    selected = case_id(PAYLOAD, "Legacy payload getter reads reader fixture readers.v1_at_v4")
    async with serve(contracts, host_type=FlagsV4Host, defect="repeated_request") as (host, url):
        report, _ = await run(contracts, generated, [PAYLOAD], url, host.profile["id"], case_ids=[selected])
    assert next(r for r in report["results"] if r["case_id"] == selected)["result"]["status"] == "passed"
    assert len(host.engines[0].parsed_responses) == 2


async def test_the_failed_record_step_requires_a_failed_record(tmp_path):
    """The step cannot relax the result of a record without failed: true."""
    scenario = GENERATOR.scenario("readers", "reader fixture", "readers.missing_group_key", "getter")
    assert scenario[-1] == "    And the public flag getter should return exactly JSON false"
    scenario[-1] = f"    And {FAILED_STEP}"
    feature = GENERATOR.FEATURES[Path(GETTER).name]
    target = tmp_path / GETTER
    target.parent.mkdir(parents=True)
    target.write_text(
        "\n".join([feature["tags"], "Feature: Misused step", "", GENERATOR.BACKGROUND.rstrip("\n"), "", *scenario])
        + "\n",
        encoding="utf-8",
    )
    contracts = Contracts()
    selected = case_id(GETTER, "Legacy getter reads reader fixture readers.missing_group_key")
    async with serve(contracts, host_type=FlagsV4Host, defect="null_for_false") as (host, url):
        report, _ = await run(contracts, tmp_path, [GETTER], url, host.profile["id"], case_ids=[selected])
    row = next(r for r in report["results"] if r["case_id"] == selected)["result"]
    assert row["status"] != "passed", row
    assert row["failure"]["code"] == "invalid_step_data"
    assert strict_exit_code(contracts, report) == 1
