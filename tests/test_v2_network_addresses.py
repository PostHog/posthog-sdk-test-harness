"""Host-only network configuration, CLI forwarding, and case URL isolation."""

import asyncio
import socket
from urllib.parse import urlsplit

import aiohttp
import pytest
from click.testing import CliRunner

from posthog_test_harness.v2.cli import main
from posthog_test_harness.v2.client import Client
from posthog_test_harness.v2.contracts import BoundaryError, Contracts
from posthog_test_harness.v2.fixtures import CaseServer
from posthog_test_harness.v2.network import url_host, validate_host
from tests.test_v2_gherkin import cli_run
from tests.v2_ai_host import AIHost
from tests.v2_flush_host import serve


@pytest.mark.parametrize(
    "host", ["127.0.0.1", "0.0.0.0", "localhost", "runner", "runner-1.private", "runner.", "::1", "::", "2001:db8::1"]
)
def test_host_only_addresses(host):
    assert validate_host(host) == host
    assert urlsplit(f"http://{url_host(host)}:1234").hostname == host


INVALID_HOSTS = [
    "",
    "http://runner",
    "https://runner",
    "runner:8080",
    "[::1]",
    "[::1]:8080",
    "user@runner",
    "user:password@runner",
    "runner/path",
    "runner?query",
    "runner#fragment",
    "runner\\path",
    " runner",
    "runner ",
    "runner\n",
    "::1%lo0",
    "runner%2fpath",
    "-runner",
    "runner-",
    "runner..private",
    "runner_1",
    "a" * 64,
    "999.0.0.1",
]


@pytest.mark.parametrize("host", INVALID_HOSTS)
def test_invalid_host_rejected_before_listener_creation(host, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Invalid address reached listener creation")

    monkeypatch.setattr("posthog_test_harness.v2.fixtures.make_server", unexpected)
    for keyword in ("bind_host", "advertised_host"):
        with pytest.raises(ValueError):
            CaseServer(**{keyword: host})


@pytest.mark.parametrize("option", ["--mock-bind-host", "--mock-advertised-host"])
@pytest.mark.parametrize("host", INVALID_HOSTS)
def test_cli_rejects_url_components(option, host, tmp_path):
    result = CliRunner().invoke(
        main,
        [
            "run",
            "--adapter-url",
            "http://unused:1",
            "--profile",
            "unused",
            "--report",
            str(tmp_path / "report.json"),
            option,
            host,
        ],
    )
    assert result.exit_code == 2
    assert option in result.output
    assert not (tmp_path / "report.json").exists()


async def test_case_server_local_defaults():
    server = CaseServer()
    try:
        assert server.server.server_address[0] == "127.0.0.1"
        assert server.url == f"http://127.0.0.1:{server.server.server_port}"
        assert server.server.server_port > 0
    finally:
        await asyncio.to_thread(server.close)


async def test_advertised_ephemeral_urls_and_retired_case_isolation():
    first = CaseServer(advertised_host="localhost")
    second = None
    try:
        first.retire()
        second = CaseServer(advertised_host="localhost")
        assert first.server.server_port != second.server.server_port
        assert first.url == f"http://localhost:{first.server.server_port}"
        assert second.url == f"http://localhost:{second.server.server_port}"
        async with aiohttp.ClientSession() as client:
            async with client.post(first.url + "/capture/", json={"event": "late"}) as response:
                assert response.status == 410
            async with client.post(second.url + "/capture/", json={"event": "current"}) as response:
                assert response.status == 200
        assert first.requests() == []
        assert [r["status"] for r in second.requests()] == [200]
    finally:
        await asyncio.to_thread(first.close)
        if second is not None:
            await asyncio.to_thread(second.close)


async def test_ipv6_literal_listener_and_url():
    probe = socket.socket(socket.AF_INET6)
    try:
        probe.bind(("::1", 0))
    except OSError:
        pytest.skip("IPv6 loopback unavailable")
    finally:
        probe.close()
    server = CaseServer(bind_host="::1", advertised_host="::1")
    try:
        assert server.url == f"http://[::1]:{server.server.server_port}"
        async with aiohttp.ClientSession() as client:
            async with client.post(server.url + "/capture/", json={"event": "ipv6"}) as response:
                assert response.status == 200
    finally:
        await asyncio.to_thread(server.close)


@pytest.mark.parametrize("explicit", [False, True])
async def test_cli_forwards_addresses_through_runner_to_each_case(tmp_path, explicit, specs):
    options = (
        ["--mock-bind-host", "localhost", "--mock-advertised-host", "localhost", "--allow-private-network"]
        if explicit
        else []
    )
    advertised = "localhost" if explicit else "127.0.0.1"
    async with serve(Contracts(), host_type=AIHost) as (host, url):
        code, report, diagnostics, output = await cli_run(
            tmp_path,
            url.replace("127.0.0.1", "localhost") if explicit else url,
            "--feature",
            "migration/yaml-parity-v1/capture-ai.feature",
            "--profile",
            host.profile["id"],
            *options,
            specs=specs,
        )
    assert code == 0, output
    assert diagnostics["network_config"] == {
        "mock_bind_host": advertised,
        "mock_advertised_host": advertised,
        "allow_private_network": explicit,
    }
    urls = [case["mock_url"] for case in diagnostics["cases"]]
    assert len(urls) == len(set(urls)) == 5
    assert all(urlsplit(url).hostname == advertised and urlsplit(url).port > 0 for url in urls)
    assert [r["args"]["config"]["host"] for r in host.inputs if r["route"] == "/setup"] == urls
    assert all(row["result"]["status"] == "passed" for row in report["results"])


@pytest.mark.parametrize(
    "url", ["http://adapter:8080", "http://localhost:8080", "http://10.1.2.3:8080", "http://[::1]:8080"]
)
def test_client_requires_explicit_network_opt_in(url):
    with pytest.raises(BoundaryError) as error:
        Client(url, None)
    assert error.value.code == "invalid_transport"
    assert Client(url, None, allow_private_network=True).base_url == url
    assert Client("http://127.0.0.1:8080/", None).base_url == "http://127.0.0.1:8080"


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:8080",
        "http://127.0.0.1",
        "http://127.0.0.1:0",
        "http://127.0.0.1:65536",
        "http://127.0.0.1:not-a-port",
        "http://user:password@127.0.0.1:8080",
        "http://@127.0.0.1:8080",
        "http://127.0.0.1:8080/path",
        "http://127.0.0.1:8080?query",
        "http://127.0.0.1:8080#fragment",
        "http://127.0.0.1:8080?",
        "http://127.0.0.1:8080#",
        "http://runner%2fpath:8080",
        "http://[::1",
        "http://[::1%lo0]:8080",
        "http://::1:8080",
        "http://runner_1:8080",
        " http://127.0.0.1:8080",
        "http://127.0.0.1:8080\n",
        "http://run\nner:8080",
    ],
)
@pytest.mark.parametrize("opt_in", [False, True])
def test_client_opt_in_preserves_transport_validation(url, opt_in):
    with pytest.raises(BoundaryError) as error:
        Client(url, None, allow_private_network=opt_in)
    assert error.value.code == "invalid_transport"


async def test_cli_default_rejects_non_loopback_adapter(tmp_path):
    (tmp_path / "local.feature").write_text("Feature: Local\n Scenario: One\n  Given unbound step\n")
    async with serve(Contracts(), host_type=AIHost) as (host, url):
        code, report, diagnostics, output = await cli_run(
            tmp_path, url.replace("127.0.0.1", "localhost"), "--feature", "local.feature", specs=tmp_path
        )
    assert code == 1 and not host.fixtures
    assert report["errors"][0]["code"] == "invalid_transport"
    assert diagnostics["network_config"]["allow_private_network"] is False
    assert diagnostics["cases"] == []
