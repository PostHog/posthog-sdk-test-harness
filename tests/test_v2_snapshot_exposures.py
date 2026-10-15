"""Exposure matching preserves independent group and canonical-value contexts."""

import json
from types import SimpleNamespace

import pytest

from posthog_test_harness.v2.snapshot_steps import traffic


@pytest.mark.parametrize("protocol", ["legacy", "analytics_v1"])
@pytest.mark.parametrize("separation", ["groups", "response"])
async def test_same_identity_key_matches_independent_exposure_contexts(protocol, separation):
    targets = [
        {"distinct_id": "same-user", "key": "checkout", "groups": {"organization": "org-a"}, "value": "control"},
        {
            "distinct_id": "same-user",
            "key": "checkout",
            "groups": {"organization": "org-b" if separation == "groups" else "org-a"},
            "value": "control" if separation == "groups" else False,
        },
    ]
    events = [
        {
            "event": "$feature_flag_called",
            "distinct_id": target["distinct_id"],
            "properties": {
                "$feature_flag": target["key"],
                "$groups": target["groups"],
                "$feature_flag_response": target["value"],
            },
        }
        for target in reversed(targets)
    ]
    captured = SimpleNamespace(
        path="/batch" if protocol == "legacy" else "/i/v1/analytics/events", parsed_events=events
    )
    ctx = SimpleNamespace(
        server=SimpleNamespace(
            flag_requests=lambda: [], requests=lambda: [], state=SimpleNamespace(get_requests=lambda: [captured])
        )
    )
    step = SimpleNamespace(
        argument={
            "docString": {
                "content": json.dumps({"requests": [], "exposures": targets}),
                "mediaType": "application/json",
            }
        }
    )
    await traffic(ctx, step)
