"""공유 JSON Schema를 server/session/schemas/에 쓴다.

    cd server/session
    python -m hyojason_session.export_schemas          # 파일 갱신
    python -m hyojason_session.export_schemas --check  # 코드와 파일이 다르면 종료 코드 1
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from .common.decisions import ArrivalDecision
from .common.events import ControlEvent
from .common.result import NodeResult
from .common.schema import json_schema
from .common.types import HelpSnapshot
from .completion import CompletionData
from .purge import PurgeData
from .reconnect import ReconnectData
from .role_view import RoleViewData
from .views import RoleSnapshot

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schemas"

TARGETS: dict[str, tuple[type, str, str]] = {
    "node-result.schema.json": (
        NodeResult,
        "NodeResult",
        "모든 Small Node의 공통 결과 봉투 (docs/node-result-contract.md). data는 노드별 타입.",
    ),
    "help-snapshot.schema.json": (
        HelpSnapshot,
        "HelpSnapshot",
        "N-11 원본 도움 상태. 서버 내부용이며 역할 앱에는 N-21 투영만 보낸다.",
    ),
    "role-snapshot.schema.json": (
        RoleSnapshot,
        "RoleSnapshot",
        "역할 앱이 받는 투영 (N-21 view + 제어 이벤트 봉투용 식별자). 재접속 응답 N-13에 실린다.",
    ),
    "n13-reconnect-data.schema.json": (
        ReconnectData,
        "N-13 ReconnectData",
        "N-13 reconcile_reconnect의 NodeResult.data.",
    ),
    "n21-role-view-data.schema.json": (
        RoleViewData,
        "N-21 RoleViewData",
        "N-21 project_role_view의 NodeResult.data.",
    ),
    "n22-completion-data.schema.json": (
        CompletionData,
        "N-22 CompletionData",
        "N-22 resolve_arrival_or_completion의 NodeResult.data.",
    ),
    "n23-purge-data.schema.json": (
        PurgeData,
        "N-23 PurgeData",
        "N-23 purge_help_and_restart의 NodeResult.data.",
    ),
    "control-event.schema.json": (
        ControlEvent,
        "ControlEvent",
        "제어 이벤트 봉투. help.ended의 payload는 {end_event_id, reason}.",
    ),
    "arrival-decision.schema.json": (
        ArrivalDecision,
        "ArrivalDecision",
        "N-18(C)이 내고 N-22(D)가 검증하는 통화 후 도착 추정. basis에 help·consent·mode·goal·"
        "projection_epoch·screen_revision이 모두 있어야 한다.",
    ),
}


def render() -> dict[str, str]:
    out: dict[str, str] = {}
    for filename, (cls, title, description) in TARGETS.items():
        schema: dict[str, Any] = json_schema(cls, title=title, description=description)
        out[filename] = json.dumps(schema, ensure_ascii=False, indent=2) + "\n"
    return out


def main(argv: list[str]) -> int:
    rendered = render()
    if "--check" in argv:
        stale = [
            name
            for name, text in rendered.items()
            if not (SCHEMA_DIR / name).exists()
            or (SCHEMA_DIR / name).read_text(encoding="utf-8") != text
        ]
        for name in stale:
            print(f"stale: {name}")
        return 1 if stale else 0
    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    for name, text in rendered.items():
        (SCHEMA_DIR / name).write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {SCHEMA_DIR / name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
