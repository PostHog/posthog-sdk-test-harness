"""Official Gherkin AST/pickle discovery with the frozen phase-1 case identities."""

import hashlib
from dataclasses import dataclass
from pathlib import Path

from gherkin.parser import Parser
from gherkin.pickles.compiler import Compiler

from .contracts import BoundaryError, decode_json, require
from .migration import SUITE, migration_cases, migration_manifest


@dataclass
class Step:
    text: str
    source: dict
    argument: dict
    keyword_type: str


@dataclass
class Case:
    id: str
    source: dict
    name: str
    tags: list[str]
    steps: list[Step]
    migration: dict | None = None


def _walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def compile_feature(text, path, revision):
    """Backgrounds, rules, outlines, substitution and escapes belong to Cucumber."""
    try:
        document = Parser().parse(text)
        document["uri"] = path
        pickles = Compiler().compile(document)
    except Exception as error:
        raise BoundaryError("parse_error", f"Gherkin parsing failed: {path}") from error
    nodes = {node["id"]: node for node in _walk(document) if "id" in node}
    declarations = {node["id"] for node in nodes.values() if "examples" in node}
    seen, cases = set(), []

    def source(line):
        return {"revision": revision, "path": path, "line": line}

    for pickle in pickles:
        declaration_id = pickle["astNodeIds"][0]
        seen.add(declaration_id)
        line = nodes[declaration_id]["location"]["line"]
        case_id = f"gherkin:{revision[:7]}:{path}:L{line}"
        if len(pickle["astNodeIds"]) > 1:
            row = nodes[pickle["astNodeIds"][1]]
            case_id += f":example-L{row['location']['line']}"
        steps = [
            Step(
                step["text"],
                source(nodes[step["astNodeIds"][0]]["location"]["line"]),
                step.get("argument", {}),
                step["type"],
            )
            for step in pickle["steps"]
        ]
        require(bool(steps), "empty_case", f"Scenario has no executable steps: {case_id}")
        cases.append(
            Case(
                case_id, source(pickle["location"]["line"]), pickle["name"], [t["name"] for t in pickle["tags"]], steps
            )
        )
    require(seen == declarations, "empty_outline", f"Scenario declaration has no compiled cases: {path}")
    return cases


def load_cases(specs, paths):
    """Read explicit local spec inputs, checking their frozen manifest digests first."""
    root = Path(specs).resolve()
    try:
        manifest = decode_json((root / "coverage/harness-v2/manifest.json").read_bytes())
        require(len(set(paths)) == len(paths), "invalid_selector", "Duplicate feature selector")
        inputs, cases = [], []
        for path in paths:
            file = (root / path).resolve()
            require(
                not Path(path).is_absolute() and file.is_relative_to(root) and file.suffix == ".feature",
                "invalid_selector",
                "Expected a relative feature path inside the specs directory",
            )
            canonical_path = file.relative_to(root).as_posix()
            migrated = canonical_path.startswith(SUITE + "/")
            selected_manifest = migration_manifest(root) if migrated else manifest
            sources = [s for s in selected_manifest["sources"] if s["path"] == canonical_path]
            require(len(sources) == 1, "unknown_source", f"Feature has no unique frozen source: {canonical_path}")
            source = sources[0]
            text = file.read_bytes()
            require(
                hashlib.sha256(text).hexdigest() == source["sha256"],
                "source_mismatch",
                f"Feature differs from its frozen source: {canonical_path}",
            )
            compiled = compile_feature(text.decode("utf-8"), canonical_path, source["revision"])
            if migrated:
                compiled = migration_cases(root, selected_manifest, compiled)
            cases.extend(compiled)
            inputs.append(source)
            if migrated:
                ledger_source = {"revision": source["revision"], **selected_manifest["ledger"]}
                if ledger_source not in inputs:
                    inputs.append(ledger_source)
        require(len({case.id for case in cases}) == len(cases), "invalid_selector", "Duplicate source case")
        return cases, inputs
    except BoundaryError:
        raise
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise BoundaryError("invalid_source", "Missing or malformed feature/manifest input") from error
