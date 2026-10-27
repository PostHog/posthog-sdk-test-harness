"""Verify GitHub identities before consuming data or updating a bot comment."""

import re
from datetime import datetime

from .compare import compare
from .github import MAX_ARCHIVE_BYTES, GitHub, read_archive
from .render import render
from .validate import InvalidResult, bounded_json, require, validate

SHA = re.compile(r"[0-9a-f]{40}")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
KEY = re.compile(r"[A-Za-z0-9_.-]{1,100}")


class SkipPublication(Exception):
    """Verified metadata cannot establish a safe, current publication target."""


def positive_id(value) -> int:
    require(type(value) is int and 0 < value < 2**63, "Invalid GitHub numeric identity")
    return value


def sha(value) -> str:
    require(isinstance(value, str) and SHA.fullmatch(value), "Invalid GitHub commit identity")
    return value


def timestamp(value) -> datetime:
    require(isinstance(value, str), "Missing GitHub timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        require(result.tzinfo is not None, "Missing timestamp timezone")
        return result
    except ValueError:
        raise InvalidResult("Invalid GitHub timestamp") from None


def same_repository(left: dict, right: dict) -> bool:
    return (
        positive_id(left["id"]) == positive_id(right["id"]) and left["full_name"].lower() == right["full_name"].lower()
    )


class Publisher:
    def __init__(self, api: GitHub, repository: str, workflow_path: str, artifact_name: str, report_key: str, sdk: str):
        require(REPOSITORY.fullmatch(repository), "Invalid caller repository")
        require(re.fullmatch(r"\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml", workflow_path), "Invalid workflow path")
        require(KEY.fullmatch(report_key), "Invalid report key")
        require(0 < len(artifact_name) <= 256 and 0 < len(sdk) <= 256, "Missing artifact name or SDK")
        self.api = api
        self.repository = repository
        self.root = f"/repos/{repository}"
        self.workflow_path = workflow_path
        self.artifact_name = artifact_name
        self.sdk = sdk
        self.marker = f"<!-- sdk-performance:{report_key} -->"
        self.comparison = None
        self.report = None

    def verified_run(self, event: dict) -> dict:
        require(event["action"] == "completed", "Expected a completed workflow_run event")
        original = event["workflow_run"]
        require(event["repository"]["full_name"].lower() == self.repository.lower(), "Event repository differs")
        run_id = positive_id(original["id"])
        run = self.api.api(f"{self.root}/actions/runs/{run_id}")
        require(
            run["id"] == run_id and same_repository(run["repository"], event["repository"]),
            "Run repository or ID differs",
        )
        require(
            run["event"] == "pull_request" and run["status"] == "completed", "Unexpected source run event or status"
        )
        require(sha(run["head_sha"]) == sha(original["head_sha"]), "Source run head differs")
        if positive_id(run["run_attempt"]) != positive_id(original["run_attempt"]):
            raise SkipPublication("Source run attempt has been superseded")
        positive_id(run["run_number"])
        workflow = self.api.api(f"{self.root}/actions/workflows/{positive_id(run['workflow_id'])}")
        require(
            workflow["id"] == run["workflow_id"] and workflow["path"] == self.workflow_path, "Source workflow differs"
        )
        return run

    def matches_pr(self, pr: dict, run: dict) -> bool:
        return (
            pr["state"] == "open"
            and pr["head"]["sha"] == run["head_sha"]
            and pr["head"]["repo"] is not None
            and same_repository(pr["head"]["repo"], run["head_repository"])
            and same_repository(pr["base"]["repo"], run["repository"])
        )

    def associate(self, run: dict) -> tuple[dict, str, bool]:
        associations = run["pull_requests"]
        require(isinstance(associations, list) and len(associations) <= 100, "Invalid PR associations")
        fallback = not associations
        candidates = associations or self.api.pages(f"{self.root}/commits/{sha(run['head_sha'])}/pulls")
        matches = []
        seen = set()
        for candidate in candidates:
            number = positive_id(candidate["number"])
            require(number not in seen, "Duplicate PR association")
            seen.add(number)
            pr = self.api.api(f"{self.root}/pulls/{number}")
            require(pr["number"] == number, "PR number differs")
            if self.matches_pr(pr, run):
                target = sha(pr["base"]["sha"] if fallback else candidate["base"]["sha"])
                if not fallback:
                    require(candidate["head"]["sha"] == run["head_sha"], "Associated PR head differs")
                matches.append((pr, target, fallback))
        if len(matches) != 1:
            raise SkipPublication("No unique currently open PR matches the source head and repository")
        return matches[0]

    def results(self, run: dict, target: str) -> dict:
        artifacts = self.api.pages(f"{self.root}/actions/runs/{run['id']}/artifacts", "artifacts")
        named = [a for a in artifacts if a["name"] == self.artifact_name and not a["expired"]]
        # Artifacts from earlier attempts remain attached to the same run ID.
        started = timestamp(run["run_started_at"])
        current = [a for a in named if timestamp(a["created_at"]) >= started]
        if not current:
            raise SkipPublication("No nonexpired result artifact belongs to this run attempt")
        require(len(current) == 1, "Ambiguous result artifacts")
        artifact = current[0]
        require(
            type(artifact["size_in_bytes"]) is int and 0 < artifact["size_in_bytes"] <= MAX_ARCHIVE_BYTES,
            "Artifact size exceeds limit",
        )
        identity = artifact["workflow_run"]
        require(
            identity["id"] == run["id"]
            and identity["head_sha"] == run["head_sha"]
            and identity["head_repository_id"] == run["head_repository"]["id"],
            "Artifact source identity differs",
        )
        files = read_archive(self.api.download(f"{self.root}/actions/artifacts/{positive_id(artifact['id'])}/zip"))
        revisions = bounded_json(files["revisions.json"], limit=8192)
        require(
            isinstance(revisions, dict) and set(revisions) == {"target", "head", "base"}, "Invalid revisions document"
        )
        for value in revisions.values():
            sha(value)
        if revisions["target"] != target:
            raise SkipPublication("Artifact target revision cannot be verified against trusted PR metadata")
        require(revisions["head"] == run["head_sha"], "Artifact head revision differs")
        merge = self.api.api(f"{self.root}/compare/{target}...{sha(run['head_sha'])}")
        baseline = sha(merge["merge_base_commit"]["sha"])
        require(revisions["base"] == baseline, "Artifact baseline is not the expected merge base")
        base, head = (validate(bounded_json(files[f"{role}.json"])) for role in ("base", "head"))
        for role, doc, commit in (("base", base, baseline), ("head", head, run["head_sha"])):
            require(doc["sdk"] == self.sdk, "Artifact SDK differs")
            require(doc["revision"]["role"] == role and doc["revision"]["commit"] == commit, "Result revision differs")
            require(not doc["revision"].get("dirty", False), "Dirty SDK result cannot be published")
            require(doc["suite"]["source_commit"] == run["head_sha"], "Benchmark source revision differs")
        require(isinstance(run.get("conclusion"), str), "Missing source run conclusion")
        return compare(base, head, source_status=run["conclusion"])

    def upsert(self, event: dict, run: dict, pr: dict, target: str, fallback: bool) -> str:
        comments = self.api.pages(f"{self.root}/issues/{positive_id(pr['number'])}/comments")
        matches = [
            c
            for c in comments
            if c["user"]["login"] == "github-actions[bot]"
            and c["user"]["type"] == "Bot"
            and (c.get("body") or "").splitlines()[:1] == [self.marker]
        ]
        require(len(matches) <= 1, "Multiple reporter bot comments")
        order = (run["run_number"], run["id"], run["run_attempt"])
        if matches:
            lines = matches[0]["body"].splitlines()
            stamp = re.fullmatch(
                r"<!-- sdk-performance-source:([1-9][0-9]*):([1-9][0-9]*):([1-9][0-9]*) -->",
                lines[1] if len(lines) > 1 else "",
            )
            require(stamp is not None, "Existing bot comment lacks a valid source stamp")
            if tuple(int(n) for n in stamp.groups()) >= order:
                raise SkipPublication("An equal or newer source run has already been published")
        current_pr = self.api.api(f"{self.root}/pulls/{pr['number']}")
        if not self.matches_pr(current_pr, run) or (fallback and current_pr["base"]["sha"] != target):
            raise SkipPublication("PR identity changed before publication")
        latest = self.verified_run(event)
        require((latest["run_number"], latest["id"], latest["run_attempt"]) == order, "Source run identity changed")
        body = self.marker + "\n<!-- sdk-performance-source:" + ":".join(str(n) for n in order) + " -->\n" + self.report
        require(len(body.encode("utf-8")) < 60_000, "Comment exceeds size limit")
        if matches:
            self.api.api(f"{self.root}/issues/comments/{positive_id(matches[0]['id'])}", "PATCH", {"body": body})
            return "updated"
        self.api.api(f"{self.root}/issues/{pr['number']}/comments", "POST", {"body": body})
        return "created"

    def publish(self, event: dict) -> tuple[dict, str]:
        try:
            run = self.verified_run(event)
            pr, target, fallback = self.associate(run)
            self.comparison = self.results(run, target)
            self.report = render(self.comparison)
            status = self.upsert(event, run, pr, target, fallback)
            self.comparison["publication"] = {
                "status": status,
                "pull_request": pr["number"],
                "run_id": run["id"],
                "run_attempt": run["run_attempt"],
            }
            return self.comparison, self.report
        except SkipPublication as exc:
            reason = str(exc)  # All skip messages are trusted constants.
            result = self.comparison or {"comparison_version": 1}
            result["publication"] = {"status": "skipped", "reason": reason}
            return result, (self.report or "## SDK performance\n\n") + f"\n**Publication skipped:** {reason}.\n"
        except (KeyError, TypeError, IndexError, AttributeError):
            raise InvalidResult("Malformed GitHub metadata") from None
