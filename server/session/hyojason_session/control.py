"""Apply authenticated N-11 events and their N-12 evidence in one critical section.

종료·도착은 계약 흐름(docs/work/D-status.md 결정 메모 5 (가))을 따른다.
- 자녀 완료·부모 중단: submit_command → N-22 승인 → N-11 help.end → N-23 정리·help.ended
- 도착 추정: submit_arrival → N-22 검증·중복 제거 → N-11 arrival.suspected/revoked
- N-11 안에서 생기는 종료(동의 철회, 목표 없는 통화 종료)도 help.purge 효과로 N-23을 거친다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from threading import RLock

from .common.decisions import ArrivalDecision
from .common.enums import ActorRole, ArrivalState, CleanupScope, EndReason, HelpCommand, Role, Source
from .common.events import ControlEvent
from .common.result import Basis, NodeResult, ResultRef, new_id
from .common.types import ActorContext, HelpSnapshot
from .completion import CompletionInput, CompletionLedger, resolve_arrival_or_completion
from .help_state import Actor, Event, HelpSession
from .navigation import (
    InstructionTrace, NavigationCatalog, NavigationTrail, ScreenRef, Transition,
    record_verified_navigation,
)
from .processing import Run
from .ports import EventSink, HelpScopedStore, HelpTombstone
from .purge import PurgeInput, TombstoneRegistry, purge_help_and_restart


@dataclass(frozen=True)
class Update:
    state: NodeResult
    navigation: NodeResult | None
    purge: NodeResult | None = None  # N-23, 이번 이벤트로 도움이 끝났을 때만


@dataclass(frozen=True)
class Resolution:
    """N-22 결과와, 승인됐을 때 이어서 적용한 N-11(·N-23) 결과."""

    completion: NodeResult
    update: Update | None


# 종료 사유 → N-23에 넘길 요청 역할 (CONSENT_REVOKED는 철회한 쪽)
_END_ROLE = {
    EndReason.CHILD_COMPLETED: ActorRole.CHILD,
    EndReason.PARENT_STOPPED: ActorRole.PARENT,
    EndReason.NO_GOAL_AT_HANGUP: ActorRole.AGENT,
}
_CONSENT_ENDS = (EndReason.PARENT_STOPPED, EndReason.CONSENT_REVOKED)


class _NoChannel:
    """제어 채널이 연결되지 않은 SessionControl. 전달 실패로 기록돼 N-23이 partial을 낸다."""

    def send(self, role: Role, event: ControlEvent) -> bool:
        return False


class _NoDataHeld:
    """이 서버가 아직 해당 범위의 데이터를 들고 있지 않음을 명시하는 자리 (B-05 결과, B-02 발화).

    해당 저장소가 세션 서버에 생기면 result_stores로 실제 저장소를 넘겨 교체한다.
    """

    def __init__(self, scope: CleanupScope) -> None:
        self.scope = scope

    def purge(self, help_id: str) -> None:
        return None


class _HelpStatePurge:
    """HELP_STATE: N-11의 _end가 목표·지시·화면·발화 참조를 이미 비웠는지 확인한다."""

    scope = CleanupScope.HELP_STATE

    def __init__(self, control: SessionControl) -> None:
        self._control = control

    def purge(self, help_id: str) -> None:
        s = self._control._session.snapshot
        if s.help_id == help_id and (s.goal or s.instruction or s.screen or s.utterance_seq is not None):
            raise RuntimeError("N-11 help state still holds help content")


class _TrailPurge:
    """NAVIGATION_TRAIL: 끝난 도움의 N-12 이력을 빈 이력으로 바꾼다."""

    scope = CleanupScope.NAVIGATION_TRAIL

    def __init__(self, control: SessionControl) -> None:
        self._control = control

    def purge(self, help_id: str) -> None:
        if self._control._trail.help_id == help_id:
            self._control._trail = NavigationTrail(help_id)


def _screen_ref(screen):
    return ScreenRef(screen.screen_kind, screen.projection_epoch, screen.screen_revision)


def _trace(event, previous, current):
    if event.type == "instruction.drafted":
        instruction = current.instruction
        target, codes, outcome, kind = None, (), "ACCEPTED", "instruction"
    else:
        instruction = previous.instruction
        codes = tuple(event.payload["evidence_codes"])
        if event.type == "target.located":
            target = event.payload["target_id"]
            outcome = event.payload["location_kind"].upper()
            kind = "location"
        else:
            target = None
            outcome = event.payload["verdict"]
            kind = "assessment"
    return InstructionTrace(
        instruction.instruction_id, instruction.instruction_revision, target,
        instruction.origin_screen_revision, current.screen_revision if current.screen else None,
        codes, outcome, kind,
    )


class SessionControl:
    """One instance per authenticated call, owned by the control server."""

    def __init__(self, pair_id: str, call_id: str, help_id: str, *,
                 catalog: NavigationCatalog, approved_slots, source: Source = "LIVE",
                 events: EventSink | None = None,
                 ledger: CompletionLedger | None = None,
                 tombstones: TombstoneRegistry | None = None,
                 result_stores: Sequence[HelpScopedStore] = ()):
        """events: 역할별 제어 채널(WS). ledger·tombstones: 통화를 넘어 페어 단위로 공유할 수 있다.
        result_stores: B-05 판단 결과·B-02 대기 발화처럼 이 서버가 도움 단위로 들고 있는 저장소.
        """
        self._session = HelpSession(
            pair_id, call_id, help_id, screen_apps=catalog.screen_apps,
            target_ids=catalog.target_ids, evidence_codes=catalog.evidence_codes,
            approved_slots=approved_slots,
        )
        self._catalog = catalog
        self._source = source
        self._trail = NavigationTrail(help_id)
        self._events: EventSink = events or _NoChannel()
        self._ledger = ledger or CompletionLedger()
        self._tombstones = tombstones or TombstoneRegistry()
        stores: list[HelpScopedStore] = [_HelpStatePurge(self), _TrailPurge(self), self._ledger]
        stores += list(result_stores)
        held = {store.scope for store in stores}
        stores += [_NoDataHeld(scope) for scope in (CleanupScope.INFERENCE_RESULTS,
                                                    CleanupScope.PENDING_UTTERANCES) if scope not in held]
        self._purge_stores = tuple(stores)
        self._lock = RLock()

    @property
    def snapshot(self):
        with self._lock:
            return self._session.snapshot

    @property
    def trail(self) -> NavigationTrail:
        with self._lock:
            return self._trail

    def latest_help_id(self, pair_id: str) -> str | None:
        snapshot = self.snapshot
        return snapshot.help_id if snapshot.pair_id == pair_id else None

    def get_snapshot(self, help_id: str) -> HelpSnapshot | None:
        snapshot = self.snapshot
        return snapshot.to_shared_snapshot() if snapshot.help_id == help_id else None

    def get_tombstone(self, help_id: str) -> HelpTombstone | None:
        return self._tombstones.get(help_id)

    @property
    def tombstones(self) -> TombstoneRegistry:
        return self._tombstones

    def arrival_state(self, help_id: str) -> ArrivalState:
        snapshot = self.snapshot
        if snapshot.help_id == help_id and snapshot.help_state == "ARRIVAL_PENDING":
            return ArrivalState.SUSPECTED
        return ArrivalState.NONE

    def observation_paused(self, help_id: str) -> bool:
        snapshot = self.snapshot
        return snapshot.help_id == help_id and snapshot.observation_health in ("STOPPED", "NOT_GRANTED")

    def apply(self, event: Event, actor: Actor, basis: Basis, *,
              trace_id: str | None = None, inputs: tuple[NodeResult, ...] = ()) -> Update:
        """Publish returned N-11 effects only after this call succeeds."""
        with self._lock:
            refs = tuple(item.ref for item in inputs)
            source = "FIXTURE" if any(item.diagnostics.source == "FIXTURE" for item in inputs) else self._source
            run = Run(basis, source=source, input_refs=refs,
                      trace_id=trace_id or (refs[0].trace_id if refs else None))
            trail = self._trail
            navigation = None

            def stage(previous, candidate, accepted, effects, parent_run):
                nonlocal trail, navigation
                kinds = {effect.kind for effect in effects}
                if "trail.clear" in kinds or candidate.help_id != trail.help_id:
                    trail = NavigationTrail(candidate.help_id)
                if candidate.help_state == "ENDED" or "trail.clear" in kinds:
                    return None
                navigation = self._record(previous, candidate, accepted, kinds, trail, parent_run)
                if navigation is not None:
                    if navigation.status == "error":
                        return navigation
                    trail = navigation.data.trail
                    return trail.last_event_seq
                return None

            state = self._session.reduce_help_state(event, actor, run, before_commit=stage)
            purge = None
            if state.status == "success" and state.outcome != "duplicate_ignored":
                self._trail = trail
                self._source = source
                if any(effect.kind == "help.purge" for effect in state.data.side_effects):
                    purge = self._purge(state, actor.role)
            return Update(state, navigation, purge)

    def _purge(self, state: NodeResult, actor_role: str) -> NodeResult:
        """N-11이 도움을 끝낸 직후 N-23으로 남은 범위를 정리하고 help.ended를 보낸다."""
        snapshot = state.data.snapshot
        reason = snapshot.end_reason
        role = ActorRole(actor_role) if reason is EndReason.CONSENT_REVOKED else _END_ROLE[reason]
        shared = snapshot.to_shared_snapshot()
        return purge_help_and_restart(
            PurgeInput(
                help_id=shared.help_id,
                end_reason=reason,
                rtc_active=snapshot.call_state in ("ACTIVE", "RECONNECTING"),
                role=role,
                consent_active=reason not in _CONSENT_ENDS,
            ),
            pair_id=shared.pair_id, current=shared, stores=self._purge_stores,
            tombstones=self._tombstones, events=self._events,
            trace_id=state.diagnostics.trace_id, source=Source(state.diagnostics.source),
            input_refs=(state.ref,),
        )

    def submit_command(self, command: HelpCommand, actor: ActorContext, help_id: str, *,
                       goal_revision: int = 0, trace_id: str | None = None) -> Resolution:
        """help.complete(자녀)/help.stop(부모). actor는 인증 토큰에서 얻는다.

        goal_revision은 기록용이다. 자녀는 목표 정보를 받지 않아 알 수 없으므로 범위 확인에 쓰지 않는다.
        """
        with self._lock:
            current = self._session.snapshot
            completion = resolve_arrival_or_completion(
                CompletionInput(None, command, help_id, goal_revision,
                                ActorRole(actor.role.value), current.to_shared_snapshot()),
                actor=actor, ledger=self._ledger, trace_id=trace_id or new_id("trace"),
                source=Source(self._source),
            )
            if completion.data is None or not completion.data.end_requested:
                return Resolution(completion, None)
            event = Event("help.end", new_id("end-cmd"), current.pair_id, current.call_id,
                          current.help_id, {"reason": completion.data.end_reason.value})
            update = self.apply(event, Actor("SERVER", current.pair_id, current.call_id), current.basis,
                                inputs=(completion,))
            return Resolution(completion, update)

    def submit_arrival(self, decision: ArrivalDecision, *, trace_id: str | None = None,
                       inputs: tuple[NodeResult, ...] = ()) -> Resolution:
        """N-18의 도착 추정. N-22가 알릴 변화가 있다고 판단할 때만 N-11에 반영한다."""
        with self._lock:
            current = self._session.snapshot
            completion = resolve_arrival_or_completion(
                CompletionInput(decision, None, decision.basis.help_id or current.help_id,
                                decision.goal_revision, ActorRole.AGENT, current.to_shared_snapshot()),
                actor=None, ledger=self._ledger,
                trace_id=trace_id or (inputs[0].diagnostics.trace_id if inputs else new_id("trace")),
                source=Source(self._source), input_refs=tuple(item.ref for item in inputs),
            )
            data = completion.data
            if data is None or data.announcement is None:
                return Resolution(completion, None)
            kind = "arrival.suspected" if data.announcement is ArrivalState.SUSPECTED else "arrival.revoked"
            event = Event(kind, new_id("arrival"), current.pair_id, current.call_id, current.help_id,
                          {"arrival_id": data.arrival_id})
            update = self.apply(event, Actor("SERVER", current.pair_id, current.call_id), decision.basis,
                                inputs=(completion,))
            return Resolution(completion, update)

    def _record(self, previous, candidate, event, kinds, trail, parent_run):
        transition = directive = None
        health = candidate.observation_health
        if event.type == "screen.observed":
            if health == "ACTIVE":
                # Capture may continue across hangup while N-11 awaits a fresh screen.
                before = trail.anchor
                contiguous = (before is not None and before.projection_epoch == candidate.projection_epoch
                              and before.screen_revision == event.payload.get("from_screen_revision"))
                transition = Transition(
                    before if contiguous else None, _screen_ref(candidate.screen),
                    candidate.screen.evidence_codes, event.payload.get("reversible", False),
                )
        elif event.type == "projection.started":
            # A new MediaProjection token starts a fresh observation segment.
            if previous.projection_epoch is None:
                return None
            health = "GAP"
        elif ((event.type == "instruction.drafted" and "instruction.accepted" in kinds)
              or event.type in ("target.located", "action.assessed")):
            directive = _trace(event, previous, candidate)
        else:
            return None

        # N-12 consumes the accepted proposal in the server's write lock. Its
        # scope includes the newly assigned revisions, unlike the N-11 input.
        ref = ResultRef(parent_run.trace_id, "N-11", parent_run.run_id)
        run = Run(candidate.basis, source=parent_run.source, trace_id=parent_run.trace_id,
                  input_refs=(ref,))
        return record_verified_navigation(
            run=run, current_basis=candidate.basis, help_id=candidate.help_id,
            goal_revision=candidate.goal_revision, trail=trail, catalog=self._catalog,
            observation_health=health, observed_transition=transition, directive_record=directive,
            current_instruction_id=directive.instruction_id if directive else None,
        )
