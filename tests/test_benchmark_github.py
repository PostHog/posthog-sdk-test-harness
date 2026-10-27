"""Bounded HTTP and ZIP handling without network access."""

import io
import json
import stat
import urllib.error
import zipfile
from http.client import BadStatusLine, IncompleteRead

import pytest

from benchmarks.report import cli
from benchmarks.report.github import MAX_ARCHIVE_BYTES, GitHub, NoRedirect, read_archive
from benchmarks.report.validate import MAX_DOCUMENT_BYTES, InvalidResult


def archive(entries=None, compression=zipfile.ZIP_STORED):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=compression) as z:
        for name, data in entries or [("base.json", b"{}"), ("head.json", b"{}"), ("revisions.json", b"{}")]:
            z.writestr(name, data)
    return stream.getvalue()


def test_archive_reads_exact_files_without_extraction():
    assert read_archive(archive()) == {"base.json": b"{}", "head.json": b"{}", "revisions.json": b"{}"}


@pytest.mark.parametrize(
    "names",
    [
        ["../base.json", "head.json", "revisions.json"],
        ["/base.json", "head.json", "revisions.json"],
        ["base.json", "base.json", "revisions.json"],
        ["base.json", "head.json", "folder/"],
        ["base.json", "head.json", "revisions.json", "extra"],
        ["base.json", "head.json"],
        ["base\\.json", "head.json", "revisions.json"],
    ],
)
def test_archive_rejects_unexpected_paths_and_duplicates(names):
    with pytest.raises(InvalidResult):
        read_archive(archive([(n, b"{}") for n in names]))


@pytest.mark.parametrize("mode", [stat.S_IFLNK, stat.S_IFDIR, stat.S_IFIFO])
def test_archive_rejects_non_regular_files(mode):
    info = zipfile.ZipInfo("base.json")
    info.create_system = 3
    info.external_attr = (mode | 0o644) << 16
    with pytest.raises(InvalidResult):
        read_archive(archive([(info, b"{}"), ("head.json", b"{}"), ("revisions.json", b"{}")]))


@pytest.mark.parametrize("data", [b"not zip", b"x" * (MAX_ARCHIVE_BYTES + 1), archive()[:-30]])
def test_archive_rejects_invalid_or_oversized_compressed_input(data):
    with pytest.raises(InvalidResult):
        read_archive(data)


def test_archive_rejects_zip_bombs_and_large_members():
    for size, compression in [(100_000, zipfile.ZIP_DEFLATED), (MAX_DOCUMENT_BYTES + 1, zipfile.ZIP_STORED)]:
        with pytest.raises(InvalidResult):
            read_archive(
                archive([("base.json", b"x" * size), ("head.json", b"{}"), ("revisions.json", b"{}")], compression)
            )
    with pytest.raises(InvalidResult):
        read_archive(archive([("base.json", b"{}"), ("head.json", b"{}"), ("revisions.json", b"x" * 8193)]))


class Response(io.BytesIO):
    def __init__(self, code=200, body=b"{}", location=None):
        super().__init__(body)
        self.code = code
        self.headers = {"Location": location}


class Opener:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def client(responses):
    api = GitHub("synthetic-token")
    api.opener = Opener(responses)
    return api


def test_signed_download_never_receives_authorization():
    api = client([Response(302, location="https://signed.example/file?signature=example"), Response(body=b"archive")])
    assert api.download("/repos/PostHog/sdk/actions/artifacts/1/zip") == b"archive"
    first, second = [req for req, _ in api.opener.calls]
    assert first.get_header("Authorization") == "Bearer synthetic-token"
    assert second.get_header("Authorization") is None
    assert all(0 < timeout <= 20 for _, timeout in api.opener.calls)
    assert NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.example") is None


@pytest.mark.parametrize(
    "url",
    ["http://signed.example/file", "file:///tmp/file", "https://user:pass@host/file", "https://host/file#fragment"],
)
def test_download_rejects_unsafe_signed_urls(url):
    api = client([Response(302, location=url)])
    with pytest.raises(InvalidResult):
        api.download("/repos/PostHog/sdk/actions/artifacts/1/zip")
    assert len(api.opener.calls) == 1


def test_credentials_are_bound_to_fixed_api_origin():
    api = client([])
    for url in ("https://evil.example", "https://api.github.com.evil.example", "https://api.github.com:444"):
        with pytest.raises(InvalidResult):
            api._request(url, authenticated=True)
    assert not api.opener.calls


def test_redirect_and_response_limits():
    api = client(
        [Response(302, location="https://signed.example/first")] + [Response(302, location="/next") for _ in range(3)]
    )
    with pytest.raises(InvalidResult, match="redirect limit"):
        api.download("/repos/PostHog/sdk/actions/artifacts/1/zip")
    api = client([Response(body=b"x" * 11)])
    with pytest.raises(InvalidResult, match="exceeds limit"):
        api._request("https://api.github.com/repos/P/r", authenticated=True, limit=10)


@pytest.mark.parametrize(
    "response",
    [
        Response(403, body=b"SECRET"),
        OSError("SECRET"),
        urllib.error.HTTPError("url", 500, "SECRET", {}, io.BytesIO(b"SECRET")),
    ],
)
def test_api_errors_do_not_disclose_response_or_token(response):
    api = client([response])
    with pytest.raises(InvalidResult) as error:
        api.api("/repos/PostHog/sdk")
    assert "SECRET" not in str(error.value) and "synthetic-token" not in str(error.value)


def test_compare_api_accepts_large_patches_without_relaxing_other_endpoints():
    payload = json.dumps(
        {"merge_base_commit": {"sha": "a" * 40}, "files": [{"patch": "+changed line\\n" * 9000}]}
    ).encode()
    path = "/repos/PostHog/sdk/compare/" + "a" * 40 + "..." + "b" * 40
    assert client([Response(body=payload)]).api(path)["merge_base_commit"]["sha"] == "a" * 40
    with pytest.raises(InvalidResult, match="Text exceeds limit"):
        client([Response(body=payload)]).api("/repos/PostHog/sdk/pulls/1")
    with pytest.raises(InvalidResult, match="response exceeds limit"):
        client([Response(body=b"x" * (MAX_DOCUMENT_BYTES + 1))]).api(path)


@pytest.mark.parametrize("stage", ["open", "read"])
def test_protocol_errors_write_safe_cli_failure_artifacts(stage, tmp_path, monkeypatch):
    class TruncatedResponse(Response):
        def read(self, limit):
            raise IncompleteRead(b"PRIVATE_SERVER_DATA")

    api = client([BadStatusLine("PRIVATE_SERVER_DATA") if stage == "open" else TruncatedResponse()])
    monkeypatch.setattr(cli, "GitHub", lambda token: api)
    event = tmp_path / "event.json"
    event.write_text(
        json.dumps({"action": "completed", "repository": {"full_name": "PostHog/sdk"}, "workflow_run": {"id": 1}})
    )
    out = tmp_path / "out"
    summary = tmp_path / "summary.md"
    args = [
        "--mode",
        "publish",
        "--workflow-path",
        ".github/workflows/benchmarks.yml",
        "--artifact-name",
        "benchmark-results",
        "--report-key",
        "node",
        "--sdk",
        "posthog-node",
        "--output-directory",
        str(out),
    ]
    env = {
        "GITHUB_EVENT_NAME": "workflow_run",
        "GITHUB_EVENT_PATH": str(event),
        "GITHUB_REPOSITORY": "PostHog/sdk",
        "GH_TOKEN": "synthetic-token",
        "GITHUB_STEP_SUMMARY": str(summary),
    }
    assert cli.main(args, env) == 1
    for path in (out / "report.md", out / "comparison.json", summary):
        assert path.exists()
        assert "PRIVATE_SERVER_DATA" not in path.read_text()
        assert "synthetic-token" not in path.read_text()
    assert "Reporting failed" in summary.read_text()


def test_request_and_pagination_budgets():
    api = client([])
    api.requests = 100
    with pytest.raises(InvalidResult, match="budget"):
        api.api("/repos/PostHog/sdk")
    api = client([Response(body=b"[" + b",".join([b"{}"] * 100) + b"]") for _ in range(10)])
    with pytest.raises(InvalidResult, match="pagination"):
        api.pages("/repos/PostHog/sdk/pulls")
    assert len(api.opener.calls) == 10
