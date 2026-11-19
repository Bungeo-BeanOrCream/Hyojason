"""공유 JSON Schema(이슈 #2)가 코드와 같은지, 실제 결과가 스키마 키와 맞는지 확인한다."""

from __future__ import annotations

import json

from fixtures import CHILD, PARENT, TRACE, snapshot
from hyojason_session.common.enums import ArrivalState
from hyojason_session.common.serialize import to_jsonable
from hyojason_session.export_schemas import SCHEMA_DIR, main, render
from hyojason_session.role_view import RoleViewInput, project_role_view


def test_committed_schema_files_match_code():
    # 다르면: cd server/session && python -m hyojason_session.export_schemas
    assert main(["--check"]) == 0


def test_schemas_are_valid_json_with_required_keys():
    for name, text in render().items():
        schema = json.loads(text)
        assert schema["$schema"].endswith("2020-12/schema"), name
        assert schema["additionalProperties"] is False


def _check_keys(value, schema, defs):
    """필수 키 존재와 선언 밖 키 부재를 재귀 확인하는 최소 검사기 (외부 의존성 없이)."""
    if "$ref" in schema:
        schema = defs[schema["$ref"].rsplit("/", 1)[1]]
    if "anyOf" in schema:
        if value is None:
            return
        schema = next(s for s in schema["anyOf"] if s.get("type") != "null")
        return _check_keys(value, schema, defs)
    if schema.get("type") == "object" and "properties" in schema and value is not None:
        assert set(schema["required"]) <= set(value), set(schema["required"]) - set(value)
        assert set(value) <= set(schema["properties"]), set(value) - set(schema["properties"])
        for k, v in value.items():
            _check_keys(v, schema["properties"][k], defs)


def test_n21_results_conform_to_schema():
    node_schema = json.loads((SCHEMA_DIR / "node-result.schema.json").read_text(encoding="utf-8"))
    data_schema = json.loads(
        (SCHEMA_DIR / "n21-role-view-data.schema.json").read_text(encoding="utf-8")
    )
    for actor in (PARENT, CHILD):
        result = project_role_view(
            RoleViewInput(snapshot(), actor.role, ArrivalState.NONE, True),
            actor=actor, latest_help_id="help-1", trace_id=TRACE,
        )
        payload = to_jsonable(result)
        _check_keys(payload, node_schema, node_schema["$defs"])
        _check_keys(payload["data"], data_schema, data_schema["$defs"])
