"""N-23 · purge_help_and_restart — 끝난 도움의 관찰과 데이터를 지우고 새 도움은 빈 상태에서 연다.

docs/small-nodes/N-23.md
- 결과: success/help_ended, success/already_ended, partial/local_cleanup_pending, error/null
- 서버: 도움 상태·이력·추론 결과·대기 발화·도착 알림을 비우고, 늦은 응답을 거를 최소 기록만 남긴다.
- 단말: help.ended(end_event_id, reason)를 양쪽에 보내 부모 캡처·좌표·타이머·테두리·띠와
  자녀 완료 버튼·도착 알림을 정리하게 한다. 못 보낸 쪽은 재접속(N-13)에서 정리된다.
- 같은 도움은 한 번만 종료한다(도움별 잠금으로 동시 실행 금지). 두 번째부터는 already_ended.
- 부모 중단이거나 동의가 없으면 다음 도움은 부모 재활성 동의 뒤에만 연다.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from .common.enums import ActorRole, CleanupScope, EndReason, Role, Source
from .common.events import EVENT_VERSION, ControlEvent
from .common.result import Basis, ErrorCode, NodeResult, NodeRun, ResultRef, StepStatus, new_id
from .common.types import HelpSnapshot, snapshot_problems
from .ports import EventSink, HelpScopedStore, HelpTombstone

NODE_ID = "N-23"
OUTCOME_HELP_ENDED = "help_ended"
OUTCOME_ALREADY_ENDED = "already_ended"
OUTCOME_LOCAL_CLEANUP_PENDING = "local_cleanup_pending"
HELP_ENDED_EVENT = "help.ended"

# 종료 때 반드시 비워야 하는 서버 범위
SERVER_SCOPES: tuple[CleanupScope, ...] = (
    CleanupScope.HELP_STATE,
    CleanupScope.NAVIGATION_TRAIL,
    CleanupScope.INFERENCE_RESULTS,
    CleanupScope.PENDING_UTTERANCES,
    CleanupScope.ARRIVAL_NOTICE,
)
_DEVICE_SCOPE = {Role.PARENT: CleanupScope.PARENT_DEVICE, Role.CHILD: CleanupScope.CHILD_DEVICE}

# 종료 사유 ↔ 요청할 수 있는 역할 (서로 바꿔 처리하지 않는다)
_REASON_ROLES: dict[EndReason, frozenset[ActorRole]] = {
    EndReason.CHILD_COMPLETED: frozenset({ActorRole.CHILD}),
    EndReason.PARENT_STOPPED: frozenset({ActorRole.PARENT}),
    EndReason.NO_GOAL_AT_HANGUP: frozenset({ActorRole.AGENT}),
    # N-11의 consent.updated(enabled=false)는 부모·자녀 어느 쪽이든 보낼 수 있다
    EndReason.CONSENT_REVOKED: frozenset({ActorRole.PARENT, ActorRole.CHILD}),
}
# 다음 도움 전에 부모 재활성 동의가 필요한 종료 사유
_CONSENT_GATED = frozenset({EndReason.PARENT_STOPPED, EndReason.CONSENT_REVOKED})


class TombstoneRegistry:
    """N-23의 종료 기록. 늦게 온 결과·명령을 거르고 다음 도움 동의 조건을 알려 준다."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._by_help: dict[str, HelpTombstone] = {}
        self._latest_by_pair: dict[str, HelpTombstone] = {}
        self._help_locks: dict[str, threading.Lock] = {}

    def get(self, help_id: str) -> HelpTombstone | None:
        with self._guard:
            return self._by_help.get(help_id)

    def latest_for_pair(self, pair_id: str) -> HelpTombstone | None:
        """N-11이 새 도움을 열기 전에 resume_requires_consent를 확인할 때 쓴다."""
        with self._guard:
            return self._latest_by_pair.get(pair_id)

    def help_lock(self, help_id: str) -> threading.Lock:
        with self._guard:
            return self._help_locks.setdefault(help_id, threading.Lock())

    def record(self, tombstone: HelpTombstone) -> None:
        with self._guard:
            self._by_help[tombstone.help_id] = tombstone
            self._latest_by_pair[tombstone.pair_id] = tombstone


@dataclass(frozen=True)
class PurgeInput:
    help_id: str
    end_reason: EndReason
    rtc_active: bool  # 음성 통화가 아직 이어지는가
    role: ActorRole  # 종료를 요청한 쪽
    consent_active: bool  # 부모 분석 동의가 아직 유효한가


@dataclass(frozen=True)
class PurgeData:
    end_event_id: str
    end_reason: EndReason
    cleared_scopes: tuple[CleanupScope, ...]
    resume_requires_consent: bool


def _input_problems(inp: object, pair_id: object, current: object, stores: object) -> list[str]:
    if not isinstance(inp, PurgeInput):
        return ["input"]
    bad = []
    if not isinstance(inp.help_id, str) or not inp.help_id:
        bad.append("help_id")
    if not isinstance(inp.end_reason, EndReason):
        bad.append("end_reason")
    if not isinstance(inp.role, ActorRole):
        bad.append("role")
    for name in ("rtc_active", "consent_active"):
        if not isinstance(getattr(inp, name), bool):
            bad.append(name)
    if not isinstance(pair_id, str) or not pair_id:
        bad.append("pair_id")
    if current is not None and snapshot_problems(current):
        bad.append("current")
    covered = {getattr(s, "scope", None) for s in stores}
    if not set(SERVER_SCOPES) <= covered:
        bad.append("stores")  # 비워야 할 서버 범위가 연결되지 않았다
    return bad


def purge_help_and_restart(
    inp: PurgeInput,
    *,
    pair_id: str,
    current: HelpSnapshot | None,
    stores: Sequence[HelpScopedStore],
    tombstones: TombstoneRegistry,
    events: EventSink,
    trace_id: str,
    source: Source = Source.LIVE,
    input_refs: Iterable[ResultRef] = (),
    clock_ns: Callable[[], int] = time.monotonic_ns,
    new_event_id: Callable[[], str] = lambda: new_id("end"),
) -> NodeResult[PurgeData]:
    """pair_id: 요청한 페어. current: 그 페어의 최신 도움 snapshot(N-11), 이미 지워졌으면 None."""

    run = NodeRun(NODE_ID, trace_id, source, clock_ns)
    refs = tuple(input_refs)
    scope_basis = Basis(
        pair_id=pair_id if isinstance(pair_id, str) else None,
        help_id=inp.help_id if isinstance(inp, PurgeInput) else None,
    )

    # 1. 입력 검사 (부수 효과 전에 서버 범위가 모두 연결됐는지도 확인)
    if _input_problems(inp, pair_id, current, stores):
        return run.error(ErrorCode.INVALID_INPUT, "validate_input", scope_basis, input_refs=refs)
    run.step("validate_input", StepStatus.SUCCESS)

    # 2. 종료 사유와 요청 역할이 맞아야 한다
    if inp.role not in _REASON_ROLES[inp.end_reason]:
        return run.error(ErrorCode.UNAUTHORIZED, "authorize", scope_basis, input_refs=refs)
    run.step("authorize", StepStatus.SUCCESS)

    with tombstones.help_lock(inp.help_id):  # 같은 도움의 정리는 한 번에 하나만
        # 3. 이미 끝난 도움이면 부수 효과 없이 기록만 돌려준다
        done = tombstones.get(inp.help_id)
        if done is not None:
            if done.pair_id != pair_id:
                return run.error(ErrorCode.UNAUTHORIZED, "check_scope", scope_basis, input_refs=refs)
            run.step("check_scope", StepStatus.SUCCESS, ErrorCode.ALREADY_ENDED.value)
            for name in ("purge_server_scopes", "write_tombstone", "notify_parent", "notify_child"):
                run.step(name, StepStatus.SKIPPED, ErrorCode.DUPLICATE.value)
            return run.success(
                OUTCOME_ALREADY_ENDED,
                PurgeData(
                    end_event_id=done.end_event_id,
                    end_reason=done.end_reason,
                    cleared_scopes=(),
                    resume_requires_consent=done.resume_requires_consent,
                ),
                Basis(pair_id=pair_id, help_id=inp.help_id, event_seq=done.final_event_seq),
                refs,
            )

        # 4. 현재 도움만 종료한다. 이전·다른 도움 요청은 현재 데이터를 건드리지 않는다.
        if current is None or current.help_id != inp.help_id:
            return run.error(ErrorCode.STALE_BASIS, "check_scope", scope_basis, input_refs=refs)
        if current.pair_id != pair_id:
            return run.error(ErrorCode.UNAUTHORIZED, "check_scope", scope_basis, input_refs=refs)
        run.step("check_scope", StepStatus.SUCCESS)
        basis = Basis.from_snapshot(current)

        # 5. 서버 범위 정리 (멱등). 실패하면 종료 기록·이벤트 없이 재시도하게 둔다.
        #    HELP_STATE를 마지막에 지워야 실패 뒤 재시도할 때 current snapshot이 남아 있다.
        cleared: list[CleanupScope] = []
        ordered = sorted(stores, key=lambda s: s.scope is CleanupScope.HELP_STATE)
        try:
            for store in ordered:
                store.purge(inp.help_id)
                if store.scope not in cleared:
                    cleared.append(store.scope)
        except Exception:
            return run.error(ErrorCode.DEPENDENCY_UNAVAILABLE, "purge_server_scopes", basis,
                             retryable=True, input_refs=refs)
        run.step("purge_server_scopes", StepStatus.SUCCESS, count=len(cleared))

        # 6. 늦은 응답을 거를 최소 기록
        end_event_id = new_event_id()
        resume_requires_consent = inp.end_reason in _CONSENT_GATED or not inp.consent_active
        tombstones.record(HelpTombstone(
            pair_id=pair_id,
            help_id=inp.help_id,
            end_event_id=end_event_id,
            end_reason=inp.end_reason,
            final_event_seq=current.event_seq + 1,
            resume_requires_consent=resume_requires_consent,
        ))
        run.step("write_tombstone", StepStatus.SUCCESS)

    # 7. 양쪽 단말에 help.ended (잠금 밖: 전송 지연이 다른 도움 정리를 막지 않게)
    event = ControlEvent(
        v=EVENT_VERSION,
        type=HELP_ENDED_EVENT,
        event_id=end_event_id,
        pair_id=pair_id,
        call_id=current.call_id,
        help_id=inp.help_id,
        payload={"end_event_id": end_event_id, "reason": inp.end_reason.value},
    )
    pending = False
    for role in (Role.PARENT, Role.CHILD):
        step = f"notify_{role.value.lower()}"
        if events.send(role, event):
            cleared.append(_DEVICE_SCOPE[role])
            run.step(step, StepStatus.SUCCESS)
        else:
            pending = True
            run.step(step, StepStatus.FAILED, ErrorCode.DEPENDENCY_UNAVAILABLE.value, retryable=False)

    # 8. 다음 도움을 받을 수 있는가 (진단 기록)
    if resume_requires_consent:
        run.step("next_help", StepStatus.SUCCESS, "WAIT_PARENT_CONSENT")
    elif inp.rtc_active:
        run.step("next_help", StepStatus.SUCCESS, "WAIT_NEW_CHILD_TURN")
    else:
        run.step("next_help", StepStatus.SUCCESS, "CALL_ENDED")

    data = PurgeData(
        end_event_id=end_event_id,
        end_reason=inp.end_reason,
        cleared_scopes=tuple(cleared),
        resume_requires_consent=resume_requires_consent,
    )
    if pending:
        return run.partial(OUTCOME_LOCAL_CLEANUP_PENDING, data, basis, refs)
    return run.success(OUTCOME_HELP_ENDED, data, basis, refs)
