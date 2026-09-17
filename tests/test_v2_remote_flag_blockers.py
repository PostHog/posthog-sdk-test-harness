"""Frozen remote blocker history remains exact after approved semantic reconciliation."""

import hashlib
import json

import pytest

from posthog_test_harness.v2.contracts import BoundaryError, Contracts, json_equal
from posthog_test_harness.v2.discovery import discover
from posthog_test_harness.v2.migration import SUITE, migration_manifest, migration_paths
from tests.test_v2_gherkin import CONTRACT_PATH, SPECS

SOURCE = "contracts/feature_flags_tests.yaml"
ORIGINS = [
    row
    for line in (SPECS / "coverage/harness-v2/legacy-cases.jsonl").read_text().splitlines()
    if (row := json.loads(line))["source"]["path"] == SOURCE
]
BLOCKERS = json.loads((SPECS / SUITE / "remote-flag-blocked-cases.json").read_text())


@pytest.mark.parametrize("origin,row", zip(ORIGINS, BLOCKERS), ids=[r["name"] for r in ORIGINS])
def test_remote_flag_blocker_preserves_exact_source_inputs_filters_and_order(origin, row):
    assert row["legacy_id"] == origin["id"]
    assert row["legacy_source"] == origin["source"]
    assert row["legacy_filters"] == origin["capability_filters"]
    assert json_equal(row["source_inputs_and_ordered_assertions"], [s["input"] for s in origin["steps"]])
    assert row["id"] == "migration:yaml-parity-v1:feature_flags:" + origin["name"]
    assert row["status"] == "blocked_contract" and row["translation"] == "not_translated"
    assert row["execution_evidence"] == row["native_sdk_evidence"] == []
    assert row["selection_status"] == "pending_contract_decision"
    assert row["candidate_routes"] == ["/get_feature_flag"]
    assert row["sdk_capabilities"] == []
    getters = [a for a in row["source_inputs_and_ordered_assertions"] if a["action"] == "get_feature_flag"]
    expected = ["remote-getter-force-remote"] if getters else ["flag-startup-default"]
    if len(getters) == 2:
        expected.append("evaluated-result-cache-default")
    assert row["contract_conflicts"] == expected


def test_remote_flag_blocker_manifest_and_source_digest():
    assert len(ORIGINS) == len(BLOCKERS) == 17
    assert [r["source_case_number"] for r in BLOCKERS] == list(range(1, 18))
    manifest = migration_manifest(SPECS)
    ledger = manifest["remote_flag_blockers"]
    assert ledger["path"] == SUITE + "/remote-flag-blocked-cases.json"
    assert hashlib.sha256((SPECS / ledger["path"]).read_bytes()).hexdigest() == ledger["sha256"]
    inventory = json.loads((SPECS / "coverage/harness-v2/manifest.json").read_text())
    pinned = next(s for s in inventory["sources"] if s["path"] == SOURCE)
    assert next(s for s in manifest["remote_flag_blocker_sources"] if s["path"] == SOURCE) == pinned


def test_force_remote_is_not_representable_by_the_frozen_getter_schema():
    contracts = Contracts(CONTRACT_PATH)
    schema = contracts.operations["/get_feature_flag"]["arguments_schema"].split("/")[-1]
    calls = [
        a["params"]
        for row in BLOCKERS
        for a in row["source_inputs_and_ordered_assertions"]
        if a["action"] == "get_feature_flag"
    ]
    assert len(calls) == 16
    for args in calls:
        assert args["force_remote"] is True
        with pytest.raises(BoundaryError, match="Invalid"):
            contracts.validate(schema, args, "catalog")
        # The approved amendment removes forcing; all other source inputs remain exact.
        contracts.validate(schema, {k: v for k, v in args.items() if k != "force_remote"}, "catalog")


def test_historical_remote_blockers_are_resolved_only_by_separate_amended_rows():
    ledger = json.loads((SPECS / SUITE / "cases.json").read_text())
    report = discover(SPECS, migration_paths(SPECS))
    assert {r["case_id"] for r in report["cases"]} == {r["id"] for r in ledger}
    translated = [r for r in ledger if r["id"] in {b["id"] for b in BLOCKERS}]
    assert len(translated) == 17
    assert all(r["amendment"] == "flag-semantics-v1" for r in translated)
    assert len(ledger) == 157
    assert len(json.loads((SPECS / SUITE / "blocked-cases.json").read_text())) == 12


def test_remote_source_retains_omissions_repeated_calls_and_observation_windows():
    actions = {o["name"]: [s["input"] for s in o["steps"]] for o in ORIGINS}
    for steps in actions.values():
        setup = next(a for a in steps if a["action"] == "init")
        assert set(setup.get("params", {})) <= {"api_key"}
        assert all(a["action"] != "wait" for a in steps)
    repeated = actions["two_flag_calls_produce_two_remote_requests"]
    assert repeated[1] == repeated[2]
    assert repeated[-1]["params"] == {"expected": 2}
    init_only = actions["no_flags_request_on_init_alone"]
    assert [a["action"] for a in init_only] == ["init", "assert_flags_request_count"]
    normal = actions["no_flags_request_on_normal_capture"]
    assert [a["action"] for a in normal] == ["init", "capture", "flush", "assert_flags_request_count"]
    first = actions["request_with_person_properties_device_id"][1]["params"]
    assert first["person_properties"] == {"$device_id": "device_abc_123"}
    assert first["groups"] == first["group_properties"] == {}
    assert "groups" not in actions["groups_default_to_empty_object"][1]["params"]
    assert actions["disable_geoip_false_propagates_as_geoip_disable_false"][1]["params"]["disable_geoip"] is False
    assert "disable_geoip" not in actions["disable_geoip_omitted_defaults_to_false"][1]["params"]
