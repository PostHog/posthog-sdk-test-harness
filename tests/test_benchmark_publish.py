"""Trusted publisher identity and stale-result checks; all GitHub calls are mocked."""

import copy
import html
import json

import pytest

from benchmarks.report import cli
from benchmarks.report.publish import Publisher
from benchmarks.report.validate import InvalidResult
from tests.test_benchmark_github import archive
from tests.test_benchmark_report import pair

BASE, HEAD, TARGET = "a" * 40, "b" * 40, "c" * 40
REPO = {"id": 1, "full_name": "PostHog/sdk"}
FORK = {"id": 2, "full_name": "contributor/sdk"}
ROOT = "/repos/PostHog/sdk"
MARKER = "<!-- sdk-performance:node-performance -->"


class FakeGitHub:
    def __init__(self):
        self.run = {
            "id": 10,
            "run_number": 20,
            "run_attempt": 1,
            "head_sha": HEAD,
            "repository": REPO,
            "head_repository": REPO,
            "event": "pull_request",
            "status": "completed",
            "conclusion": "success",
            "workflow_id": 3,
            "run_started_at": "2026-10-27T10:00:00Z",
            "pull_requests": [{"number": 7, "head": {"sha": HEAD}, "base": {"sha": TARGET}}],
        }
        self.event = {"action": "completed", "repository": REPO, "workflow_run": copy.deepcopy(self.run)}
        self.pr = {
            "number": 7,
            "state": "open",
            "head": {"sha": HEAD, "repo": REPO},
            "base": {"sha": TARGET, "repo": REPO},
        }
        self.workflow = {"id": 3, "path": ".github/workflows/benchmarks.yml"}
        self.artifacts = [
            {
                "id": 40,
                "name": "benchmark-results",
                "expired": False,
                "size_in_bytes": 1000,
                "created_at": "2026-10-27T10:01:00Z",
                "workflow_run": {"id": 10, "head_sha": HEAD, "head_repository_id": 1},
            }
        ]
        self.candidates = [{"number": 7}]
        self.comments = []
        self.documents = pair()
        self.revisions = {"target": TARGET, "head": HEAD, "base": BASE}
        self.calls = []
        self.writes = []
        self.run_reads = 0
        self.pr_reads = 0
        self.before_final_pr = None
        self.before_final_run = None
        self.write_error = False

    def api(self, path, method="GET", body=None):
        self.calls.append((path, method))
        if method != "GET":
            if self.write_error:
                raise InvalidResult("GitHub API request failed (HTTP 403)")
            self.writes.append((path, method, body))
            return {"id": 99}
        if path == ROOT + "/actions/runs/10":
            self.run_reads += 1
            if self.run_reads > 1 and self.before_final_run:
                self.before_final_run()
            return copy.deepcopy(self.run)
        if path == ROOT + "/actions/workflows/3":
            return copy.deepcopy(self.workflow)
        if path.startswith(ROOT + "/pulls/"):
            self.pr_reads += 1
            if self.pr_reads > 1 and self.before_final_pr:
                self.before_final_pr()
            pr = copy.deepcopy(self.pr)
            pr["number"] = int(path.rsplit("/", 1)[-1])
            return pr
        if path == ROOT + f"/compare/{TARGET}...{HEAD}":
            return {"merge_base_commit": {"sha": BASE}}
        raise AssertionError(f"Unexpected API request: {path}")

    def pages(self, path, key=None):
        self.calls.append((path, "GET"))
        if path == ROOT + "/actions/runs/10/artifacts":
            assert key == "artifacts"
            return copy.deepcopy(self.artifacts)
        if path == ROOT + f"/commits/{HEAD}/pulls":
            return copy.deepcopy(self.candidates)
        if path == ROOT + "/issues/7/comments":
            return copy.deepcopy(self.comments)
        raise AssertionError(path)

    def download(self, path):
        assert path == ROOT + "/actions/artifacts/40/zip"
        self.calls.append((path, "DOWNLOAD"))
        return archive(
            [(f"{role}.json", json.dumps(doc).encode()) for role, doc in zip(("base", "head"), self.documents)]
            + [("revisions.json", json.dumps(self.revisions).encode())]
        )


def publisher(api):
    return Publisher(
        api, "PostHog/sdk", ".github/workflows/benchmarks.yml", "benchmark-results", "node-performance", "posthog-node"
    )


def bot_comment(number=19, run_id=9, attempt=1, login="github-actions[bot]", kind="Bot", marker=MARKER):
    return {
        "id": 50,
        "user": {"login": login, "type": kind},
        "body": f"{marker}\n<!-- sdk-performance-source:{number}:{run_id}:{attempt} -->\nold report",
    }


def test_same_repository_creates_bot_comment_with_trusted_marker():
    api = FakeGitHub()
    result, report = publisher(api).publish(api.event)
    assert result["publication"]["status"] == "created"
    path, method, body = api.writes[0]
    assert path == ROOT + "/issues/7/comments" and method == "POST"
    assert body["body"].startswith(MARKER + "\n<!-- sdk-performance-source:20:10:1 -->\n")
    assert HEAD in report and BASE in report
    assert api.run_reads == 2 and api.pr_reads == 2


def test_existing_bot_comment_is_updated():
    api = FakeGitHub()
    api.comments = [bot_comment()]
    assert publisher(api).publish(api.event)[0]["publication"]["status"] == "updated"
    assert api.writes[0][:2] == (ROOT + "/issues/comments/50", "PATCH")


@pytest.mark.parametrize(
    "change",
    [
        {"login": "human"},
        {"kind": "User"},
        {"marker": "<!-- sdk-performance:other -->"},
        {"marker": "prefix " + MARKER},
        {"login": "different-bot[bot]"},
    ],
)
def test_only_exact_marker_expected_bot_comments_are_updated(change):
    api = FakeGitHub()
    api.comments = [bot_comment(**change)]
    assert publisher(api).publish(api.event)[0]["publication"]["status"] == "created"
    assert api.writes[0][1] == "POST"


def test_fork_empty_associations_uses_exact_commit_and_repo():
    api = FakeGitHub()
    api.run.update(pull_requests=[], head_repository=FORK)
    api.pr["head"]["repo"] = FORK
    api.artifacts[0]["workflow_run"]["head_repository_id"] = 2
    result, _ = publisher(api).publish(api.event)
    assert result["publication"]["status"] == "created"
    assert (ROOT + f"/commits/{HEAD}/pulls", "GET") in api.calls


@pytest.mark.parametrize(
    "mutation",
    [
        lambda a: a.run.update(pull_requests=[]),
        lambda a: a.pr.update(state="closed"),
        lambda a: a.pr["head"].update(sha="d" * 40),
        lambda a: a.pr["head"].update(repo=FORK),
        lambda a: a.pr["base"].update(repo=FORK),
        lambda a: a.pr["head"].update(repo=None),
    ],
)
def test_no_verified_current_pr_explicitly_skips(mutation):
    api = FakeGitHub()
    api.candidates = []
    mutation(api)
    result, report = publisher(api).publish(api.event)
    assert result["publication"]["status"] == "skipped"
    assert "Publication skipped" in report
    assert not api.writes
    assert not any(method == "DOWNLOAD" for _, method in api.calls)


def test_ambiguous_fork_association_skips():
    api = FakeGitHub()
    api.run["pull_requests"] = []
    api.candidates = [{"number": 7}, {"number": 8}]
    assert publisher(api).publish(api.event)[0]["publication"]["status"] == "skipped"
    assert not api.writes


@pytest.mark.parametrize(
    "mutation",
    [
        lambda a: a.event.update(action="requested"),
        lambda a: a.event.update(repository=FORK),
        lambda a: a.run.update(repository=FORK),
        lambda a: a.run.update(event="push"),
        lambda a: a.run.update(status="in_progress"),
        lambda a: a.run.update(head_sha="d" * 40),
        lambda a: a.run.update(id=11),
        lambda a: a.workflow.update(path=".github/workflows/other.yml"),
        lambda a: a.workflow.update(id=4),
        lambda a: a.run["pull_requests"][0]["head"].update(sha="d" * 40),
        lambda a: a.artifacts[0]["workflow_run"].update(head_sha="d" * 40),
        lambda a: a.artifacts[0]["workflow_run"].update(id=11),
        lambda a: a.artifacts[0]["workflow_run"].update(head_repository_id=2),
        lambda a: a.revisions.update(head="d" * 40),
        lambda a: a.revisions.update(base="d" * 40),
        lambda a: a.documents[0]["revision"].update(commit="d" * 40),
        lambda a: a.documents[1]["revision"].update(commit="d" * 40),
        lambda a: a.documents[1]["revision"].update(dirty=True),
        lambda a: a.documents[0]["suite"].update(source_commit=BASE),
        lambda a: a.documents[1].update(sdk="other-sdk"),
        lambda a: a.revisions.update(pull_request=123),
        lambda a: a.artifacts.append(copy.deepcopy(a.artifacts[0])),
        lambda a: a.artifacts[0].update(size_in_bytes=30 * 1024 * 1024),
    ],
)
def test_mismatched_or_malformed_identities_fail_closed(mutation):
    api = FakeGitHub()
    mutation(api)
    with pytest.raises(InvalidResult):
        publisher(api).publish(api.event)
    assert not api.writes


@pytest.mark.parametrize(
    "mutation",
    [
        lambda a: a.artifacts.clear(),
        lambda a: a.artifacts[0].update(expired=True),
        lambda a: a.artifacts[0].update(created_at="2026-10-26T10:00:00Z"),
        lambda a: a.artifacts[0].update(name="wrong-name"),
        lambda a: a.run.update(run_attempt=2),
        lambda a: a.revisions.update(target="d" * 40),
    ],
)
def test_missing_artifact_or_unverifiable_attempt_target_skips(mutation):
    api = FakeGitHub()
    mutation(api)
    result, report = publisher(api).publish(api.event)
    assert result["publication"]["status"] == "skipped"
    assert "Publication skipped" in report and not api.writes


@pytest.mark.parametrize("number,run_id,attempt", [(21, 11, 1), (20, 10, 2), (20, 10, 1), (20, 11, 1)])
def test_equal_or_newer_source_report_never_overwritten(number, run_id, attempt):
    api = FakeGitHub()
    api.comments = [bot_comment(number, run_id, attempt)]
    result, _ = publisher(api).publish(api.event)
    assert result["publication"]["status"] == "skipped" and not api.writes


@pytest.mark.parametrize("change", ["head", "closed", "attempt", "fork-target"])
def test_current_pr_and_attempt_rechecked_immediately_before_write(change):
    api = FakeGitHub()
    if change == "head":
        api.before_final_pr = lambda: api.pr["head"].update(sha="d" * 40)
    elif change == "closed":
        api.before_final_pr = lambda: api.pr.update(state="closed")
    elif change == "attempt":
        api.before_final_run = lambda: api.run.update(run_attempt=2)
    else:
        api.run["pull_requests"] = []
        api.before_final_pr = lambda: api.pr["base"].update(sha="d" * 40)
    result, report = publisher(api).publish(api.event)
    assert result["publication"]["status"] == "skipped" and not api.writes
    assert "Informational medians" in report


def test_invalid_or_duplicate_bot_markers_fail_closed():
    for comments in [[dict(bot_comment(), body=MARKER + "\nmalformed")], [bot_comment(), bot_comment()]]:
        api = FakeGitHub()
        api.comments = comments
        with pytest.raises(InvalidResult):
            publisher(api).publish(api.event)
        assert not api.writes


def test_api_failure_propagates_not_success():
    api = FakeGitHub()
    api.write_error = True
    with pytest.raises(InvalidResult, match="403"):
        publisher(api).publish(api.event)
    assert not api.writes


def test_publisher_failed_execution_is_still_reported():
    api = FakeGitHub()
    api.documents[1]["benchmarks"][0].update(status="oom")
    result, report = publisher(api).publish(api.event)
    assert result["execution_failed"]
    assert "Benchmark execution failed" in report and api.writes


@pytest.mark.parametrize(
    "conclusion",
    ["failure", "cancelled", "timed_out", "action_required", "neutral", "skipped", "stale", "startup_failure"],
)
def test_failed_source_run_cannot_publish_successful_partial_results(conclusion):
    api = FakeGitHub()
    api.run["conclusion"] = conclusion
    result, report = publisher(api).publish(api.event)
    assert result["execution_failed"]
    assert result["source_execution_status"] == conclusion
    assert all("base_median" not in row and "change_percent" not in row for row in result["rows"])
    assert "Benchmark execution failed" in report and api.writes
    assert f"Source execution: {conclusion}" in html.unescape(api.writes[0][2]["body"]).replace("\u200b", "")


def test_publish_cli_uses_environment_token_and_writes_skip_outputs(tmp_path, monkeypatch):
    api = FakeGitHub()
    api.artifacts.clear()
    tokens = []
    monkeypatch.setattr(cli, "GitHub", lambda token: tokens.append(token) or api)
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(api.event))
    env = {
        "GH_TOKEN": "synthetic-token",
        "GITHUB_EVENT_NAME": "workflow_run",
        "GITHUB_EVENT_PATH": str(event_path),
        "GITHUB_REPOSITORY": "PostHog/sdk",
    }
    args = [
        "--mode",
        "publish",
        "--workflow-path",
        ".github/workflows/benchmarks.yml",
        "--artifact-name",
        "benchmark-results",
        "--report-key",
        "node-performance",
        "--sdk",
        "posthog-node",
        "--output-directory",
        str(tmp_path / "out"),
    ]
    assert cli.main(args, env) == 0
    assert tokens == ["synthetic-token"]
    assert json.loads((tmp_path / "out/comparison.json").read_text())["publication"]["status"] == "skipped"
    env["GITHUB_EVENT_NAME"] = "pull_request_target"
    assert cli.main(args, env) == 1
    assert not api.writes


def test_newer_attempt_updates_older_same_run_report():
    api = FakeGitHub()
    api.run["run_attempt"] = api.event["workflow_run"]["run_attempt"] = 2
    api.comments = [bot_comment(20, 10, 1)]
    result, _ = publisher(api).publish(api.event)
    assert result["publication"]["status"] == "updated"
    assert "sdk-performance-source:20:10:2" in api.writes[0][2]["body"]


def test_original_target_is_used_when_same_repo_base_advances():
    api = FakeGitHub()
    api.pr["base"]["sha"] = "d" * 40
    assert publisher(api).publish(api.event)[0]["publication"]["status"] == "created"
    assert (ROOT + f"/compare/{TARGET}...{HEAD}", "GET") in api.calls


def test_previous_attempt_artifact_is_not_selected():
    api = FakeGitHub()
    api.artifacts.append(dict(copy.deepcopy(api.artifacts[0]), created_at="2026-10-26T10:00:00Z", id=41))
    assert publisher(api).publish(api.event)[0]["publication"]["status"] == "created"
    assert [path for path, method in api.calls if method == "DOWNLOAD"] == [ROOT + "/actions/artifacts/40/zip"]
