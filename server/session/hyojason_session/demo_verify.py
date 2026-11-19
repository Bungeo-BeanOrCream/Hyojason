"""Run the B-08 checks on sanitized fixture manifests and evidence files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .common.enums import Source
from .common.result import Basis, ErrorCode, NodeRun, Status, new_id
from .common.serialize import to_jsonable
from .demo_fixtures import load_demo_manifest, prepare_demo_fixtures


def _unique_object(items):
    values = dict(items)
    if len(values) != len(items):
        raise ValueError("Repeated JSON key")
    return values


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object,
                      parse_constant=_invalid_constant)


def _invalid_constant(value: str):
    raise ValueError("Expected a finite JSON value")


def _prepare(path: Path, source: Source, trace_id: str):
    manifest = load_demo_manifest(_read_json(path))
    return prepare_demo_fixtures(
        manifest.device_profiles, manifest.fixture_revision, manifest.scenario_ids,
        manifest.staged_assets, source=source, trace_id=trace_id,
    )


def _read_runs(path: Path):
    from .demo_evaluation import RunEvidence

    data = _read_json(path)
    if not isinstance(data, dict) or set(data) != {"observed_runs"} or not isinstance(data["observed_runs"], list):
        raise ValueError("Expected an observed_runs array")
    runs = []
    for entry in data["observed_runs"]:
        if not isinstance(entry, dict):
            raise ValueError("Expected a run object")
        raw = dict(entry)
        if "basis" in raw:
            basis = raw["basis"]
            if not isinstance(basis, dict):
                raise ValueError("Expected a basis object")
            raw["basis"] = Basis(**basis)
        runs.append(RunEvidence(**raw))
    return tuple(runs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="N-24 AVD and app preflight")
    prepare.add_argument("manifest", type=Path)
    plan = commands.add_parser("plan", help="List the fixed N-25 case inputs")
    plan.add_argument("fixtures", type=Path)
    evaluate = commands.add_parser("evaluate", help="N-25 evidence report")
    evaluate.add_argument("fixtures", type=Path)
    evaluate.add_argument("evidence", type=Path)
    for command in (prepare, plan, evaluate):
        command.add_argument("--source", choices=tuple(Source), type=Source, default=Source.FIXTURE)
    args = parser.parse_args(argv)

    trace_id = new_id("demo")
    try:
        if args.command == "prepare":
            result = _prepare(args.manifest, args.source, trace_id)
        else:
            from .demo_evaluation import evaluate_end_to_end
            from .demo_suite import CRITERIA_REVISION, EXPECTED_MODES, build_demo_suite

            cases = build_demo_suite(
                _read_json(args.fixtures / "instructions.json"),
                _read_json(args.fixtures / "positions.json"),
                _read_json(args.fixtures / "goals.json"),
                cohort=args.source,
            )
            if args.command == "plan":
                _emit({"criteria_revision": CRITERIA_REVISION, "expected_modes": EXPECTED_MODES, "scenarios": cases})
                return 0
            result = evaluate_end_to_end(
                cases, _read_runs(args.evidence), CRITERIA_REVISION, EXPECTED_MODES,
                basis=Basis(), trace_id=trace_id, source=args.source,
            )
    except (OSError, KeyError, TypeError, ValueError):
        node_id = "N-24" if args.command == "prepare" else "N-25"
        result = NodeRun(node_id, trace_id, args.source).error(ErrorCode.INVALID_INPUT, "read_input", Basis())

    _emit(result)
    return 2 if result.status == Status.ERROR else 0


def _emit(value) -> None:
    json.dump(to_jsonable(value), sys.stdout, ensure_ascii=True, indent=2, allow_nan=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    raise SystemExit(main())
