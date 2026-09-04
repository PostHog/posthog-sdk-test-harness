import hashlib
import math
import re
import struct
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from tests.test_feature_flag_rules_v2_contract import CONTRACT_ROOT, _load_json, _manifest, _walk_json

SCALE = 0xFFFFFFFFFFFFFFF
MAX_IDENTIFIER_SCALAR_VALUES = 200
CASE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
SAFE_INTEGER = 2**53 - 1


def _corpus_artifacts() -> list[dict[str, Any]]:
    return [artifact for artifact in _manifest()["artifacts"] if artifact["kind"] == "corpus"]


def _corpus(name: str) -> dict[str, Any]:
    return _load_json(CONTRACT_ROOT / "corpus" / name)


def _case_ids(artifact: dict[str, Any], data: dict[str, Any]) -> list[str]:
    if artifact["path"].endswith("hash_sha1_60_v1.json"):
        sections = ("vectors", "threshold_vectors", "variant_vectors", "seed_parity")
        return [entry["id"] for section in sections for entry in data[section]]
    return [case["id"] for case in data["cases"]]


# Independent sha1_60_v1 implementation used only to verify recorded expectations.
def _hash01(text: str) -> float:
    n = int(hashlib.sha1(text.encode("utf-8")).hexdigest()[:15], 16)
    return float(n) / float(SCALE)


def _binary64_hex(value: float) -> str:
    return struct.pack(">d", value).hex()


def _threshold(percentage: float) -> float:
    return percentage / 100


def _boundaries(variants: list[dict[str, Any]]) -> list[float]:
    total = 0.0
    boundaries = []
    for variant in variants:
        total += variant["rollout_percentage"] / 100
        boundaries.append(total)
    return boundaries


def _select_variant(hash_value: float, variants: list[dict[str, Any]]) -> str | None:
    for variant, boundary in zip(variants, _boundaries(variants)):
        if hash_value < boundary:
            return str(variant["key"])
    return None


def _check_rollout(check: dict[str, Any], hash_value: float, percentage_key: str, result_key: str) -> None:
    percentage = check[percentage_key]
    if check.get("short_circuit"):
        assert percentage == 100 and check[result_key] is True and check["threshold"] == 1.0
        return
    assert percentage < 100
    assert check["threshold"] == _threshold(percentage)
    assert check[result_key] == (hash_value <= check["threshold"]), check


def test_manifest_declares_corpus_component_versions() -> None:
    manifest = _manifest()
    corpus_version = manifest["corpus"]["version"]
    assert manifest["corpus"]["config_version_locked"] == 1
    assert manifest["corpus"]["published_versions_are_immutable"] is True

    artifacts = _corpus_artifacts()
    assert [artifact["path"] for artifact in artifacts] == [
        "corpus/hash_sha1_60_v1.json",
        "corpus/v1_evaluation.json",
        "corpus/legacy_projection.json",
    ]
    schema_versions = {a["path"]: a["version"] for a in manifest["artifacts"] if a["kind"] == "schema"}
    for artifact in artifacts:
        assert artifact["version"] == corpus_version
        assert schema_versions[artifact["schema"]] == corpus_version
        data = _load_json(CONTRACT_ROOT / artifact["path"])
        assert data["corpus_version"] == corpus_version
        schema = _load_json(CONTRACT_ROOT / artifact["schema"])
        assert schema["$id"].endswith(":" + corpus_version)


def test_corpus_files_match_their_companion_schemas() -> None:
    manifest = _manifest()
    for artifact in _corpus_artifacts():
        schema = _load_json(CONTRACT_ROOT / artifact["schema"])
        assert schema["$schema"] == manifest["schema_dialect"]
        Draft202012Validator.check_schema(schema)
        for node in _walk_json(schema):
            if isinstance(node, dict) and node.get("type") == "object" and "x-posthog-open-object" not in node:
                assert node.get("additionalProperties") is False, (artifact["schema"], node)
        errors = list(Draft202012Validator(schema).iter_errors(_load_json(CONTRACT_ROOT / artifact["path"])))
        assert not errors, f"{artifact['path']}: {[error.message for error in errors]}"


def test_corpus_case_ids_are_unique_stable_and_declared() -> None:
    manifest = _manifest()
    assert re.compile(manifest["corpus"]["case_id_pattern"]).pattern == CASE_ID_PATTERN.pattern
    seen: list[str] = []
    for artifact in _corpus_artifacts():
        actual = _case_ids(artifact, _load_json(CONTRACT_ROOT / artifact["path"]))
        assert actual == artifact["case_ids"], artifact["path"]
        assert all(CASE_ID_PATTERN.fullmatch(case_id) for case_id in actual)
        seen.extend(actual)
    assert len(seen) == len(set(seen))


def test_corpus_values_are_portable_json() -> None:
    for artifact in _corpus_artifacts():
        for node in _walk_json(_load_json(CONTRACT_ROOT / artifact["path"])):
            if isinstance(node, bool):
                continue
            if isinstance(node, int):
                assert abs(node) <= SAFE_INTEGER, artifact["path"]
            elif isinstance(node, float):
                assert math.isfinite(node), artifact["path"]


def test_hash_vectors_match_an_independent_sha1_60_v1_implementation() -> None:
    corpus = _corpus("hash_sha1_60_v1.json")
    assert corpus["algorithm"] == "sha1_60_v1"
    assert float(SCALE) == 2.0**60

    for vector in corpus["vectors"]:
        raw = vector.get("identifier_before_truncation", vector["identifier"])
        assert vector["identifier"] == raw[:MAX_IDENTIFIER_SCALAR_VALUES]
        assert len(vector["identifier"]) <= MAX_IDENTIFIER_SCALAR_VALUES
        if "identifier_before_truncation" in vector:
            assert vector["identifier_before_truncation_scalar_values"] == len(raw)
            assert vector["identifier_scalar_values"] == len(vector["identifier"])
            assert vector["identifier_utf8_bytes"] == len(vector["identifier"].encode("utf-8"))
            assert vector["identifier_utf16_code_units"] == len(vector["identifier"].encode("utf-16-le")) // 2
        assert vector.get("identifier_empty", False) == (vector["identifier"] == "")

        text = vector["prefix"] + vector["identifier"] + vector["salt"]
        assert vector["input"] == text
        assert vector["input_utf8_hex"] == text.encode("utf-8").hex()
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
        assert vector["sha1_hex"] == digest
        assert vector["first_15_hex"] == digest[:15]
        n = int(digest[:15], 16)
        assert vector["n"] == str(n)
        hash_value = float(n) / float(SCALE)
        assert vector["hash01"] == hash_value
        assert vector["hash01_binary64_hex"] == _binary64_hex(hash_value)
        if "hash01_if_quotient_rounded_once" in vector:
            assert vector["hash01_if_quotient_rounded_once"] != hash_value
            assert vector["hash01_if_quotient_rounded_once"] == n / SCALE
            assert vector["hash01_if_quotient_rounded_once_binary64_hex"] == _binary64_hex(n / SCALE)
        for check in vector.get("rollout_checks", []):
            _check_rollout(check, hash_value, "rollout_percentage", "included")
        for check in vector.get("holdout_checks", []):
            _check_rollout(check, hash_value, "exclusion_percentage", "member")
        if "variant_check" in vector:
            check = vector["variant_check"]
            assert check["cumulative_boundaries"] == _boundaries(check["variants"])
            assert check["selected"] == _select_variant(hash_value, check["variants"])

    for check in corpus["threshold_vectors"]:
        _check_rollout(check, check["hash01"], "rollout_percentage", "included")
        if "parsed_decimal_literal" in check:
            assert check["parsed_decimal_literal"] != check["threshold"]

    for check in corpus["variant_vectors"]:
        assert check["cumulative_boundaries"] == _boundaries(check["variants"])
        assert check["selected"] == _select_variant(check["hash01"], check["variants"])
        if check["selected"] is None:
            assert check["v2_rule"] == "final_variant_fallback"
            assert check["v2_selected"] == check["variants"][-1]["key"]
        if "parsed_decimal_literal_boundary" in check:
            assert check["parsed_decimal_literal_boundary"] not in check["cumulative_boundaries"]

    for parity in corpus["seed_parity"]:
        assert parity["identical_bytes"] is True
        assert parity["identical_outcome"] == ("divergence" not in parity)
        if parity["identifier"] is None:
            continue
        assert parity["v2"]["input"] == parity["v2"]["prefix"] + parity["v2"]["identifier"] + parity["v2"]["salt"]
        assert parity["v1_input"] == parity["v2"]["input"]
        assert parity["hash01"] == _hash01(parity["v1_input"])


def test_v1_evaluation_expectations_are_internally_consistent() -> None:
    corpus = _corpus("v1_evaluation.json")
    reason_codes = set(corpus["reason_codes"])
    for case in corpus["cases"]:
        flags = {flag["key"]: flag for flag in case["definitions"]["flags"]}
        assert len(flags) == len(case["definitions"]["flags"]), case["id"]
        expected = case["expected"]["flags"]
        omitted = case["expected"]["omitted_flags"]
        assert set(expected) | set(omitted) == set(flags), case["id"]
        assert not set(expected) & set(omitted), case["id"]
        assert all(flags[key]["active"] for key in expected), case["id"]
        assert all(not flags[key]["active"] for key in omitted), case["id"]

        for key, outcome in expected.items():
            value = outcome["value"]
            assert outcome["variant"] == (value if isinstance(value, str) else None), case["id"]
            assert outcome["enabled"] == (True if isinstance(value, str) else value), case["id"]
            assert outcome["reason"]["code"] in reason_codes, case["id"]
            index = outcome["reason"]["condition_index"]
            assert index is None or index < len(flags[key]["filters"]["groups"]), case["id"]
            payloads = flags[key]["filters"].get("payloads", {})
            payload_key = value if isinstance(value, str) else "true"
            assert outcome["payload"] == (payloads.get(payload_key) if outcome["enabled"] else None), case["id"]
            for condition in flags[key]["filters"]["groups"]:
                for prop in condition["properties"]:
                    if prop["type"] != "flag":
                        continue
                    assert prop["operator"] == "flag_evaluates_to", case["id"]
                    if prop["dependency_chain"]:
                        assert prop["key"] in flags and prop["key"] in expected, case["id"]
                    else:
                        assert prop["key"] not in flags, case["id"]
                        assert outcome["reason"]["code"] == "missing_dependency", case["id"]

        for evidence in case.get("hash_evidence", []):
            hash_value = _hash01(evidence["input"])
            assert evidence["hash01"] == hash_value, case["id"]
            if "threshold_percentage" in evidence:
                assert evidence["threshold"] == _threshold(evidence["threshold_percentage"]), case["id"]
                assert evidence["included"] == (hash_value <= evidence["threshold"]), case["id"]
            if "cumulative_boundaries" in evidence:
                flag = next(f for f in flags.values() if evidence["input"].startswith(f["key"] + "."))
                variants = flag["filters"]["multivariate"]["variants"]
                assert evidence["cumulative_boundaries"] == _boundaries(variants), case["id"]
                assert evidence["selected"] == _select_variant(hash_value, variants), case["id"]


def test_legacy_projection_rows_follow_the_outcome() -> None:
    corpus = _corpus("legacy_projection.json")
    for case in corpus["cases"]:
        outcome = case["outcome"]
        key = outcome["key"]
        value = outcome["variant"] if outcome["variant"] is not None else outcome["enabled"]
        projections = case["projections"]
        flags_v2, flags_v1 = projections["flags_v2"], projections["flags_v1"]
        assert case["config_version"] == corpus["config_version"] == 1

        if outcome["omitted"]:
            assert flags_v2["flags"] == {} and flags_v1["featureFlags"] == {}, case["id"]
            assert projections["decide_v2"]["featureFlags"] == {} and projections["decide_v1"]["featureFlags"] == []
            continue

        detail = flags_v2["flags"][key]
        assert set(flags_v2["flags"]) == {key} and detail["key"] == key, case["id"]
        assert detail["enabled"] == outcome["enabled"] and detail["variant"] == outcome["variant"], case["id"]
        assert detail["reason"] == outcome["reason"], case["id"]
        expected_metadata = {
            "id": outcome["flag_id"],
            "version": outcome["flag_version"],
            "payload": outcome["payload"],
        }
        assert detail["metadata"] == expected_metadata, case["id"]
        assert detail.get("failed", False) == outcome["failed"], case["id"]
        assert flags_v2["errorsWhileComputingFlags"] == flags_v1["errorsWhileComputingFlags"] == outcome["failed"]
        assert flags_v1["featureFlags"] == {key: value}, case["id"]
        expected_payloads = {key: outcome["payload"]} if outcome["payload"] is not None else {}
        assert flags_v1["featureFlagPayloads"] == expected_payloads, case["id"]
        assert projections["decide_v2"]["featureFlags"] == ({key: value} if outcome["enabled"] else {}), case["id"]
        assert projections["decide_v1"]["featureFlags"] == ([key] if outcome["enabled"] else []), case["id"]
        if outcome["failed"]:
            assert not outcome["enabled"] and outcome["variant"] is None and outcome["payload"] is None


def test_corpus_directory_contains_only_declared_files() -> None:
    declared = {Path(artifact["path"]).name for artifact in _corpus_artifacts()}
    actual = {path.name for path in (CONTRACT_ROOT / "corpus").iterdir()}
    assert actual == declared
