"""N-22 · resolve_arrival_or_completion — 도착 추정과 자녀 최종 완료, 부모 안내 끄기를 따로 처리한다.

docs/small-nodes/N-22.md
- 결과: success/arrival_announced, success/end_requested, success/duplicate_ignored, error/null
- 도착 알림은 POST_CALL_AGENT에서 (help_id, mode_revision, goal_revision, arrival_id)로 중복 제거
- 자녀는 도착 알림 전에도 완료할 수 있다. 부모 안내 끄기는 별도 종료로 받는다.
- 종료 승인은 도움마다 원자적으로 한 번만 (자녀 완료·부모 중단이 겹쳐도 종료 1회)
- 자녀 완료와 부모 중단의 종류를 서로 바꿔 처리하지 않는다

검증 범위 결정 (docs/work/D-status.md '결정 메모')
- 도착: help·goal·mode·동의·화면(epoch, screen_revision)이 현재 snapshot과 모두 같아야 한다.
- 완료·중단 명령: help_id로만 범위를 확인한다. 자녀 앱은 N-21에 따라 목표 정보를 받지 않아
  goal_revision을 알 수 없고, 부모 안내 끄기는 네트워크와 무관하게 항상 받아야 하기 때문이다(N-23).
  입력 goal_revision은 Basis 기록에만 남는다.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .common.decisions import ArrivalDecision
from .common.enums import (
    ActorRole,
    ArrivalState,
    CallState,
    CleanupScope,
    EndReason,
    HelpCommand,
    HelpState,
    Mode,
    Source,
)
from .common.result import Basis, ErrorCode, NodeResult, NodeRun, ResultRef, StepStatus
from .common.roles import completed_reason, counterpart, stopped_reason
from .common.types import ActorContext, HelpSnapshot, snapshot_problems

NODE_ID = "N-22"
OUTCOME_ARRIVAL_ANNOUNCED = "arrival_announced"
OUTCOME_END_REQUESTED = "end_requested"
OUTCOME_DUPLICATE_IGNORED = "duplicate_ignored"

def _command_rule(command: HelpCommand, snapshot: HelpSnapshot) -> tuple[ActorRole, EndReason]:
    """명령 종류 → 보낼 수 있는 역할과 종료 사유 (#6 제안: 완료는 지원 대상의 상대, 안내 끄기는 지원 대상).

    기본 지원 대상이 부모라 완료=자녀(CHILD_COMPLETED), 끄기=부모(PARENT_STOPPED)로 기존과 같다.
    """
    role = counterpart(snapshot.assisted_role) if command is HelpCommand.COMPLETE else snapshot.assisted_role
    reason = completed_reason(role) if command is HelpCommand.COMPLETE else stopped_reason(role)
    return ActorRole(role.value), reason


@dataclass(frozen=True)
class _ArrivalRecord:
    state: ArrivalState
    goal_revision: int
    mode_revision: int
    arrival_id: str


class CompletionLedger:
    """N-22의 도움별 메모리 상태: 도착 알림 상태, 처리한 도착 키, 종료 승인.

    HelpScopedStore(scope=ARRIVAL_NOTICE)로 N-23 정리 대상이다. 종료 승인은 사용자 내용이 없는
    멱등 식별자라 purge 뒤에도 남긴다 (contracts §3 '종료에 필요한 멱등 식별자').
    """

    scope = CleanupScope.ARRIVAL_NOTICE

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seen: set[tuple[str, int, int, str]] = set()
        self._arrival: dict[str, _ArrivalRecord] = {}
        self._end_claims: dict[str, EndReason] = {}

    def arrival_state_for(self, snapshot: HelpSnapshot) -> ArrivalState:
        """goal/mode가 바뀌면 이전 도착 추정은 보이지 않는다."""
        with self._lock:
            rec = self._arrival.get(snapshot.help_id)
        goal_rev = snapshot.goal.goal_revision if snapshot.goal else None
        if rec is None or rec.goal_revision != goal_rev or rec.mode_revision != snapshot.mode_revision:
            return ArrivalState.NONE
        return rec.state

    def apply_arrival(
        self, *, help_id: str, mode_revision: int, goal_revision: int, arrival_id: str, arrived: bool
    ) -> tuple[ArrivalState | None, str | None]:
        """(새로 알릴 상태, 알림의 arrival_id). 바뀔 게 없거나 처리한 키면 (None, None).

        철회(REVOKED)의 arrival_id는 철회 판단의 id가 아니라 앞서 알린 도착의 id다.
        자녀 앱과 N-11은 그 id로 어떤 알림을 내릴지 안다.
        """
        key = (help_id, mode_revision, goal_revision, arrival_id)
        with self._lock:
            if key in self._seen:
                return None, None
            self._seen.add(key)
            rec = self._arrival.get(help_id)
            current = (
                rec
                if rec and rec.goal_revision == goal_revision and rec.mode_revision == mode_revision
                else None
            )
            if arrived:
                if current is not None and current.state is ArrivalState.SUSPECTED:
                    return None, None  # 같은 목표에 이미 알렸다
                new_state, announced = ArrivalState.SUSPECTED, arrival_id
            else:
                if current is None or current.state is not ArrivalState.SUSPECTED:
                    return None, None  # 철회할 알림이 없다
                new_state, announced = ArrivalState.REVOKED, current.arrival_id
            self._arrival[help_id] = _ArrivalRecord(new_state, goal_revision, mode_revision, announced)
            return new_state, announced

    def end_claim(self, help_id: str) -> EndReason | None:
        with self._lock:
            return self._end_claims.get(help_id)

    def claim_end(self, help_id: str, reason: EndReason) -> tuple[bool, EndReason]:
        """원자적 종료 승인. (이번 호출이 승인했는가, 승인된 사유)."""
        with self._lock:
            existing = self._end_claims.get(help_id)
            if existing is not None:
                return False, existing
            self._end_claims[help_id] = reason
            return True, reason

    def purge(self, help_id: str) -> None:
        with self._lock:
            self._arrival.pop(help_id, None)
            self._seen = {k for k in self._seen if k[0] != help_id}


@dataclass(frozen=True)
class CompletionInput:
    arrival: ArrivalDecision | None
    command: HelpCommand | None
    help_id: str
    goal_revision: int
    actor_role: ActorRole
    snapshot: HelpSnapshot


@dataclass(frozen=True)
class CompletionData:
    announcement: ArrivalState | None  # SUSPECTED → arrival.suspected, REVOKED → arrival.revoked
    arrival_id: str | None
    end_requested: bool  # True면 호출자가 N-23을 한 번 부른다
    end_reason: EndReason | None


def _is_uint(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def _input_problems(inp: object) -> list[str]:
    if not isinstance(inp, CompletionInput):
        return ["input"]
    bad = snapshot_problems(inp.snapshot)
    if not isinstance(inp.help_id, str) or not inp.help_id:
        bad.append("help_id")
    if not _is_uint(inp.goal_revision):
        bad.append("goal_revision")
    if not isinstance(inp.actor_role, ActorRole):
        bad.append("actor_role")
    if (inp.arrival is None) == (inp.command is None):
        bad.append("arrival|command")  # 한 호출에 도착 또는 명령 하나만
    if inp.command is not None and not isinstance(inp.command, HelpCommand):
        bad.append("command")
    a = inp.arrival
    if a is not None:
        if not isinstance(a, ArrivalDecision) or not isinstance(a.basis, Basis):
            bad.append("arrival")
        else:
            if not isinstance(a.arrival_id, str) or not a.arrival_id:
                bad.append("arrival.arrival_id")
            if not isinstance(a.arrived, bool) or not _is_uint(a.goal_revision):
                bad.append("arrival.arrived|goal_revision")
            if a.arrived and not a.evidence_codes:
                bad.append("arrival.evidence_codes")  # 화면 증거 없는 도착은 받지 않는다
            b = a.basis
            # 도착 제안은 도움·동의·모드·목표·화면·캡처 버전이 모두 있어야 한다 (node-result-contract §3)
            for name in ("help_id", "consent_revision", "mode_revision", "goal_revision",
                         "projection_epoch", "screen_revision"):
                if getattr(b, name) is None:
                    bad.append(f"arrival.basis.{name}")
    return bad


def resolve_arrival_or_completion(
    inp: CompletionInput,
    *,
    actor: ActorContext | None,
    ledger: CompletionLedger,
    trace_id: str,
    source: Source = Source.LIVE,
    input_refs: Iterable[ResultRef] = (),
    clock_ns: Callable[[], int] = time.monotonic_ns,
) -> NodeResult[CompletionData]:
    """actor: 사용자 명령이면 인증 토큰의 호출자, Agent 도착 판단이면 None."""

    run = NodeRun(NODE_ID, trace_id, source, clock_ns)
    refs = tuple(input_refs)

    # 1. 입력 검사
    if _input_problems(inp):
        pair = inp.snapshot.pair_id if isinstance(inp, CompletionInput) and isinstance(
            inp.snapshot, HelpSnapshot) else None
        return run.error(ErrorCode.INVALID_INPUT, "validate_input", Basis(pair_id=pair),
                         input_refs=refs)
    run.step("validate_input", StepStatus.SUCCESS)
    snap = inp.snapshot
    basis = _call_basis(inp)

    # 2. 권한: 도착은 서버 Agent만, 완료는 등록 자녀만, 중단은 부모만
    if inp.arrival is not None:
        authorized = inp.actor_role is ActorRole.AGENT and actor is None
    else:
        allowed_role, _ = _command_rule(inp.command, snap)
        authorized = (
            inp.actor_role is allowed_role
            and actor is not None
            and actor.role.value == inp.actor_role.value
            and actor.pair_id == snap.pair_id
        )
    if not authorized:
        return run.error(ErrorCode.UNAUTHORIZED, "authorize", basis, input_refs=refs)
    run.step("authorize", StepStatus.SUCCESS)

    # 3. 대상 도움이 현재 도움인가
    if inp.help_id != snap.help_id:
        return run.error(ErrorCode.STALE_BASIS, "check_scope", basis, input_refs=refs)

    if inp.command is not None:
        return _resolve_command(run, inp, ledger, basis, refs)
    return _resolve_arrival(run, inp, ledger, basis, refs)


def _call_basis(inp: CompletionInput) -> Basis:
    """호출의 입력 스코프. 도착이면 판단이 잠근 Basis, 명령이면 현재 snapshot + 명령의 goal_revision."""
    if inp.arrival is not None:
        return inp.arrival.basis
    base = Basis.from_snapshot(inp.snapshot)
    return Basis(**{**vars(base), "help_id": inp.help_id, "goal_revision": inp.goal_revision})


def _resolve_command(run, inp, ledger, basis, refs) -> NodeResult[CompletionData]:
    snap = inp.snapshot
    _, reason = _command_rule(inp.command, snap)
    already = ledger.end_claim(inp.help_id) or (
        snap.end_reason if snap.help_state is HelpState.ENDED else None
    )
    if already is not None:
        run.step("check_scope", StepStatus.SUCCESS)
        run.step("claim_end", StepStatus.SKIPPED, ErrorCode.DUPLICATE.value)
        return run.success(OUTCOME_DUPLICATE_IGNORED, _duplicate_end(already), basis, refs)
    if inp.command is HelpCommand.COMPLETE and snap.help_state is HelpState.LISTENING:
        # 지시·목표가 생기기 전에는 자녀 완료 버튼이 비활성이다 (N-21 can_complete=false)
        return run.error(ErrorCode.NOT_APPLICABLE, "check_scope", basis, input_refs=refs)
    run.step("check_scope", StepStatus.SUCCESS)

    claimed, winner = ledger.claim_end(inp.help_id, reason)
    if not claimed:  # 동시에 들어온 다른 종료가 먼저 승인됨
        run.step("claim_end", StepStatus.SKIPPED, ErrorCode.DUPLICATE.value)
        return run.success(OUTCOME_DUPLICATE_IGNORED, _duplicate_end(winner), basis, refs)
    run.step("claim_end", StepStatus.SUCCESS)
    return run.success(
        OUTCOME_END_REQUESTED,
        CompletionData(announcement=None, arrival_id=None, end_requested=True, end_reason=reason),
        basis,
        refs,
    )


def _duplicate_end(reason: EndReason) -> CompletionData:
    # 종료 이벤트는 다시 내지 않는다. 이미 승인된 사유만 알려 현재 상태를 안내한다.
    return CompletionData(announcement=None, arrival_id=None, end_requested=False, end_reason=reason)


def _resolve_arrival(run, inp, ledger, basis, refs) -> NodeResult[CompletionData]:
    snap = inp.snapshot
    a: ArrivalDecision = inp.arrival
    if snap.help_state is HelpState.ENDED or ledger.end_claim(inp.help_id) is not None:
        return run.error(ErrorCode.ALREADY_ENDED, "check_scope", basis, input_refs=refs)
    # 통화 중에는 도착 알림을 내지 않는다
    if snap.mode is not Mode.POST_CALL_AGENT or snap.call_state is not CallState.ENDED or snap.goal is None:
        return run.error(ErrorCode.NOT_APPLICABLE, "check_scope", basis, input_refs=refs)
    b = a.basis
    current_goal = snap.goal.goal_revision
    fresh = (
        b.help_id == inp.help_id
        and a.goal_revision == inp.goal_revision == b.goal_revision == current_goal
        and b.mode_revision == snap.mode_revision
        and b.consent_revision == snap.consent_revision
        and snap.screen is not None
        and b.projection_epoch == snap.screen.projection_epoch
        and b.screen_revision == snap.screen.screen_revision
    )
    if not fresh:
        return run.error(ErrorCode.STALE_BASIS, "check_scope", basis, input_refs=refs)
    run.step("check_scope", StepStatus.SUCCESS)

    announcement, announced_id = ledger.apply_arrival(
        help_id=inp.help_id,
        mode_revision=snap.mode_revision,
        goal_revision=current_goal,
        arrival_id=a.arrival_id,
        arrived=a.arrived,
    )
    data = CompletionData(
        announcement=announcement,
        arrival_id=announced_id or a.arrival_id,
        end_requested=False,
        end_reason=None,
    )
    if announcement is None:
        run.step("announce_arrival", StepStatus.SKIPPED, ErrorCode.DUPLICATE.value)
        return run.success(OUTCOME_DUPLICATE_IGNORED, data, basis, refs)
    run.step("announce_arrival", StepStatus.SUCCESS, announcement.value)
    return run.success(OUTCOME_ARRIVAL_ANNOUNCED, data, basis, refs)
