"""N-13 · reconcile_reconnect — 제어 채널 재접속에서 최신 상태와 필요한 재검증만 복원한다.

docs/small-nodes/N-13.md
- 과거 명령(Overlay·GuideProposal)은 재생하지 않고 최신 목표와 도움 상태만 응답한다.
- 역할 투영은 N-21(project_role_view)을 그대로 써서 재접속에도 같은 필드 권한을 적용한다.
- 부모가 다시 접속하면 must_reobserve=True: 클라이언트는 직전 Overlay·타이머를 지우고
  새 관찰부터 다시 보낸다. 서버는 그 전에 만든 GuideProposal을 재사용하지 않는다.
- 서버 재시작 뒤 복구 가능한 도움(RESUMED)과 존재하지 않는 도움(UNKNOWN)을 구분한다.
- 이 노드는 동의·승인 상태를 쓰지 않는다. 부모가 일시정지했다면 재접속이 켜 주지 않는다.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from .common.enums import HelpState, Role, Source
from .common.result import Basis, ErrorCode, NodeResult, NodeRun, StepStatus
from .common.types import ActorContext
from .ports import HelpStateReader, PairDirectory
from .role_view import RoleViewInput, project_role_view
from .views import RoleSnapshot

NODE_ID = "N-13"
OUTCOME_SNAPSHOT_READY = "snapshot_ready"
OUTCOME_NO_ACTIVE_HELP = "no_active_help"


class ResumeState(StrEnum):
    RESUMED = "RESUMED"  # 클라이언트가 알던 도움이 그대로 활성
    SWITCHED = "SWITCHED"  # 클라이언트의 도움은 없거나 다르고, 다른 활성 도움이 있음 → 로컬 상태 교체
    ENDED = "ENDED"  # 활성 도움 없음. 클라이언트의 도움은 끝난 것으로 기록돼 있음
    UNKNOWN = "UNKNOWN"  # 활성 도움 없음. 클라이언트의 도움을 서버가 모름 (재시작 등)
    NO_HELP = "NO_HELP"  # 클라이언트도 서버도 도움이 없음


@dataclass(frozen=True)
class ReconnectRequest:
    pair_id: str
    help_id: str | None
    role: Role
    last_event_seq: int
    snapshot_request_id: str


@dataclass(frozen=True)
class ReconnectData:
    snapshot: RoleSnapshot | None
    server_event_seq: int
    must_reobserve: bool
    resume_state: ResumeState


def _request_problems(req: object) -> list[str]:
    if not isinstance(req, ReconnectRequest):
        return ["request"]
    bad = []
    if not isinstance(req.pair_id, str) or not req.pair_id:
        bad.append("pair_id")
    if req.help_id is not None and (not isinstance(req.help_id, str) or not req.help_id):
        bad.append("help_id")
    if not isinstance(req.role, Role):
        bad.append("role")
    seq = req.last_event_seq
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
        bad.append("last_event_seq")
    if not isinstance(req.snapshot_request_id, str) or not req.snapshot_request_id:
        bad.append("snapshot_request_id")
    return bad


def reconcile_reconnect(
    req: ReconnectRequest,
    *,
    actor: ActorContext,
    pairs: PairDirectory,
    store: HelpStateReader,
    trace_id: str,
    source: Source = Source.LIVE,
    clock_ns: Callable[[], int] = time.monotonic_ns,
) -> NodeResult[ReconnectData]:
    run = NodeRun(NODE_ID, trace_id, source, clock_ns)

    # 1. 입력 검사
    if _request_problems(req):
        return run.error(ErrorCode.INVALID_INPUT, "validate_input", Basis(pair_id=actor.pair_id))
    run.step("validate_input", StepStatus.SUCCESS)
    scope = Basis(pair_id=req.pair_id, help_id=req.help_id, event_seq=req.last_event_seq)

    # 2. 인증: 토큰의 역할·페어와 요청이 같아야 하고, 페어가 아직 승인돼 있어야 한다.
    if (
        actor.pair_id != req.pair_id
        or actor.role is not req.role
        or pairs.role_of(req.pair_id, actor.user_id) is not req.role
    ):
        return run.error(ErrorCode.UNAUTHORIZED, "authenticate", scope)
    client_snapshot = store.get_snapshot(req.help_id) if req.help_id else None
    client_tombstone = store.get_tombstone(req.help_id) if req.help_id else None
    known = client_snapshot or client_tombstone
    if known is not None and known.pair_id != req.pair_id:
        # 다른 페어의 help_id로 재접속 → 존재 여부도 알리지 않고 거절
        return run.error(ErrorCode.UNAUTHORIZED, "authenticate", scope)
    run.step("authenticate", StepStatus.SUCCESS)

    # 3. 페어의 최신 도움 확인
    latest_id = store.latest_help_id(req.pair_id)
    current = store.get_snapshot(latest_id) if latest_id else None

    if current is None or current.help_state is HelpState.ENDED:
        return _no_active_help(run, req, client_snapshot, client_tombstone)

    resume = ResumeState.RESUMED if req.help_id == current.help_id else ResumeState.SWITCHED
    run.step("resolve_help", StepStatus.SUCCESS, resume.value)
    basis = Basis.from_snapshot(current)

    if resume is ResumeState.RESUMED:
        # 클라이언트가 서버보다 앞선 seq를 봤다면 서버가 이전 상태로 복구된 것이다.
        # 서버 상태가 권위이므로 클라이언트는 더 새 로컬 상태를 버린다.
        ahead = req.last_event_seq > current.event_seq
        run.step("compare_event_seq", StepStatus.SUCCESS, "CLIENT_AHEAD" if ahead else None)
    else:
        run.step("compare_event_seq", StepStatus.SKIPPED, ErrorCode.NOT_APPLICABLE.value)

    # 4. N-21로 역할 투영 (재접속도 같은 필드 권한)
    view = project_role_view(
        RoleViewInput(
            snapshot=current,
            viewer_role=req.role,
            arrival_state=store.arrival_state(current.help_id),
            online=True,  # 지금 새로 인증된 연결로 응답한다
        ),
        actor=actor,
        latest_help_id=latest_id,
        trace_id=trace_id,
        source=source,
        clock_ns=clock_ns,
    )
    if view.data is None:
        err = view.diagnostics.terminal_error
        code = ErrorCode(err.code) if err else ErrorCode.DEPENDENCY_UNAVAILABLE
        return run.error(code, "project_role_view", basis, input_refs=(view.ref,))
    run.step("project_role_view", StepStatus.SUCCESS)

    # 5. 재관찰 결정: 부모만. 일시정지(관찰 중단·미승인)면 켜지 않는다.
    must_reobserve = False
    if req.role is not Role.PARENT:
        run.step("reobserve_decision", StepStatus.SKIPPED, ErrorCode.NOT_APPLICABLE.value)
    elif store.observation_paused(current.help_id):
        run.step("reobserve_decision", StepStatus.SKIPPED, ErrorCode.NO_CONSENT.value)
    else:
        must_reobserve = True
        run.step("reobserve_decision", StepStatus.SUCCESS)

    snapshot = RoleSnapshot(
        role=req.role,
        pair_id=current.pair_id,
        call_id=current.call_id,
        help_id=current.help_id,
        parent_view=view.data.parent_view,
        child_view=view.data.child_view,
    )
    return run.success(
        OUTCOME_SNAPSHOT_READY,
        ReconnectData(
            snapshot=snapshot,
            server_event_seq=current.event_seq,
            must_reobserve=must_reobserve,
            resume_state=resume,
        ),
        basis,
        input_refs=(view.ref,),
    )


def _no_active_help(run, req, client_snapshot, client_tombstone) -> NodeResult[ReconnectData]:
    if req.help_id is None:
        resume = ResumeState.NO_HELP
    elif client_snapshot is not None or client_tombstone is not None:
        resume = ResumeState.ENDED
    else:
        resume = ResumeState.UNKNOWN
    if client_tombstone is not None:
        server_seq = client_tombstone.final_event_seq
    elif client_snapshot is not None:
        server_seq = client_snapshot.event_seq
    else:
        server_seq = 0
    run.step("resolve_help", StepStatus.SUCCESS, resume.value)
    for name in ("compare_event_seq", "project_role_view", "reobserve_decision"):
        run.step(name, StepStatus.SKIPPED, ErrorCode.NOT_APPLICABLE.value)
    return run.success(
        OUTCOME_NO_ACTIVE_HELP,
        ReconnectData(
            snapshot=None, server_event_seq=server_seq, must_reobserve=False, resume_state=resume
        ),
        Basis(pair_id=req.pair_id, help_id=req.help_id, event_seq=server_seq),
    )
