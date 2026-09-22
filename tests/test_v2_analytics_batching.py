"""Public behavior and deliberate defect regressions over the draft2 HTTP adapter."""

import asyncio
import json
import time
from copy import deepcopy
from types import SimpleNamespace

import pytest

from posthog_test_harness.v2.ai_steps import json_arguments
from posthog_test_harness.v2.analytics_wire_steps import STEPS, capture_sequence
from posthog_test_harness.v2.contracts import Contracts
from posthog_test_harness.v2.report import strict_exit_code
from posthog_test_harness.v2.runner import run
from tests.v2_analytics_wire_host import AnalyticsWireHost
from tests.v2_flush_host import serve

FEATURE = "migration/yaml-parity-v1/capture-analytics-v1-batching.feature"


@pytest.fixture(scope="module")
def contracts():
    return Contracts()


DEFECTS = [
    ('property_value:0:custom_string:"wrong"', 0, "event_property"),
    ('property_value:0:custom_number:"42"', 0, "event_property"),
    ("property_value:0:custom_bool:false", 0, "event_property"),
    ("missing_property:custom_bool", 0, "event_property"),
    ("property_value:0:$set:[]", 1, "event_property_object"),
    ("property_value:0:$set_once:null", 2, "event_property_object"),
    ("missing_property:$groups", 3, "event_property_object"),
    *[("array_properties", index, "event_property_object") for index in (1, 2, 3, 11, 12, 13)],
    ("null_properties", 1, "event_property_object"),
    ("missing_uuid", 4, "event_field_missing"),
    ("invalid_uuid", 4, "event_uuid"),
    ("omit_index_field:1:event", 5, "event_fields"),
    ("invalid_uuid", 6, "event_uuid"),
    ("second_event_offset", 7, "event_timestamp"),
    ("numeric_identity", 8, "event_string"),
    ("property_identity", 9, "event_placement"),
    ("property_value:0:custom_number:41", 10, "event_property"),
    ("duplicate_uuids", 14, "uuid_unique"),
    ("short_batch", 15, "batch_count"),
    ("duplicate_request", 15, "request_count"),
    ("missing_header:Authorization", 16, "missing_header"),
    ("omit_body:created_at", 16, "body_format"),
    ("empty_network", 17, "request_count"),
    ("startup_flags", 17, "request_count"),
    ("startup_capture", 17, "request_count"),
    ("suppress_threshold", 18, "request_count"),
    ("created_at_delta:-10", 19, "created_at_recent"),
    ("created_at_delta:10", 19, "created_at_recent"),
    ("offset_created_at", 19, "created_at_recent"),
    ("omit_body:created_at", 19, "created_at_recent"),
    ("non_json_body", 19, "created_at_recent"),
    ("duplicate_uuids", 20, "uuid_unique"),
    ("duplicate_request", 20, "uuid_unique"),
    ("duplicate_uuids", 21, "uuid_pair"),
    ("missing_uuid", 21, "uuid_pair"),
]


@pytest.mark.parametrize("defect,index,code", DEFECTS)
async def test_assertion_families_reject_attributed_http_defects(contracts, defect, index, code, specs, case_ids):
    async with serve(contracts, host_type=AnalyticsWireHost, defect=defect) as (host, url):
        report, _ = await run(contracts, specs, [FEATURE], url, host.profile["id"], case_ids=[case_ids[index]])
    result = report["results"][index]["result"]
    assert result["status"] == "failed_assertion", result
    assert result["failure"]["code"] == code
    assert result["failure"]["failed_step"]["source"]["path"] == FEATURE
    assert len(host.closed) == 1 and strict_exit_code(contracts, report) == 1


@pytest.mark.parametrize(
    "variation,index",
    [
        ("property_value:0:custom_bool:1", 0),
        ("property_value:0:custom_number:42.0", 0),
        ("property_value:0:$set:{}", 1),
        ('property_value:0:$set_once:{"other":false}', 2),
        ("property_value:0:$groups:{}", 3),
        ("second_event_invalid_uuid", 6),
        ("second_event_numeric_identity", 8),
        ("null_properties", 9),
        ('property_value:1:custom_number:"wrong"', 10),
        ("property_value:1:$set:null", 11),
        ("property_value:1:$set_once:[]", 12),
        ("property_value:1:$groups:false", 13),
        ("omit_index_field:1:uuid", 14),
        ("duplicate_request", 18),
        ("created_at_delta:-2", 19),
        ("created_at_delta:2", 19),
        ("missing_uuid", 20),
        ("no_delivery", 20),
        ("append_duplicate_uuid", 21),
    ],
)
async def test_weaker_source_assertions_remain_weak(contracts, variation, index, specs, case_ids):
    async with serve(contracts, host_type=AnalyticsWireHost, defect=variation) as (host, url):
        report, _ = await run(contracts, specs, [FEATURE], url, host.profile["id"], case_ids=[case_ids[index]])
    assert report["results"][index]["result"]["status"] == "passed", report
    assert strict_exit_code(contracts, report) == 0


async def test_sequence_preserves_nested_types_and_only_formats_top_level_strings_sequentially(feature_cases):
    step = deepcopy(feature_cases[5].steps[3])
    template = {
        "distinct_id": "user_{index}",
        "event": "event_{index}",
        "properties": {
            "nested": {"literal": "{index}"},
            "array": ["{index}", False, 42, None],
            "bool": True,
            "number": 0,
        },
    }
    step.argument["docString"]["content"] = json.dumps(template)
    calls, active = [], False

    async def call(route, args):
        nonlocal active
        assert not active
        active = True
        await asyncio.sleep(0)
        calls.append((route, deepcopy(args)))
        active = False

    await capture_sequence(SimpleNamespace(call=call), step, "3")
    assert calls == [("/capture", {**template, "distinct_id": f"user_{i}", "event": f"event_{i}"}) for i in range(3)]
    assert json_arguments(step) == template


async def test_batch_count_and_uniqueness_use_their_original_request_scopes(feature_cases):
    observed = [SimpleNamespace(parsed_events=[{}] * 5), SimpleNamespace(parsed_events=[{"uuid": "later"}])]
    ctx = SimpleNamespace(server=SimpleNamespace(state=SimpleNamespace(get_requests=lambda: observed)))
    for step in (feature_cases[15].steps[-1], feature_cases[20].steps[-1]):
        handler, args = STEPS.bind(step)
        await handler(ctx, step, *args)
    # UUID pair collection skips absent fields and compares only the first two present values.
    observed[0].parsed_events = [{}, {"uuid": None}, {"uuid": "later"}, {"uuid": "later"}]
    step = feature_cases[21].steps[-1]
    handler, args = STEPS.bind(step)
    await handler(ctx, step, *args)


async def test_threshold_observation_waits_the_full_real_second_without_calling_sdk(feature_cases):
    step = feature_cases[18].steps[-2]
    handler, args = STEPS.bind(step)
    started = time.monotonic()
    await handler(SimpleNamespace(), step, *args)
    assert time.monotonic() - started >= 1
