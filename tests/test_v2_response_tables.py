"""Ordered mock response tables, including the two-column form the capture specs use."""

import json
from types import SimpleNamespace

import pytest

from posthog_test_harness.v2.analytics_retry_steps import RESPONSE_COLUMNS, mock_response
from posthog_test_harness.v2.contracts import BoundaryError
from posthog_test_harness.v2.steps import table


def _rows(*rows):
    step = SimpleNamespace(argument={"dataTable": {"rows": [{"cells": [{"value": v} for v in row]} for row in rows]}})
    return table(step, RESPONSE_COLUMNS, optional=("headers", "body", "event_results"))


def test_two_column_rows_leave_optional_columns_out():
    rows = _rows(["status", "body"], ["503", "null"], ["200", '{"results":{"*":{"result":"drop"}}}'])

    responses = [mock_response(r, "/i/v1/ai/events") for r in rows]

    assert [(r.status_code, r.headers, r.body, r.path) for r in responses] == [
        (503, {}, None, "/i/v1/ai/events"),
        (200, {}, None, "/i/v1/ai/events"),
    ]
    assert responses[1].v1_event_results == {"*": {"result": "drop"}}


def test_object_body_without_wildcard_is_sent_as_json():
    rows = _rows(["status", "body"], ["400", '{"error":"request_parsing_error"}'])

    assert json.loads(mock_response(rows[0]).body) == {"error": "request_parsing_error"}


@pytest.mark.parametrize("header", [["body"], ["status", "body", "extra"], ["status", "status"]])
def test_missing_required_or_unknown_columns_are_rejected(header):
    with pytest.raises(BoundaryError):
        _rows(header, ["1"] * len(header))
