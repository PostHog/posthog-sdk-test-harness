"""Boundary proof against a controlled HTTP host, not SDK conformance."""

import asyncio
import os
from contextlib import asynccontextmanager
from contextvars import ContextVar
from copy import deepcopy
from pathlib import Path
from urllib.parse import quote

import pytest
from aiohttp import web

from posthog_test_harness.v2.client import Client
from posthog_test_harness.v2.contracts import VERSION, BoundaryError, Contracts, decode_json, encode_json
from posthog_test_harness.v2.data import arguments_from_cells, cell, doc_string
from posthog_test_harness.v2.report import strict_exit_code, validate_report

CONTRACT_PATH = Path(os.environ.get("SDK_V2_CONTRACTS", Path(__file__).resolve().parents[2] / "specs/contracts/v2"))
PROFILE = {
    "id": "test-profile",
    "runtime": {"family": "server", "name": "controlled-host", "version": "1", "execution_context": "async_local"},
    "identity": "request_scoped",
    "protocol": "legacy",
    "products": ["analytics"],
    "module": {"entry": "test-double", "format": "native", "package": "test-double", "version": "1"},
    "fixture_capabilities": ["callbacks.continuation", "references.value", "references.exception"],
}
SOURCE = {"revision": "9cb330e3bac8868f39cc7dd665e42817285c9493", "path": "acceptance/public/flush.feature", "line": 12}
ACTIVE = ContextVar("controlled_native_context", default=None)


@pytest.fixture(scope="module")
def contracts():
    return Contracts(CONTRACT_PATH)


def invoke(route="/capture", args=None, references=None):
    result = {
        "call_id": "call",
        "route": route,
        "receiver": {"kind": "instance", "id": "receiver"},
        "args": {"event": "event"} if args is None else args,
    }
    if references is not None:
        result["references"] = references
    return result


class Host:
    """Independent wire fixture: no v1 helpers, SDK queues, counters or bindings."""

    def __init__(self, contracts):
        self.contracts = contracts
        self.paths, self.inputs, self.closed = [], [], []
        self.outcome = None
        self.fault = None
        self.delay = asyncio.Event()
        self.entered = asyncio.Event()
        self.cancel_ack = asyncio.Event()
        self.events, self.plans = [], {}
        self.routes = list(contracts.operations)
        self.cancelled = False
        self.native_contexts = []

    async def handle(self, request):
        path = request.path.removeprefix("/v2/")
        self.paths.append(path)
        if path == "negotiate" and self.fault == "v1":
            return web.Response(status=404)
        data = decode_json(await request.read())
        if path == "negotiate":
            assert data == {
                "contract_version": VERSION,
                "catalog_sha256": self.contracts.catalog_hash,
                "transport": "http-json-v2",
            }
            return web.json_response(
                {
                    "kind": "accepted",
                    **data,
                    "session_id": "controlled-session",
                    "adapter": {"name": "test-double", "version": "1"},
                    "profiles": [PROFILE],
                    "supported_routes": self.routes,
                    "max_timeout_ms": 300000,
                }
            )
        assert request.headers["Authorization"] == "Bearer controlled-session"
        fixture_id = data["fixture_id"]
        if path == "fixtures/allocate":
            return web.json_response(
                {
                    "kind": "allocated",
                    "fixture_id": fixture_id,
                    "receiver": {"kind": "instance", "id": fixture_id + "-receiver"},
                }
            )
        if path == "fixtures/close":
            self.closed.append(fixture_id)
            self.delay.set()
            if self.fault == "close":
                return web.Response(status=500)
            return web.json_response({"kind": "closed", "fixture_id": fixture_id})
        if path == "fixtures/references":
            fixture = data["fixture"]
            if fixture["kind"] == "callback":
                self.plans[data["reference_id"]] = fixture["plan"]
            return web.json_response(
                {
                    "kind": "created",
                    "fixture_id": fixture_id,
                    "reference": {"kind": fixture["kind"], "id": data["reference_id"]},
                }
            )
        if path == "fixtures/observations":
            events = self.events[data["after_sequence"] :]
            return web.json_response({"fixture_id": fixture_id, "cursor": len(self.events), "observations": events})
        if path == "fixtures/context-scope":
            self.contracts.validate("ContextScopeRequest", data)
            token = ACTIVE.set({"distinct_id": "scoped-user"})
            try:
                receipts = [
                    {
                        "fixture_id": fixture_id,
                        "call_id": call["call_id"],
                        "route": call["route"],
                        "completion": {"kind": "sdk", "outcome": {"kind": "value", "value": ACTIVE.get()}},
                    }
                    for call in data["calls"]
                ]
                return web.json_response(
                    {
                        "fixture_id": fixture_id,
                        "scope_id": data["scope_id"],
                        "calls": receipts,
                        "result": {"kind": "completed"},
                    }
                )
            finally:
                ACTIVE.reset(token)
        if path == "cancel":
            self.cancelled = True
            if self.fault != "cancel_ack_first":
                self.delay.set()
            if self.fault == "cancel_receipt_first":
                await self.cancel_ack.wait()
            return web.json_response({"fixture_id": fixture_id, "call_id": data["call_id"], "state": "cancelled"})
        assert path == "invoke"
        self.contracts.validate("InvokeRequest", data)
        self.inputs.append(data)
        self.entered.set()
        if self.fault == "http":
            return web.Response(status=503)
        if self.fault == "json":
            return web.Response(text='{"receipt":null,"receipt":null}', content_type="application/json")
        if self.fault == "large":
            return web.Response(body=b" " * (1024 * 1024 + 1), content_type="application/json")
        if self.fault == "redirect":
            return web.Response(status=302, headers={"Location": "/v2/invoke"})
        if self.fault in ("slow", "cancel", "cancel_receipt_first", "cancel_ack_first"):
            await self.delay.wait()
        call = data["invoke"]
        if self.outcome is None:
            # This controlled native method has an explicit void signature. Mapping
            # its None completion is independent of the invoked route's expectation.
            assert self.native_void() is None
            outcome = {"kind": "void"}
        else:
            outcome = deepcopy(self.outcome)  # Deliberate native-outcome/defect fixture.
        completion = {"kind": "sdk", "outcome": outcome}
        if self.cancelled or self.fault == "host_timeout":
            completion = {
                "kind": "harness",
                "failure": {
                    "kind": "cancelled" if self.cancelled else "timeout",
                    "code": "host_deadline",
                    "message": "Host terminated execution",
                },
            }
        if call["route"] in ("/with_context", "/with_span"):
            completion = {"kind": "sdk", "outcome": await self.native_callback(fixture_id, call)}
        receipt = {
            "fixture_id": fixture_id,
            "call_id": call["call_id"],
            "route": call["route"],
            "completion": completion,
        }
        if self.fault == "attribution":
            receipt["call_id"] = "another-call"
        if self.fault in ("early", "early_conflict"):
            self.events.append({"kind": "call", "sequence": 1, "receipt": deepcopy(receipt)})
            await self.delay.wait()
            if self.fault == "early_conflict":
                receipt["completion"]["outcome"]["value"] = 0
        return web.json_response({"receipt": receipt})

    def native_void(self) -> None:
        return None

    async def native_callback(self, fixture_id, call):
        """Real sync/async call contexts in the controlled host; no HTTP callback replay."""
        ref_id = call["references"]["/callback"]["id"]
        plan = self.plans[ref_id]
        token = ACTIVE.set(call["args"].get("context", {"distinct_id": "async-user"}))
        prefix = f"@callback/{quote(fixture_id, safe='-._~')}/{quote(ref_id, safe='-._~')}/0"
        ids = []

        def callback():
            for step in plan["calls"]:
                assert step["route"] == "/get_context"
                current = ACTIVE.get()
                self.native_contexts.append(current)
                call_id = prefix + "/" + quote(step["step_id"], safe="-._~")
                ids.append(call_id)
                receipt = {
                    "fixture_id": fixture_id,
                    "call_id": call_id,
                    "route": step["route"],
                    "parent_call_id": call["call_id"],
                    "callback_invocation_id": prefix,
                    "completion": {"kind": "sdk", "outcome": {"kind": "value", "value": current}},
                }
                self.events.append({"kind": "call", "sequence": len(self.events) + 1, "receipt": receipt})
            return deepcopy(plan["returns"]["outcome"])

        try:
            if call["route"] == "/with_span":
                await asyncio.sleep(0)  # Native asynchronous boundary, still on owning context.
            result = callback()  # Synchronous invocation on the native owning stack.
            self.events.append(
                {
                    "kind": "callback",
                    "sequence": len(self.events) + 1,
                    "fixture_id": fixture_id,
                    "callback": {"kind": "callback", "id": ref_id},
                    "invocation_id": prefix,
                    "invocation_index": 0,
                    "owner_call_id": call["call_id"],
                    "args": (
                        [
                            {
                                "kind": "value",
                                "value": {"kind": "span", "id": "native-span"},
                                "retained": {"kind": "span", "id": "native-span"},
                            }
                        ]
                        if call["route"] == "/with_span"
                        else []
                    ),
                    "completion": {"kind": "sdk", "outcome": result},
                    "call_ids": ids,
                }
            )
            return result
        finally:
            ACTIVE.reset(token)
            assert ACTIVE.get() is None


@asynccontextmanager
async def serve(contracts):
    host = Host(contracts)
    app = web.Application()
    app.router.add_post("/v2/{tail:.*}", host.handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        yield host, f"http://127.0.0.1:{port}"
    finally:
        host.delay.set()
        host.cancel_ack.set()
        await runner.cleanup()


def test_all_typed_schemas(contracts):
    assert len(contracts.operations) == 180
    for operation in contracts.operations.values():
        assert operation["arguments_schema"] and operation["result_schema"]
    contracts.validate(
        "OpSetupArgs",
        {
            "project_token": "test",
            "config": {
                "bootstrap": {"feature_flags": {"flag": False}},
                "before_send": [{"kind": "callback", "id": "cb"}],
            },
        },
        "catalog",
    )
    with pytest.raises(BoundaryError):
        contracts.validate("OpSetConfigArgs", {"config": {"bootstrap": {}}}, "catalog")
    with pytest.raises(BoundaryError):
        contracts.validate("OpDisplaySurveyArgs", {"survey_id": "s", "options": {"display_type": "inline"}}, "catalog")


@pytest.mark.parametrize("value", [False, 0, None, "", [], {}, {"a": [False, None]}])
def test_typed_preservation(contracts, value):
    args, refs = arguments_from_cells(
        contracts, {"supplied": {"kind": "json", "value": value}, "absent": {"kind": "omitted"}}
    )
    assert "absent" not in args and not refs
    assert type(args["supplied"]) is type(value) and args["supplied"] == value
    assert decode_json(encode_json(value)) == value
    assert cell("false", "string")["value"] == "false"
    assert cell("false", "json")["value"] is False
    assert doc_string('{"x":null}', "application/json") == {"x": None}


@pytest.mark.parametrize(
    "text",
    [
        '{"a":0,"a":1}',
        '{"a":0,"\\u0061":1}',
        '{"x":NaN}',
        '{"x":1e999}',
        '{"x":Infinity}',
        "{}{}",
        "[1,]",
        "/*comment*/ {}",
        b'"\xff"',
    ],
)
def test_strict_json(text):
    with pytest.raises(BoundaryError):
        decode_json(text)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), {1: "number-key"}, (1, 2), object()])
def test_non_json_inputs(value):
    with pytest.raises(BoundaryError):
        encode_json(value)


def test_negative_inputs_and_reference_structure(contracts):
    live = {"receiver": "instance", "cb": "callback", "v": "value"}
    contracts.validate_invoke(invoke("/capture", {"event": 42}), live)
    contracts.validate_invoke(invoke("/identify", {}), live)
    valid = invoke("/flush", {}, {"/callback": {"kind": "callback", "id": "cb"}})
    contracts.validate_invoke(valid, live)
    with pytest.raises(BoundaryError):
        contracts.validate_invoke({**valid, "args": {"callback": None}}, live)
    with pytest.raises(BoundaryError):
        contracts.validate_invoke(valid, {"receiver": "instance"})
    for pointer in ("", "callback", "/bad~2", "/missing/child", "/unknown"):
        with pytest.raises(BoundaryError):
            contracts.validate_invoke(invoke("/flush", {}, {pointer: {"kind": "value", "id": "v"}}), live)
    contracts.validate_invoke(
        invoke("/capture", {"properties": {}}, {"/properties/a~1b~0": {"kind": "value", "id": "v"}}), live
    )
    refs = {
        "/config/before_send/1": {"kind": "callback", "id": "cb"},
        "/config/before_send/0": {"kind": "callback", "id": "cb"},
    }
    contracts.validate_invoke(invoke("/setup", {"config": {"before_send": []}}, refs), live)
    with pytest.raises(BoundaryError):
        contracts.validate_invoke(invoke("/setup", {"config": {"before_send": [None]}}, refs), live)
    with pytest.raises(BoundaryError):
        contracts.validate_invoke(invoke("/span/end", {}), live)


@pytest.mark.asyncio
async def test_negotiation_no_v1_fallback(contracts):
    async with serve(contracts) as (host, url):
        host.fault = "v1"
        with pytest.raises(BoundaryError):
            async with Client(url, contracts):
                pytest.fail("v1 accepted")
        assert host.paths == ["negotiate"]


@pytest.mark.asyncio
async def test_http_omission_negative_arguments_and_outcomes(contracts):
    async with serve(contracts) as (host, url):
        async with Client(url, contracts) as client:
            async with client.fixture("f", "case", PROFILE["id"]) as fixture:
                for i, outcome in enumerate(
                    [
                        {"kind": "void"},
                        {"kind": "undefined"},
                        {"kind": "value", "value": None},
                        {"kind": "value", "value": False},
                        {"kind": "thrown", "error": {"kind": "exception", "id": "native-error"}},
                    ]
                ):
                    host.outcome = outcome
                    args = {
                        "event": 42,
                        "properties": {"false": False, "zero": 0, "null": None, "string": "", "array": []},
                    }
                    result = await fixture.invoke(str(i), "/capture", args)
                    assert result["completion"] == {"kind": "sdk", "outcome": outcome}
                    assert host.inputs[-1]["invoke"]["args"] == args
                    assert "timestamp" not in host.inputs[-1]["invoke"]["args"]
                    assert contracts.target_result_matches("/capture", outcome) == (i == 0)
                assert fixture.references["native-error"] == "exception"
        assert host.closed == ["f"]
        assert not fixture.references
        assert "/shutdown" not in [r["invoke"]["route"] for r in host.inputs]


@pytest.mark.asyncio
async def test_references_retained_and_data_lookalikes(contracts):
    async with serve(contracts) as (host, url):
        async with Client(url, contracts) as client:
            async with client.fixture("f", "case", PROFILE["id"]) as fixture:
                value = await fixture.reference("undefined", {"kind": "value", "value": {"value": "undefined"}})
                await fixture.invoke("negative", "/capture", {}, references={"/event": value})
                assert host.inputs[-1]["invoke"]["args"] == {}
                host.outcome = {"kind": "value", "value": {"kind": "snapshot", "id": "snapshot"}}
                await fixture.invoke("snapshot", "/evaluate_flags", {})
                assert fixture.references["snapshot"] == "snapshot"
                await fixture.invoke("keys", "/snapshot/keys", {}, receiver={"kind": "snapshot", "id": "snapshot"})
                host.outcome = {"kind": "value", "value": {"kind": "span", "id": "ordinary-json"}}
                await fixture.invoke("json", "/get_property", {"key": "x"})
                assert "ordinary-json" not in fixture.references
                with pytest.raises(BoundaryError):
                    await fixture.invoke("json", "/get_property", {})
            async with client.fixture("next", "case2", PROFILE["id"]) as next_fixture:
                with pytest.raises(BoundaryError):
                    await next_fixture.invoke(
                        "stale", "/snapshot/keys", {}, receiver={"kind": "snapshot", "id": "snapshot"}
                    )
            with pytest.raises(BoundaryError):
                await fixture.invoke("closed", "/flush", {})


@pytest.mark.parametrize(
    "fault,code",
    [
        ("http", "http_error"),
        ("json", "invalid_json"),
        ("large", "body_limit"),
        ("attribution", "invalid_response"),
        ("redirect", "http_error"),
        ("host_timeout", "host_deadline"),
        ("slow", "transport_timeout"),
    ],
)
@pytest.mark.asyncio
async def test_transport_failures_are_not_sdk_throws(contracts, fault, code):
    async with serve(contracts) as (host, url):
        async with Client(url, contracts) as client:
            async with client.fixture("f", "case", PROFILE["id"]) as fixture:
                host.fault = fault
                receipt = await fixture.invoke("failure", "/flush", {"timeout_ms": 0}, timeout_ms=1)
                assert receipt["completion"]["kind"] == "harness"
                assert receipt["completion"]["failure"]["code"] == code
                assert not fixture.active
                assert not fixture.references
            host.fault = None
            async with client.fixture("next", "case2", PROFILE["id"]) as next_fixture:
                receipt = await next_fixture.invoke("after", "/flush", {})
                assert receipt["completion"]["outcome"] == {"kind": "void"}
        assert host.closed == ["f", "next"]


@pytest.mark.asyncio
async def test_unsupported_is_not_excluded(contracts):
    async with serve(contracts) as (host, url):
        host.routes = ["/flush"]
        async with Client(url, contracts) as client:
            async with client.fixture("f", "case", PROFILE["id"]) as fixture:
                receipt = await fixture.invoke("unsupported", "/capture", {"event": "e"})
                assert receipt["completion"]["failure"]["kind"] == "unsupported_binding"
                assert not host.inputs


@pytest.mark.asyncio
async def test_pending_call_cancel_and_duplicate(contracts):
    async with serve(contracts) as (host, url):
        host.fault = "cancel"
        async with Client(url, contracts) as client:
            async with client.fixture("f", "case", PROFILE["id"]) as fixture:
                task = asyncio.create_task(fixture.invoke("pending", "/flush", {}))
                await host.entered.wait()
                with pytest.raises(BoundaryError):
                    await fixture.invoke("second", "/flush", {})
                await fixture.cancel("pending")
                receipt = await task
                assert receipt["completion"]["failure"]["kind"] == "cancelled"
                assert not fixture.references


@pytest.mark.asyncio
async def test_close_failure_prevents_success(contracts):
    async with serve(contracts) as (host, url):
        with pytest.raises(BoundaryError):
            async with Client(url, contracts) as client:
                await client.allocate("f", "case", PROFILE["id"])
                host.fault = "close"
        assert client.errors


@pytest.mark.parametrize("route,signature", [("/with_context", "with_context"), ("/with_span", "with_span")])
@pytest.mark.asyncio
async def test_callback_plan_executes_on_native_owning_context(contracts, route, signature):
    async with serve(contracts) as (host, url):
        async with Client(url, contracts) as client:
            async with client.fixture("f", "case", PROFILE["id"]) as fixture:
                plan = {
                    "signature": signature,
                    "max_invocations": 1,
                    "calls": [
                        {
                            "step_id": "read",
                            "route": "/get_context",
                            "receiver": {"source": "reference", "reference": fixture.receiver},
                            "args": {},
                        }
                    ],
                    "returns": {"source": "literal", "outcome": {"kind": "value", "value": False}},
                }
                ref = await fixture.reference("callback", {"kind": "callback", "plan": plan})
                args = {"context": {"distinct_id": "sync-user"}} if route == "/with_context" else {"name": "span"}
                receipt = await fixture.invoke("owner", route, args, references={"/callback": ref})
                assert receipt["completion"]["outcome"] == {"kind": "value", "value": False}
                observed = await fixture.observe()
                assert observed[0]["receipt"]["completion"]["outcome"]["value"] == host.native_contexts[0]
                assert host.native_contexts == [
                    {"distinct_id": "sync-user" if route == "/with_context" else "async-user"}
                ]
                assert observed[1]["call_ids"] == ["@callback/f/callback/0/read"]
                assert client.calls["@callback/f/callback/0/read"]["parent_call_id"] == "owner"
                assert await fixture.observe() == []
                assert ACTIVE.get() is None


@pytest.mark.asyncio
async def test_context_scope_uses_one_host_request(contracts):
    async with serve(contracts) as (host, url):
        async with Client(url, contracts) as client:
            async with client.fixture("f", "case", PROFILE["id"]) as fixture:
                context = {"kind": "context", "id": "native-context"}
                host.outcome = {"kind": "value", "value": context}
                await fixture.invoke("new", "/new_context", {})
                calls = [
                    {"call_id": identity, "route": "/get_context", "receiver": fixture.receiver, "args": {}}
                    for identity in ("first", "second")
                ]
                response = await fixture.context_scope("scope", context, calls)
                assert response["result"] == {"kind": "completed"}
                assert [call["completion"]["outcome"]["value"] for call in response["calls"]] == [
                    {"distinct_id": "scoped-user"},
                    {"distinct_id": "scoped-user"},
                ]
                assert host.paths.count("fixtures/context-scope") == 1
                assert ACTIVE.get() is None
                with pytest.raises(BoundaryError):
                    await fixture.context_scope("duplicate", context, calls)


@pytest.mark.asyncio
async def test_close_collects_unobserved_callback_failures(contracts):
    async with serve(contracts) as (host, url):
        with pytest.raises(BoundaryError):
            async with Client(url, contracts) as client:
                async with client.fixture("f", "case", PROFILE["id"]) as fixture:
                    plan = {
                        "signature": "with_context",
                        "max_invocations": 1,
                        "calls": [],
                        "returns": {"source": "literal", "outcome": {"kind": "value", "value": False}},
                    }
                    ref = await fixture.reference("cb", {"kind": "callback", "plan": plan})
                    await fixture.invoke("owner", "/with_context", {"context": {}}, references={"/callback": ref})
                    host.events[0]["completion"] = {
                        "kind": "harness",
                        "failure": {
                            "kind": "blocked_fixture",
                            "code": "native_context",
                            "message": "Cannot execute continuation",
                        },
                    }
        assert host.closed == ["f"]
        assert client.errors[0].code == "native_context"
        assert not fixture.references


@pytest.mark.asyncio
async def test_close_cannot_revive_retained_references(contracts):
    async with serve(contracts) as (host, url):
        async with Client(url, contracts) as client:
            async with client.fixture("f", "case", PROFILE["id"]) as fixture:
                host.outcome = {"kind": "value", "value": {"kind": "snapshot", "id": "snapshot"}}
                receipt = await fixture.invoke("snapshot", "/evaluate_flags", {})
                host.events.append({"kind": "call", "sequence": 1, "receipt": receipt})
            assert not fixture.references
            assert fixture.closed
            assert fixture.cursor == 1


def test_parent_cycles_and_unexecuted_calls_fail_reports(contracts):
    value = report()
    second = deepcopy(value["calls"][0])
    second["call_id"] = "second"
    second["parent_call_id"] = "call"
    value["calls"][0]["parent_call_id"] = "second"
    value["calls"].append(second)
    value["results"][0]["result"]["call_ids"].append("second")
    assert strict_exit_code(contracts, value) == 2
    value = report()
    value["results"][0]["result"] = {
        "status": "blocked_fixture",
        "executed": False,
        "failure": {"failed_step": None, "call_ids": ["call"], "code": "fixture", "message": "not executed"},
    }
    assert strict_exit_code(contracts, value) == 2


@pytest.mark.parametrize("decimal", ["abc", "", "+1", "01", "1.2", " 1", "1e3"])
@pytest.mark.asyncio
async def test_malformed_bigint_fixture_is_rejected_before_transport(contracts, decimal):
    async with serve(contracts) as (host, url):
        async with Client(url, contracts) as client:
            async with client.fixture("f", "case", PROFILE["id"]) as fixture:
                with pytest.raises(BoundaryError):
                    await fixture.reference("big", {"kind": "value", "value": {"value": "bigint", "decimal": decimal}})
                assert "fixtures/references" not in host.paths
                ref = await fixture.reference(
                    "big", {"kind": "value", "value": {"value": "bigint", "decimal": "-9007199254740993"}}
                )
                assert ref == {"kind": "value", "id": "big"}


@pytest.mark.asyncio
async def test_false_to_zero_observation_conflict_keeps_original(contracts):
    async with serve(contracts) as (host, url):
        with pytest.raises(BoundaryError):
            async with Client(url, contracts) as client:
                async with client.fixture("f", "case", PROFILE["id"]) as fixture:
                    host.outcome = {"kind": "value", "value": False}
                    receipt = await fixture.invoke("call", "/get_property", {"key": "x"})
                    changed = deepcopy(receipt)
                    changed["completion"]["outcome"]["value"] = 0
                    host.events.append({"kind": "call", "sequence": 1, "receipt": changed})
                    with pytest.raises(BoundaryError, match="conflicting"):
                        await fixture.observe()
        assert client.calls["call"]["completion"]["outcome"]["value"] is False


@pytest.mark.parametrize("conflict", [False, True])
@pytest.mark.asyncio
async def test_observation_before_invocation_reply(contracts, conflict):
    async with serve(contracts) as (host, url):
        host.fault = "early_conflict" if conflict else "early"
        host.outcome = {"kind": "value", "value": False}
        client = Client(url, contracts)
        await client.__aenter__()
        try:
            async with client.fixture("f", "case", PROFILE["id"]) as fixture:
                task = asyncio.create_task(fixture.invoke("call", "/get_property", {"key": "x"}))
                await host.entered.wait()
                await fixture.observe()
                assert fixture.active
                assert client.calls["call"]["completion"]["outcome"]["value"] is False
                host.delay.set()
                receipt = await task
                assert receipt["completion"]["outcome"]["value"] is False
                assert bool(client.errors) is conflict
                assert fixture.active is not conflict
        finally:
            if conflict:
                with pytest.raises(BoundaryError):
                    await client.__aexit__(None, None, None)
            else:
                await client.__aexit__(None, None, None)


@pytest.mark.parametrize("order", ["cancel_ack_first", "cancel_receipt_first"])
@pytest.mark.asyncio
async def test_cancellation_terminal_receipt_and_acknowledgment_orders(contracts, order):
    async with serve(contracts) as (host, url):
        host.fault = order
        async with Client(url, contracts) as client:
            async with client.fixture("f", "case", PROFILE["id"]) as fixture:
                task = asyncio.create_task(fixture.invoke("call", "/flush", {}))
                await host.entered.wait()
                cancellation = asyncio.create_task(fixture.cancel("call"))
                if order == "cancel_ack_first":
                    assert (await cancellation)["state"] == "cancelled"
                    host.delay.set()
                    receipt = await task
                else:
                    receipt = await task
                    host.cancel_ack.set()
                    assert (await cancellation)["state"] == "cancelled"
                assert receipt["completion"]["failure"]["kind"] == "cancelled"
                assert not client.errors
                assert not fixture.references


def report():
    identity = {"case_id": "case", "profile_id": PROFILE["id"], "source": SOURCE}
    return {
        "contract_version": VERSION,
        "catalog_sha256": Contracts(CONTRACT_PATH).catalog_hash,
        "run_id": "run",
        "scope_id": "scope",
        "profiles": [PROFILE],
        "inventory": [{**identity, "selected": True, "applicability": {"kind": "applicable"}}],
        "results": [{**identity, "result": {"status": "passed", "executed": True, "call_ids": ["call"]}}],
        "fixtures": [{"fixture_id": "f", "case_id": "case", "profile_id": PROFILE["id"]}],
        "calls": [
            {
                "fixture_id": "f",
                "call_id": "call",
                "route": "/flush",
                "completion": {"kind": "sdk", "outcome": {"kind": "void"}},
            }
        ],
        "errors": [],
    }


def test_strict_report_gate(contracts):
    valid = report()
    validate_report(contracts, valid)
    assert strict_exit_code(contracts, valid) == 0
    for status in ("failed_assertion", "unsupported_binding", "blocked_fixture", "blocked_contract", "harness_error"):
        failed = report()
        failed["results"][0]["result"] = {
            "status": status,
            "executed": True,
            "failure": {
                "failed_step": {"index": 0, "source": SOURCE},
                "call_ids": ["call"],
                "code": "failure",
                "message": "cause",
            },
        }
        assert strict_exit_code(contracts, failed) == 1
    empty = report()
    for key in ("inventory", "results", "fixtures", "calls"):
        empty[key] = []
    assert strict_exit_code(contracts, empty) == 1
    for code in ("unknown_step", "invalid_selector", "ambiguous_step", "teardown_failed"):
        bad = report()
        bad["errors"] = [{"code": code, "message": "run failure"}]
        assert strict_exit_code(contracts, bad) == 1
    for key in ("inventory", "results", "fixtures", "calls"):
        bad = report()
        bad[key].append(deepcopy(bad[key][0]))
        assert strict_exit_code(contracts, bad) == 2
    bad = report()
    bad["results"] = []
    assert strict_exit_code(contracts, bad) == 2
    bad = report()
    bad["calls"][0]["parent_call_id"] = "call"
    assert strict_exit_code(contracts, bad) == 2
    bad = report()
    bad["calls"][0]["completion"] = {"kind": "harness", "failure": {"kind": "timeout", "code": "t", "message": "t"}}
    assert strict_exit_code(contracts, bad) == 2
    bad = report()
    bad["success"] = True
    assert strict_exit_code(contracts, bad) == 2


def test_only_unselected_or_inapplicable_is_not_success(contracts):
    for status in ("not_selected", "not_applicable"):
        value = report()
        value["calls"], value["fixtures"] = [], []
        entry = value["inventory"][0]
        entry["selected"] = status == "not_applicable"
        entry["applicability"] = {"kind": "not_applicable", "rule": "ui-host", "reason": "UI required"}
        value["results"][0]["result"] = {"status": status, "executed": False, "reason": "selection"}
        if status == "not_applicable":
            value["results"][0]["result"]["applicability_rule"] = "ui-host"
        assert strict_exit_code(contracts, value) == 1
        entry["selected"] = True
        entry["applicability"] = {"kind": "applicable"}
        assert strict_exit_code(contracts, value) == 2
