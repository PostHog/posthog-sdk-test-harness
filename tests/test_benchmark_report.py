"""Schema, comparison and offline reporting regressions for both producer formats."""

import copy
import json
from pathlib import Path

import pytest
import yaml

from benchmarks.report import cli
from benchmarks.report.compare import compare
from benchmarks.report.render import MAX_REPORT_BYTES, headline_rows, json_text, render
from benchmarks.report.validate import MAX_DOCUMENT_BYTES, InvalidResult, bounded_json, load_result, validate

FIXTURES = Path(__file__).parent / "fixtures" / "benchmarks"


def pair(sdk="node"):
    return [load_result(FIXTURES / f"{sdk}-{role}.json") for role in ("base", "head")]


def metric(doc):
    return doc["benchmarks"][0]["metrics"][0]


@pytest.mark.parametrize("sdk", ["node", "python"])
def test_producer_fixtures_render_with_checkpoints(sdk):
    base, head = pair(sdk)
    result = compare(base, head)
    assert not result["compatibility"]
    assert not result["execution_failed"]
    assert any(w["head"]["checkpoints"] for w in result["workloads"].values())
    row = next(r for r in result["rows"] if r.get("pairs"))
    assert row["pairs"] == 2
    assert row["paired_delta"]["min"] <= row["paired_delta"]["median"] <= row["paired_delta"]["max"]
    report = render(result)
    assert "inconclusive" in report and "Resource limits:" in report
    assert "confidence intervals" in report
    assert head["revision"]["commit"] in report
    assert len(report.encode()) < MAX_REPORT_BYTES


def test_escaped_context_stays_within_report_budget(tmp_path):
    base, head = pair()
    for doc in (base, head):
        for field in ("runtime", "runtime_version", "os", "arch", "cpu", "runner_class"):
            doc["execution"][field] = "." * 256
        doc["execution"]["resource_limits"]["description"] = "." * 4096
        validate(doc)
    paths = []
    for role, doc in (("base", base), ("head", head)):
        path = tmp_path / f"{role}.json"
        path.write_text(json.dumps(doc))
        paths.append(str(path))
    output = tmp_path / "output"
    assert (
        cli.main(["--base-results", paths[0], "--head-results", paths[1], "--output-directory", str(output)], {}) == 0
    )
    report = (output / "report.md").read_text()
    assert len(report.encode()) < MAX_REPORT_BYTES
    assert "flags" in report


def test_unavailable_explanations_are_visible_and_escaped():
    base, head = pair()
    report = render(compare(base, head))
    assert "V8 samples do not expose total allocation count" in report
    head["benchmarks"][0].update(status="unsupported", reason="missing baseline API <script> @all")
    report = render(compare(base, head))
    assert "missing baseline API" in report
    assert "<script>" not in report and "@all" not in report


@pytest.mark.parametrize(
    "body",
    [
        b'{"x":1,"x":2}',
        b'{"x":NaN}',
        b'{"x":Infinity}',
        b'{"x":-Infinity}',
        b"{",
        b"\xff",
        b'{"x":"\\ud800"}',
        b'{"x":' + b"9" * 4000 + b"}",
        b"[" * 1100 + b"]" * 1100,
        b'{"x":"' + b"a" * 4097 + b'"}',
        b'{"stderr":"' + b"a" * 65537 + b'"}',
        b"0" * (MAX_DOCUMENT_BYTES + 1),
    ],
)
def test_bounded_json_rejects_hostile_input(body):
    with pytest.raises(InvalidResult):
        bounded_json(body)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d.update(schema_version=2),
        lambda d: d["revision"].update(commit="a" * 40 + "\n"),
        lambda d: d["suite"].update(source_digest="not-a-digest"),
        lambda d: d["suite"].update(repetitions=101),
        lambda d: d["benchmarks"].append(copy.deepcopy(d["benchmarks"][0])),
        lambda d: d["benchmarks"][0]["metrics"].append(copy.deepcopy(metric(d))),
        lambda d: metric(d)["samples"].append(copy.deepcopy(metric(d)["samples"][0])),
        lambda d: d["benchmarks"][0]["runs"].append(copy.deepcopy(d["benchmarks"][0]["runs"][0])),
        lambda d: metric(d)["samples"][0].update(repetition=99),
        lambda d: d["benchmarks"][0]["runs"][0].update(repetition=99),
        lambda d: metric(d)["samples"][0].update(value=-1),
        lambda d: metric(d)["samples"][0].update(value=float("inf")),
        lambda d: metric(d)["samples"][0].update(operations=0),
        lambda d: d["benchmarks"][0]["runs"][0].update(operations=0),
        lambda d: d["benchmarks"][0]["runs"][0].update(elapsed_ns=-1),
        lambda d: metric(d).update(status="success"),
        lambda d: metric(d).update(quality="precise"),
        lambda d: metric(d).update(unit="bananas"),
        lambda d: metric(d).update(direction="unknown"),
        lambda d: metric(d).update(samples=[]),
        lambda d: d["benchmarks"][0]["runs"][0].update(checkpoints=[{"operations": 1}, {"operations": 1}]),
        lambda d: d["benchmarks"][0]["runs"][0].update(checkpoints=[{"operations": 999999999}]),
        lambda d: d.update(benchmarks=[dict(d["benchmarks"][0], id=str(i)) for i in range(129)]),
        lambda d: metric(d).update(samples=[dict(metric(d)["samples"][0], repetition=i) for i in range(101)]),
    ],
)
def test_schema_semantics_reject_invalid_results(mutation):
    doc = pair()[0]
    mutation(doc)
    with pytest.raises(InvalidResult):
        validate(doc)


@pytest.mark.parametrize(
    "path,value",
    [
        (("sdk",), "other"),
        (("suite", "id"), "other"),
        (("suite", "version"), 2),
        (("suite", "source_commit"), "c" * 40),
        (("suite", "source_digest"), "d" * 64),
        (("suite", "profile"), "smoke"),
        (("suite", "transport"), "network"),
        (("suite", "settling"), "none"),
        (("suite", "allocation_sampling", "samplingInterval"), 1),
        (("execution", "pair_id"), "other"),
        (("execution", "runtime"), "other"),
        (("execution", "runtime_version"), "other"),
        (("execution", "v8_version"), "other"),
        (("execution", "runtime_flags"), []),
        (("execution", "os"), "linux"),
        (("execution", "arch"), "x64"),
        (("execution", "kernel"), "other"),
        (("execution", "cpu"), "other"),
        (("execution", "runner_class"), "other"),
        (("execution", "runner_image"), "other"),
        (("execution", "resource_limits", "worker_timeout_ms"), 1),
        (("execution", "measurement_tools"), {"profiler": "different"}),
    ],
)
def test_global_configuration_mismatches_are_not_compared(path, value):
    base, head = pair()
    target = head
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    result = compare(base, head)
    assert result["compatibility"]
    assert all("change_percent" not in row for row in result["rows"])


@pytest.mark.parametrize(
    "mutation",
    [
        lambda b: b["parameters"].update(concurrency=999),
        lambda b: b.update(fixture_digest="f" * 64),
        lambda b: b["runs"][0].update(warmup_operations=99999),
        lambda b: b["runs"].pop(),
        lambda b: b["metrics"][0].update(unit="bytes"),
        lambda b: b["metrics"][0].update(method="different"),
        lambda b: b["metrics"][0].update(quality="estimated"),
        lambda b: b["metrics"][0].update(direction="higher"),
        lambda b: b["metrics"][0].update(configuration={"sampling": 123}),
        lambda b: b["metrics"][0].update(scope="different"),
        lambda b: b["metrics"][0]["samples"].pop(),
        lambda b: b["metrics"][0]["samples"][0].update(operations=999),
    ],
)
def test_workload_metric_and_sample_mismatches(mutation):
    base, head = pair()
    mutation(head["benchmarks"][0])
    row = next(
        r
        for r in compare(base, head)["rows"]
        if r["workload"] == head["benchmarks"][0]["id"] and r["metric"] == metric(head)["id"]
    )
    assert row["status"] == "not comparable"
    assert "change_percent" not in row


def test_sdk_build_and_dependency_revisions_may_change():
    base, head = pair("python")
    head["execution"].update(
        build_digest="c" * 64,
        dependency_lock_digest="d" * 64,
        sdk_version="next",
        entry="/new/sdk",
        packages={"posthog": "next"},
    )
    assert not compare(base, head)["compatibility"]
    head["execution"]["measurement_tools"]["memray"] = "next"
    assert compare(base, head)["compatibility"]


@pytest.mark.parametrize("status", ["error", "timeout", "oom", "unsupported"])
@pytest.mark.parametrize("level", ["workload", "metric", "run"])
def test_failed_partial_samples_never_become_improvements(status, level):
    base, head = pair()
    workload = head["benchmarks"][0]
    obj = {"workload": workload, "metric": workload["metrics"][0], "run": workload["runs"][0]}[level]
    obj.update(status=status, reason="controlled failure")
    result = compare(base, head)
    affected = next(r for r in result["rows"] if r["metric"] == metric(head)["id"] and r["workload"] == workload["id"])
    assert affected["status"] == "unavailable" and "change_percent" not in affected
    assert result["execution_failed"] == (status != "unsupported")
    assert status in render(result)


def test_missing_workload_metrics_and_zero_baseline():
    base, head = pair()
    removed = base["benchmarks"].pop()
    result = compare(base, head)
    assert all(r["status"] == "no baseline" for r in result["rows"] if r["workload"] == removed["id"])
    base, head = pair()
    head["benchmarks"][0]["metrics"].pop(0)
    assert any(r["status"] == "missing head" for r in compare(base, head)["rows"])
    base, head = pair()
    for s in metric(base)["samples"]:
        s["value"] = 0
    row = next(
        r
        for r in compare(base, head)["rows"]
        if r["metric"] == metric(base)["id"] and r["workload"] == base["benchmarks"][0]["id"]
    )
    assert row["base_median"] == 0 and row["change_percent"] is None
    assert any("Zero baseline" in reason for reason in row["reasons"])


@pytest.mark.parametrize("values", [[10, 10], [1, 1000], [0, 0]])
def test_variability_is_descriptive_even_for_large_shift(values):
    base, head = pair()
    for doc, factor in [(base, 1), (head, 100)]:
        for sample, value in zip(metric(doc)["samples"], values):
            sample["value"] = value * factor
    result = compare(base, head)
    assert all(r["status"] in ("inconclusive", "unavailable") for r in result["rows"])


def test_real_node_headline_covers_timing_allocations_and_memory():
    head = load_result(FIXTURES / "node-headline.json")
    base = copy.deepcopy(head)
    base["revision"].update(role="base", commit="a" * 40)
    result = compare(base, head)
    assert sum(r["status"] == "unavailable" for r in result["rows"]) >= 12
    headline = headline_rows(result)
    identities = {(r["workload"], r["metric"]) for r in headline}
    assert len(headline) == 12
    assert all("base_median" in r for r in headline)
    assert ("flags.single.boolean", "elapsed_time_per_op") in identities
    for size in (10, 100, 1000):
        assert (f"flags.all.{size}", "elapsed_time_per_op") in identities
        assert (f"flags.all.{size}", "allocated_bytes_per_op") in identities
    assert ("flags.load.1000", "observed_workload_peak_rss") in identities
    assert ("flags.refresh.1000", "post_settling_process_rss") in identities
    assert ("capture.enqueue.4096", "elapsed_time_per_op") in identities
    assert ("capture.drain.4096", "observed_workload_peak_rss") in identities
    report = render(result)
    assert "unsupported" not in report.split("<details>")[0]
    assert "unsupported" in report.split("<details>")[1]
    # Execution failures, unlike unsupported instrumentation, take precedence.
    head["benchmarks"][1].update(status="timeout")
    assert headline_rows(compare(base, head))[0]["execution_failed"]


def test_report_bound_includes_escaped_headlines_and_context():
    base, head = pair()
    symbol = "\U0001f6d1"
    for doc in (base, head):
        doc["sdk"] = symbol * 256
        for field in ("runtime", "runtime_version", "os", "arch", "cpu", "runner_class"):
            doc["execution"][field] = symbol * 256
        doc["execution"]["resource_limits"]["description"] = symbol * 4096
        doc["benchmarks"] = [
            dict(copy.deepcopy(doc["benchmarks"][0]), id=symbol * 250 + str(i), status="timeout") for i in range(20)
        ]
        for workload in doc["benchmarks"]:
            for i, m in enumerate(workload["metrics"]):
                m["id"] = symbol * 250 + str(i)
    report = render(compare(base, head))
    assert len(report.encode()) < MAX_REPORT_BYTES


def test_partial_repetitions_are_explicit():
    base, head = pair()
    for d in (base, head):
        metric(d)["samples"].pop()
    assert any("Incomplete repetitions" in r.get("reasons", []) for r in compare(base, head)["rows"])


def test_injection_and_report_truncation():
    base, head = pair()
    attack = "<script>@team [click](https://evil.example) | ![image](x) <!-- sdk-performance:x -->\n"
    for d in (base, head):
        d["sdk"] = attack
        d["execution"]["cpu"] = attack
        metric(d)["method"] = attack
        d["benchmarks"][0]["runs"][0]["stderr"] = "PRIVATE RAW STDERR"
    result = compare(base, head)
    result["rows"] *= 1000
    report = render(result)
    assert len(report.encode()) < MAX_REPORT_BYTES
    for unsafe in (
        "<script>",
        "@team",
        "https://evil.example",
        "![image]",
        "<!-- sdk-performance:x -->",
        "PRIVATE RAW STDERR",
    ):
        assert unsafe not in report
    assert "Omitted or shortened" in report
    text = json_text(result)
    assert "<script>" not in text and json.loads(text) == result


def test_render_only_cli_is_offline_and_writes_outputs(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "GitHub", lambda *a: pytest.fail("Offline rendering constructed a GitHub client"))
    env = {"GITHUB_STEP_SUMMARY": str(tmp_path / "summary"), "GITHUB_OUTPUT": str(tmp_path / "outputs")}
    assert (
        cli.main(
            [
                "--base-results",
                str(FIXTURES / "node-base.json"),
                "--head-results",
                str(FIXTURES / "node-head.json"),
                "--output-directory",
                str(tmp_path / "report"),
            ],
            env,
        )
        == 0
    )
    assert (tmp_path / "report/report.md").read_text() == (tmp_path / "summary").read_text()
    assert "comparison-path=" in (tmp_path / "outputs").read_text()


def test_cli_execution_failure_and_invalid_json_fail_but_write_summary(tmp_path):
    base, head = pair()
    head["benchmarks"][0].update(status="timeout")
    (tmp_path / "base.json").write_text(json.dumps(base))
    (tmp_path / "head.json").write_text(json.dumps(head))
    args = [
        "--base-results",
        str(tmp_path / "base.json"),
        "--head-results",
        str(tmp_path / "head.json"),
        "--output-directory",
        str(tmp_path / "out"),
    ]
    assert cli.main(args, {}) == 1
    assert "execution failed" in (tmp_path / "out/report.md").read_text()
    (tmp_path / "head.json").write_text('{"secret": "DO NOT PRINT",')
    assert cli.main(args, {}) == 1
    assert "Reporting failed" in (tmp_path / "out/report.md").read_text()
    assert "DO NOT PRINT" not in (tmp_path / "out/report.md").read_text()


def test_action_pins_toolchain_and_passes_inputs_as_data():
    action = yaml.safe_load(Path(".github/actions/benchmark-report/action.yml").read_text())
    setup, report = action["runs"]["steps"]
    assert len(setup["uses"].split("@")[1]) == 40
    assert setup["with"] == {"version": "0.13.4", "python-version": "3.12.12", "enable-cache": "false"}
    assert "${{" not in report["run"]
    assert "--frozen --no-dev --no-install-project" in report["run"]
    assert action["inputs"]["mode"]["default"] == "render"
    assert "token" not in action["inputs"]


def test_workload_failure_without_metrics_remains_visible():
    base, head = pair()
    head["benchmarks"][0].update(status="error", metrics=[], runs=[])
    result = compare(base, head)
    assert result["execution_failed"]
    assert result["rows"][0]["execution_failed"]
    assert "head: error" in result["rows"][0]["reasons"]
    assert "change_percent" not in result["rows"][0]


def test_json_structure_and_log_limits():
    for document in ({"x": [0] * 2001}, {str(i): 0 for i in range(1025)}, {"x": "a" * 4097}):
        with pytest.raises(InvalidResult):
            bounded_json(json.dumps(document).encode())
    deep = {}
    for _ in range(21):
        deep = {"x": deep}
    with pytest.raises(InvalidResult):
        bounded_json(json.dumps(deep).encode())
