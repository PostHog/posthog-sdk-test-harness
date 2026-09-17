"""Controlled analytics-v1 engine with actual admission, storage and HTTP delivery."""

import asyncio
import gzip
import json
import subprocess
import zlib
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import aiohttp

from tests.v2_ai_host import AIEngine, AIHost


class AnalyticsWireEngine(AIEngine):
    retryable_statuses = {408, 500, 503, 504}

    def __init__(self, storage, host, config, token, protocol, defect):
        super().__init__(storage, host, config, protocol, defect)
        self.compression = config.get("compression", "none")
        self.disable_geoip = config.get("disable_geoip")
        self.token = token
        self.max_retries = config.get("max_retries", 3)
        self.retry_delay_ms = config.get("retry_delay_ms", 3000)
        self.max_retry_delay_ms = config.get("max_retry_delay_ms", 30000)
        self.request_id = None
        self.attempt = 1
        self.response_headers = {}
        self.response_body = None
        self.historical_migration = config.get("historical_migration", False)

    def enrich_event(self, event, args):
        if "options" in args and self.defect != "omit_options":
            target = event["properties"] if self.defect == "options_in_properties" else event
            target["options"] = deepcopy(args["options"])
            if self.defect == "drop_false_options":
                target["options"] = {k: v for k, v in target["options"].items() if v}
        if self.disable_geoip is not None and self.defect != "omit_geoip":
            event["properties"]["$geoip_disable"] = self.disable_geoip
        return event

    def encode_body(self, payload, headers):
        if self.compression == "none" or self.defect == "omit_compression":
            return {"json": payload}
        raw = json.dumps(payload).encode("utf-8")
        if self.compression == "gzip":
            encoded = gzip.compress(raw)
        elif self.compression == "deflate":
            encoded = zlib.compress(raw)
        else:
            command = {"br": ["brotli", "-c"], "zstd": ["zstd", "-q", "-c"]}[self.compression]
            encoded = subprocess.run(command, input=raw, capture_output=True, check=True, timeout=10).stdout
        headers["Content-Encoding"] = self.compression
        if self.defect == "invalid_compressed_body":
            encoded = b"invalid-compressed-body"
        if self.defect == "empty_compressed_events":
            encoded = gzip.compress(b'{"batch":[]}')
        return {"data": encoded}

    async def flush(self) -> None:
        records = list(self.records)
        if not records:
            if self.defect == "empty_network":
                await self.post("/i/v1/analytics/events", {"batch": []})
            return
        path = records[0]["path"]
        self.request_id = str(uuid4()) if self.defect != "reuse_request_id" else "fixed-request-id"
        first_timestamp = datetime.now(timezone.utc).isoformat()
        budget = self.max_retries + (1 if self.defect == "exceed_retry_budget" else 0)
        for retry in range(budget + 1):
            self.attempt = retry + 1
            self.request_timestamp = first_timestamp if self.defect == "frozen_request_timestamp" else None
            if retry and self.defect == "replace_request_id":
                self.request_id = str(uuid4())
            events = deepcopy([r["event"] for r in records])
            if retry and self.defect in ("retry_uuid", "retry_timestamp"):
                field = self.defect.removeprefix("retry_")
                events[0][field] = str(uuid4()) if field == "uuid" else datetime.now(timezone.utc).isoformat()
            status = await self.post(records[0]["path"], {"batch": events})
            retryable = status in self.retryable_statuses
            if status == 200 and isinstance(self.response_body, dict) and "results" in self.response_body:
                results = self.response_body["results"]
                retained = [r for r in records if results.get(r["event"].get("uuid"), {}).get("result") == "retry"]
                if self.defect == "retain_partial_terminals" and retained:
                    retained = records
                if self.defect == "retry_terminal_results":
                    retained = [r for r in records if results.get(r["event"].get("uuid"), {}).get("result") != "ok"]
                if self.defect == "discard_partial_retry":
                    retained = []
                removed = {id(r) for r in records} - {id(r) for r in retained}
                self.records[:] = [r for r in self.records if id(r) not in removed]
                records = retained
                retryable = bool(records)
            if self.defect == "retry_terminal":
                retryable = status >= 400
            if self.defect == "no_retry":
                retryable = False
            if not retryable:
                removed = {id(r) for r in records}
                self.records[:] = [r for r in self.records if id(r) not in removed]
                break
            if retry == budget:
                break
            delay = min(self.retry_delay_ms * 2**retry, self.max_retry_delay_ms) / 1000
            # The server's seconds-valued instruction takes precedence over local backoff.
            if "Retry-After" in self.response_headers and self.defect != "ignore_retry_after":
                delay = float(self.response_headers["Retry-After"])
            if self.defect in ("no_backoff", "ignore_retry_after"):
                delay = 0
            await asyncio.sleep(delay)
        if self.defect == "duplicate_request":
            await self.post(path, {"batch": events})

    async def post(self, path, payload):
        payload = deepcopy(payload)
        now = datetime.now(timezone.utc).isoformat()
        headers = {
            "Authorization": "Bearer " + self.token,
            "Content-Type": "application/json",
            "PostHog-Sdk-Info": "controlled-wire/1",
            "PostHog-Attempt": str(self.attempt if self.defect != "frozen_attempt" else 1),
            "PostHog-Request-Id": self.request_id or str(uuid4()),
            "PostHog-Request-Timestamp": getattr(self, "request_timestamp", None) or now,
            "User-Agent": "controlled-wire/1",
        }
        if self.protocol == "analytics_v1":
            payload["created_at"] = now
            if self.historical_migration:
                payload["historical_migration"] = True
        else:
            payload["api_key"] = self.token
        defect = self.defect or ""
        if defect.startswith("missing_header:"):
            del headers[defect.split(":", 1)[1]]
        if defect.startswith("header:"):
            _, name, value = defect.split(":", 2)
            headers[name] = value
        if defect == "trailing_slash":
            path += "/"
        if defect.startswith("path:"):
            path = defect.split(":", 1)[1]
        if defect.startswith("omit_body:"):
            payload.pop(defect.split(":", 1)[1], None)
        if defect.startswith("root:"):
            payload[defect.split(":", 1)[1]] = None
        if defect == "offset_created_at":
            payload["created_at"] = "2025-01-02T08:34:05+05:30"
        if defect.startswith("created_at_delta:"):
            payload["created_at"] = (
                datetime.now(timezone.utc) + timedelta(seconds=float(defect.split(":", 1)[1]))
            ).isoformat()
        if defect == "empty_batch":
            payload["batch"] = []
        if defect == "object_batch":
            payload["batch"] = {}
        events = payload.get("batch", [])
        if events:
            if defect.startswith("timestamp:"):
                events[0]["timestamp"] = defect.split(":", 1)[1]
            if defect == "empty_identity":
                events[0]["distinct_id"] = ""
            if defect.startswith("omit_event:"):
                events[0].pop(defect.split(":", 1)[1], None)
            if defect == "numeric_identity":
                events[0]["distinct_id"] = 123
            if defect == "property_identity":
                events[0]["properties"]["distinct_id"] = events[0]["distinct_id"]
            if defect == "null_properties":
                events[0]["properties"] = None
            if defect == "array_properties":
                events[0]["properties"] = []
            if defect == "null_identity":
                events[0]["distinct_id"] = None
            if defect == "null_required_fields":
                for key in ("event", "uuid", "timestamp", "distinct_id"):
                    events[0][key] = None
            if defect == "second_event_missing_root":
                events.append({"event": "second"})
            if defect == "second_event_timestamp":
                events.append({**events[0], "timestamp": "2025-01-02T03:04:06Z"})
            if defect == "second_event_offset":
                events.append({**events[0], "timestamp": "2025-01-02T08:34:05+05:30"})
            if defect == "second_event_invalid_uuid":
                events.append({**events[0], "uuid": "invalid"})
            if defect == "second_event_numeric_identity":
                events.append({**events[0], "distinct_id": 123})
            if defect == "nested_token":
                events[0]["properties"]["token"] = "ordinary-property"
            if defect == "short_batch":
                del events[-1]
            if defect == "duplicate_uuids":
                for event in events:
                    event["uuid"] = events[0]["uuid"]
            if defect == "append_duplicate_uuid":
                events.append(deepcopy(events[0]))
            if defect.startswith("omit_index_field:"):
                _, index, field = defect.split(":")
                events[int(index)].pop(field, None)
            if defect.startswith("property_value:"):
                _, index, name, value = defect.split(":", 3)
                events[int(index)]["properties"][name] = json.loads(value)
            if defect.startswith("missing_property:"):
                events[0]["properties"].pop(defect.split(":", 1)[1], None)
            if defect.startswith("default_option:"):
                events[0]["options"] = {defect.split(":", 1)[1]: False}
        path, payload, headers = self.encode_request(path, payload, headers)
        async with aiohttp.ClientSession(trust_env=False, skip_auto_headers={"User-Agent", "Content-Type"}) as session:
            data = self.encode_body(payload, headers)
            if defect in ("empty_body", "non_json_body"):
                data = {"data": b"" if defect == "empty_body" else b"not-json"}
            async with session.post(self.host + path, headers=headers, **data) as response:
                raw = await response.read()
                try:
                    self.response_body = json.loads(raw)
                except (ValueError, UnicodeError):
                    self.response_body = None
                status = response.status
                self.response_headers = dict(response.headers)
            if defect == "second_request_bad_header":
                async with session.post(self.host + path, json=payload, headers={"Authorization": "wrong"}) as response:
                    await response.read()
            return status

    def encode_request(self, path, payload, headers):
        return path, payload, headers


class AnalyticsWireHost(AIHost):
    def __init__(self, contracts, *, sdk_capabilities=("capture_v1",), protocol="analytics_v1", **options):
        super().__init__(contracts, sdk_capabilities=sdk_capabilities, protocol=protocol, **options)
        self.profile["id"] = "controlled-analytics-wire-v1"
        self.profile["module"]["entry"] = "tests.v2_analytics_wire_host.AnalyticsWireEngine"
        self.profile["products"] = ["analytics"]
        self.routes = [r for r in self.routes if r != "/capture_ai"]

    async def invoke(self, fixture, call):
        if call["route"] == "/setup":
            args = call["args"]
            assert fixture.storage_prepared and fixture.engine is None
            fixture.engine = AnalyticsWireEngine(
                fixture.storage,
                args["config"]["host"],
                args["config"],
                args["project_token"],
                self.profile["protocol"],
                fixture.defect,
            )
            return await fixture.engine.setup()
        return await super().invoke(fixture, call)
