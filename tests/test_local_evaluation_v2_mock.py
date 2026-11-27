"""The definitions mock serves a separate document to v=2 requests, with ETags per document."""

import pytest

from posthog_test_harness.mock_server.scoped import ScopedMockServerState
from posthog_test_harness.mock_server.server import MockServer
from posthog_test_harness.mock_server.state import MockServerState

PATH = "/flags/definitions"
AUTH = {"Authorization": "Bearer phx_test_key"}
LEGACY = {"flags": [{"key": "legacy-banner"}], "cohorts": {}, "group_type_mapping": {}}
V2 = {"flags": [{"key": "legacy-banner"}, {"key": "layout-preview"}], "cohorts": {}, "group_type_mapping": {}}


def get(client, query=(), headers=None):
    return client.get(PATH, query_string=[("token", "phc_test_key"), *query], headers={**AUTH, **(headers or {})})


def keys(response):
    return [flag["key"] for flag in response.get_json()["flags"]]


@pytest.mark.parametrize(
    "query,served",
    [
        ((), "legacy"),
        ((("v", "2"),), "v2"),
        ((("v", "1"),), "legacy"),
        ((("v", "3"),), "legacy"),
        ((("v", "2.0"),), "legacy"),
        ((("v", " 2"),), "legacy"),
        ((("v", ""),), "legacy"),
        ((("v", "2"), ("v", "2")), "legacy"),
        ((("v", "1"), ("v", "2")), "legacy"),
        ((("V", "2"),), "legacy"),
    ],
)
def test_only_exactly_one_v_equal_to_2_selects_the_v2_document(query, served):
    state = MockServerState()
    state.set_definitions(LEGACY)
    state.set_v2_definitions(V2)
    response = get(MockServer(state).app.test_client(), query)
    assert response.status_code == 200
    assert keys(response) == [flag["key"] for flag in (V2 if served == "v2" else LEGACY)["flags"]]


def test_a_v2_request_gets_the_legacy_document_and_etag_when_no_v2_document_is_set():
    state = MockServerState()
    state.set_definitions(LEGACY, etag='"legacy"')
    client = MockServer(state).app.test_client()
    response = get(client, [("v", "2")])
    assert keys(response) == ["legacy-banner"]
    assert response.headers["ETag"] == '"legacy"'
    assert get(client, [("v", "2")], {"If-None-Match": '"legacy"'}).status_code == 304


def test_each_request_records_its_whole_query():
    state = MockServerState()
    client = MockServer(state).app.test_client()
    get(client, [("v", "2")])
    get(client, [("v", "1"), ("v", "2"), ("send_cohorts", "")])
    get(client)
    recorded = state.get_definition_requests()
    assert [r.query_param_lists for r in recorded] == [
        {"token": ["phc_test_key"], "v": ["2"]},
        {"token": ["phc_test_key"], "v": ["1", "2"], "send_cohorts": [""]},
        {"token": ["phc_test_key"]},
    ]
    assert recorded[0].query_params == {"token": "phc_test_key", "v": "2"}


def test_etags_are_per_document():
    state = MockServerState()
    state.set_definitions(LEGACY, etag='"legacy"')
    state.set_v2_definitions(V2, etag='"v2"')
    client = MockServer(state).app.test_client()

    first = get(client, [("v", "2")])
    assert first.status_code == 200 and first.headers["ETag"] == '"v2"'
    assert keys(first) == ["legacy-banner", "layout-preview"]

    poll = get(client, [("v", "2")], {"If-None-Match": '"v2"'})
    assert poll.status_code == 304
    assert poll.headers["ETag"] == '"v2"'
    assert poll.get_data() == b""

    # The v=2 ETag is not the legacy document's, so a poll that drops v=2 gets the legacy body.
    dropped = get(client, (), {"If-None-Match": '"v2"'})
    assert dropped.status_code == 200 and dropped.headers["ETag"] == '"legacy"'
    assert keys(dropped) == ["legacy-banner"]
    assert get(client, (), {"If-None-Match": '"legacy"'}).status_code == 304
    assert get(client, [("v", "2")], {"If-None-Match": '"legacy"'}).status_code == 200

    statuses = [(r.response_status, r.response_headers.get("ETag")) for r in state.get_definition_requests()]
    assert statuses == [(200, '"v2"'), (304, '"v2"'), (200, '"legacy"'), (304, '"legacy"'), (200, '"v2"')]
    assert state.get_definition_requests()[1].response_body is None


@pytest.mark.parametrize("header", ['W/"v2"', '"other", "v2"', "*"])
def test_if_none_match_uses_weak_comparison_over_a_list(header):
    state = MockServerState()
    state.set_v2_definitions(V2, etag='"v2"')
    assert get(MockServer(state).app.test_client(), [("v", "2")], {"If-None-Match": header}).status_code == 304


def test_without_an_etag_there_is_no_etag_header_and_no_304():
    state = MockServerState()
    state.set_definitions(LEGACY)
    state.set_v2_definitions(V2)
    client = MockServer(state).app.test_client()
    for query in ((), [("v", "2")]):
        response = get(client, query, {"If-None-Match": "*"})
        assert response.status_code == 200
        assert "ETag" not in response.headers


def test_unauthorized_requests_get_401_without_an_etag():
    state = MockServerState()
    state.set_v2_definitions(V2, etag='"v2"')
    response = (
        MockServer(state)
        .app.test_client()
        .get(PATH, query_string={"token": "phc_test_key", "v": "2"}, headers={"If-None-Match": '"v2"'})
    )
    assert response.status_code == 401
    assert "ETag" not in response.headers


def test_each_setter_replaces_only_its_own_document():
    state = MockServerState()
    state.set_definitions(LEGACY, etag='"legacy"')
    state.set_v2_definitions(V2, etag='"v2"')
    state.set_definitions({"flags": [{"key": "replaced"}]})
    client = MockServer(state).app.test_client()
    legacy = get(client)
    assert keys(legacy) == ["replaced"] and "ETag" not in legacy.headers
    assert keys(get(client, [("v", "2")])) == ["legacy-banner", "layout-preview"]
    state.set_v2_definitions({"flags": []})
    v2 = get(client, [("v", "2")])
    assert keys(v2) == [] and "ETag" not in v2.headers


def test_v2_document_is_a_snapshot_and_isolated_by_partition_and_reset():
    state = MockServerState()
    scoped = ScopedMockServerState(state, "a")
    document = {"flags": [{"key": "layout-preview"}]}
    scoped.set_v2_definitions(document, etag='"v2"')
    document["flags"].clear()
    client = MockServer(state).app.test_client()
    response = get(client, [("v", "2")], {"X-Test-Id": "a"})
    assert response.get_json() == {"flags": [{"key": "layout-preview"}], "cohorts": {}, "group_type_mapping": {}}
    other = get(client, [("v", "2")], {"X-Test-Id": "b"})
    assert keys(other) == [] and "ETag" not in other.headers
    scoped.reset()
    assert keys(get(client, [("v", "2")], {"X-Test-Id": "a"})) == []


def test_v2_document_credentials_are_the_partition_credentials():
    state = MockServerState()
    state.set_v2_definitions(V2, api_key="project", personal_api_key="personal")
    client = MockServer(state).app.test_client()
    ok = client.get(PATH, query_string={"token": "project", "v": "2"}, headers={"Authorization": "Bearer personal"})
    assert ok.status_code == 200 and keys(ok) == ["legacy-banner", "layout-preview"]
    assert get(client, [("v", "2")]).status_code == 401
