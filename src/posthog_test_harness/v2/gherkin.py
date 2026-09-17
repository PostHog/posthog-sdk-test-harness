"""Official Gherkin AST/pickle discovery directly from feature files."""

import hashlib
from dataclasses import dataclass
from pathlib import Path

from gherkin.parser import Parser
from gherkin.pickles.compiler import Compiler

from .contracts import BoundaryError, require
from .migration import SUITE


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
        tags = [t["name"] for t in pickle["tags"]]
        if len(pickle["astNodeIds"]) > 1:
            examples = next(n for n in nodes.values() if row in n.get("tableBody", []))
            values = dict(
                zip((c["value"] for c in examples["tableHeader"]["cells"]), (c["value"] for c in row["cells"]))
            )
            tags = [tag.replace("<case_id>", values.get("case_id", "<case_id>")) for tag in tags]
        identities = [tag.removeprefix("@case:") for tag in tags if tag.startswith("@case:")]
        require(
            len(identities) <= 1 and all(x and "<" not in x for x in identities),
            "invalid_source",
            "Expected one literal stable case ID",
        )
        if path.startswith(SUITE + "/"):
            require(
                len(identities) == 1 and identities[0].startswith("migration:yaml-parity-v1:"),
                "invalid_source",
                "Migrated scenario requires a stable @case: tag",
            )
        if identities:
            case_id = identities[0]
        metadata = None
        if path.startswith(SUITE + "/"):
            metadata = {"sdk_capabilities": [t.removeprefix("@requires:") for t in tags if t.startswith("@requires:")]}
        cases.append(Case(case_id, source(pickle["location"]["line"]), pickle["name"], tags, steps, metadata))
    require(seen == declarations, "empty_outline", f"Scenario declaration has no compiled cases: {path}")
    return cases


def load_cases(specs, paths):
    """Load explicit features, recording content identities without an external ledger."""
    root = Path(specs).resolve()
    try:
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
            text = file.read_bytes()
            digest = hashlib.sha256(text).hexdigest()
            cases.extend(compile_feature(text.decode("utf-8"), canonical_path, digest))
            inputs.append({"path": canonical_path, "sha256": digest})
        require(len({case.id for case in cases}) == len(cases), "invalid_selector", "Duplicate source case")
        return cases, inputs
    except BoundaryError:
        raise
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise BoundaryError("invalid_source", "Missing or malformed feature input") from error
