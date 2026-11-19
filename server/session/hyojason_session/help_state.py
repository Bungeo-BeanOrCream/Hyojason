"""N-11: the in-memory, single writer for an authenticated call's help state."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from copy import copy
from dataclasses import dataclass, replace
from threading import RLock
from types import MappingProxyType
from uuid import uuid4

from .common import types as shared
from .common.enums import Action, CallState, EndReason, HelpState, Mode, ObservationHealth, Role
from .processing import Basis, NodeResult, Run, SCREEN_BOUNDARIES


@dataclass(frozen=True)
class Actor:
    """Supplied by the authenticated channel (or an internal node), never the payload."""

    role: str
    pair_id: str
    call_id: str


@dataclass(frozen=True)
class Event:
    type: str
    event_id: str
    pair_id: str
    call_id: str
    help_id: str | None
    payload: dict
    v: int = 1


@dataclass(frozen=True)
class Instruction:
    instruction_id: str
    instruction_revision: int
    source_utterance_id: str
    target_key: str | None
    action: str
    origin_screen_revision: int | None


@dataclass(frozen=True)
class Goal:
    goal_id: str
    goal_revision: int
    approved_slots: Mapping
    source_utterance_id: str


@dataclass(frozen=True)
class ScreenState:
    projection_epoch: str
    screen_revision: int
    screen_kind: str
    target_ids: tuple[str, ...]
    evidence_codes: tuple[str, ...]
    observation_health: str = "ACTIVE"


@dataclass(frozen=True)
class HelpSnapshot:
    pair_id: str
    call_id: str
    help_id: str
    call_state: str = "IDLE"
    help_state: str = "LISTENING"
    mode: str = "CALL_CHILD_LED"
    mode_revision: int = 0
    consent_revision: int = 0
    instruction_revision: int = 0
    goal_revision: int = 0
    instruction: Instruction | None = None
    goal: Goal | None = None
    screen: ScreenState | None = None
    projection_epoch: str | None = None
    screen_revision: int | None = None
    observation_health: str = "NOT_GRANTED"
    utterance_seq: int | None = None
    trail_cursor: int = 0
    event_seq: int = 0
    end_reason: EndReason | None = None
    # 이번 도움에서 관찰·안내를 받는 기기 (#6). 도움마다 고정한다.
    assisted_role: str = "PARENT"

    @property
    def basis(self) -> Basis:
        return Basis(
            pair_id=self.pair_id, call_id=self.call_id, help_id=self.help_id,
            event_seq=self.event_seq, consent_revision=self.consent_revision,
            goal_revision=self.goal_revision, mode_revision=self.mode_revision,
            instruction_revision=self.instruction_revision, projection_epoch=self.projection_epoch,
            screen_revision=self.screen_revision, utterance_seq=self.utterance_seq,
        )

    def to_shared_snapshot(self) -> shared.HelpSnapshot:
        """N-13/N-21 snapshot. String slots are preserved, integers use decimal
        notation, and booleans use the JSON literals 'true' and 'false'.
        """
        goal = None
        if self.goal:
            slots = {key: str(value).lower() if type(value) is bool else str(value)
                     for key, value in self.goal.approved_slots.items()}
            goal = shared.Goal(self.goal.goal_id, self.goal.goal_revision, MappingProxyType(slots),
                               self.goal.source_utterance_id)
        instruction = None
        if self.instruction:
            item = self.instruction
            instruction = shared.Instruction(
                item.instruction_id, item.instruction_revision, item.source_utterance_id,
                item.target_key, Action(item.action), item.origin_screen_revision,
            )
        screen = None
        if self.screen:
            item = self.screen
            screen = shared.ScreenState(item.projection_epoch, item.screen_revision, item.screen_kind,
                                        item.target_ids, item.evidence_codes,
                                        ObservationHealth(item.observation_health))
        return shared.HelpSnapshot(
            pair_id=self.pair_id, call_id=self.call_id, help_id=self.help_id,
            call_state=CallState(self.call_state), help_state=HelpState(self.help_state),
            mode=Mode(self.mode), mode_revision=self.mode_revision, goal=goal,
            instruction=instruction, screen=screen, trail_cursor=self.trail_cursor,
            consent_revision=self.consent_revision, event_seq=self.event_seq, end_reason=self.end_reason,
            assisted_role=Role(self.assisted_role),
        )


@dataclass(frozen=True)
class EffectRef:
    kind: str
    recipient: str
    basis: Basis


@dataclass(frozen=True)
class Reduction:
    snapshot: HelpSnapshot
    side_effects: tuple[EffectRef, ...]
    applied_event_seq: int

    def to_wire_fields(self):
        return {
            "snapshot": self.snapshot.to_shared_snapshot(),
            "side_effects": self.side_effects,
            "applied_event_seq": self.applied_event_seq,
        }


@dataclass
class _State:
    """Copy-on-write transaction. All shared members are immutable until commit."""

    snapshot: HelpSnapshot
    consenting: frozenset[str] = frozenset()
    audio_evaluated: bool = False
    # 지시를 말하는 쪽(지원 대상의 상대)의 최신 확정 발화
    latest_turn_id: str | None = None
    latest_turn_seq: int | None = None
    draft_kinds: frozenset[str] = frozenset()
    previous_screen_revision: int | None = None
    last_screen_meaning: tuple[str, frozenset[str]] | None = None
    observation_fence: int = 0
    arrival_id: str | None = None
    used_epochs: frozenset[str] = frozenset()
    acceptance_confirmed: bool = False
    inviter: str | None = None  # 이 통화를 건 역할. 수락은 그 상대만 한다
    call_fence: int = 0

    def update(self, **changes):
        self.snapshot = replace(self.snapshot, **changes)


class _Rejected(Exception):
    pass


def _require(condition, code="INVALID_INPUT"):
    if not condition:
        raise _Rejected(code)


def _id(value):
    _require(isinstance(value, str) and 0 < len(value) <= 128 and not any(c.isspace() for c in value))
    return value


def _uint(value):
    _require(type(value) is int and value >= 0)
    return value


def _codes(value, catalog):
    _require(isinstance(value, (list, tuple)) and all(isinstance(v, str) and v in catalog for v in value))
    return tuple(dict.fromkeys(value))


def _basis(current: HelpSnapshot, supplied: Basis, *names):
    _require(all(getattr(current, field) == getattr(supplied, field) for field in names), "STALE_BASIS")


_PUBLISHERS = {
    # 발신은 두 역할 모두, 수락은 발신자의 상대만 (#6 발신 방향과 안내 대상 분리, #10 B 요청).
    "call.invite": ("PARENT", "CHILD"), "call.accepted": ("PARENT", "CHILD"),
    "call.connected": ("SERVER",), "call.reconnecting": ("SERVER",), "call.ended": ("SERVER",),
    "consent.updated": ("PARENT", "CHILD"), "projection.started": ("PARENT", "CHILD"),
    "screen.observed": ("PARENT", "CHILD"), "speech.final": ("SERVER",),
    "instruction.drafted": ("SERVER",), "goal.drafted": ("SERVER",),
    "target.located": ("SERVER",), "action.assessed": ("SERVER",),
    "arrival.suspected": ("SERVER",), "arrival.revoked": ("SERVER",),
    # 자녀 완료·부모 중단은 N-22가 승인한 뒤 서버가 help.end로 넣는다 (계약 흐름 N-22 → N-11 → N-23)
    "help.end": ("SERVER",),
    # #6: 도움이 끝난 뒤 새 코드가 승인되면 B 서버가 넣는다. payload의 assisted_role로 다음 도움을 연다.
    "help.link_approved": ("SERVER",),
    "guide.shown": ("PARENT", "CHILD"), "guide.suppressed": ("PARENT", "CHILD"),
}
_ROLES = frozenset({"PARENT", "CHILD"})
# 지원 대상 기기만 보낼 수 있는 관찰·표시 이벤트
_ASSISTED_EVENTS = frozenset({"projection.started", "screen.observed", "guide.shown", "guide.suppressed"})


def _other(role: str) -> str:
    """지원 대상의 상대 역할: 지시를 말하고 도움 완료를 누른다."""
    return "CHILD" if role == "PARENT" else "PARENT"
_GOALS = {"SET_FONT", "SET_TIMEOUT", "CLOCK_ALARM", "MAP_ROUTE", "SMS_SEND", "MAIL_OPEN"}
# Agreed N-10 state evidence (docs/work/D-issue3-screen-kind-review.md).
# Visible labels, layout movement and target IDs are not semantic state.
_STATE_EVIDENCE = frozenset({
    "font_scale_changed", "timeout_10m_selected", "alarm_card_active", "alarm_next_tomorrow",
    "route_shown", "sms_sent_status", "mail_detail_open",
})


class HelpSession:
    """Owned by the server for the full call, including successive help IDs.

    N-02 authenticates roles and resolves analysis consent upstream. N-03 confirms
    actual RTC connection/hangup. Target, screen, evidence and slot registries come
    from the approved project catalog, not from a control event.
    """

    def __init__(self, pair_id: str, call_id: str, help_id: str, *,
                 screen_apps: Mapping[str, str], target_ids: Collection[str],
                 evidence_codes: Collection[str], approved_slots: Mapping[str, Callable],
                 assisted_role: str = "PARENT"):
        _require(assisted_role in _ROLES)
        self._state = _State(HelpSnapshot(_id(pair_id), _id(call_id), _id(help_id),
                                          assisted_role=assisted_role))
        self._screen_apps = dict(screen_apps)
        self._targets = frozenset(target_ids)
        self._evidence = frozenset(evidence_codes)
        self._slots = dict(approved_slots)
        self._seen = {}
        self._end_call_seq = 0
        self._lock = RLock()

    @property
    def snapshot(self) -> HelpSnapshot:
        with self._lock:
            return self._state.snapshot

    @property
    def processing_allowed(self) -> bool:
        """B-05 처리권: 양쪽 분석 동의가 있고 도움이 끝나지 않았다 (server/agent README '연결' 1)."""
        with self._lock:
            return self._state.consenting == _ROLES and self._state.snapshot.help_state != "ENDED"

    def reduce_help_state(self, event: Event, actor: Actor, run: Run, *, before_commit=None) -> NodeResult[Reduction]:
        """Apply an event once. An optional server hook can veto the candidate before publication."""
        with self._lock:
            previous = self._state.snapshot
            try:
                _require(isinstance(event, Event) and type(event.v) is int and event.v == 1)
                _require(isinstance(event.type, str) and event.type in _PUBLISHERS and isinstance(event.payload, dict))
                _id(event.event_id)
                _require(isinstance(actor, Actor) and actor.role in _PUBLISHERS[event.type], "UNAUTHORIZED")
                _require((actor.pair_id, actor.call_id) == (previous.pair_id, previous.call_id)
                         == (event.pair_id, event.call_id), "UNAUTHORIZED")
                _basis(previous, run.basis, "pair_id", "call_id")
                _require(event.help_id == run.basis.help_id, "STALE_BASIS")
                # A verified RTC hangup belongs to the call. A new help can have
                # opened while N-03's final disconnect notification was in flight.
                # Any other event must target the current help: a new help opens only
                # through help.link_approved (#6), so results for a retired help are stale.
                _require(event.help_id == previous.help_id or event.type == "call.ended", "STALE_BASIS")
                prior = self._seen.get(event.event_id)
                if event.type == "call.ended" and previous.call_state == "ENDED":
                    return run.success("N-11", "duplicate_ignored", Reduction(previous, (), self._end_call_seq))
                if prior:
                    _require(prior[:2] == (actor.role, event.type))
                    return run.success("N-11", "duplicate_ignored", Reduction(previous, (), prior[2]))

                candidate = copy(self._state)
                effects = []
                outcome = self._apply(candidate, event, actor.role, run.basis, effects)
                if outcome == "duplicate_ignored":
                    return run.success("N-11", outcome, Reduction(previous, (), previous.event_seq))
                new_help = candidate.snapshot.help_id != previous.help_id
                seq = 1 if new_help else previous.event_seq + 1
                candidate.update(event_seq=seq)
                snapshot = candidate.snapshot
                refs = tuple(EffectRef(name, recipient, snapshot.basis) for name, recipient in effects)
                if before_commit:
                    navigation = before_commit(previous, snapshot, event, refs, run)
                    if isinstance(navigation, int):
                        candidate.update(trail_cursor=navigation)
                        snapshot = candidate.snapshot
                    elif navigation is not None:
                        return run.error("N-11", navigation.diagnostics.terminal_error.code, step="navigation")
                if new_help or (snapshot.help_state == "ENDED" and previous.help_state != "ENDED"):
                    self._seen.clear()
                self._state = candidate
                if event.type == "call.ended":
                    self._end_call_seq = seq
                self._seen[event.event_id] = (actor.role, event.type, seq)
                return run.success("N-11", outcome, Reduction(snapshot, refs, seq))
            except _Rejected as error:
                return run.error("N-11", str(error))
            except (KeyError, TypeError, ValueError):
                return run.error("N-11", "INVALID_INPUT")

    def _apply(self, state, event, role, basis, effects):
        s, p, kind = state.snapshot, event.payload, event.type
        if kind in _ASSISTED_EVENTS:
            _require(role == s.assisted_role, "UNAUTHORIZED")
        if kind.startswith("call."):
            return self._call(state, kind, effects, role)
        if kind == "consent.updated":
            _basis(s, basis, "consent_revision")
            _require(p["purpose"] == "analysis" and type(p["enabled"]) is bool)
            if (role in state.consenting) == p["enabled"]:
                return "duplicate_ignored"
            state.update(consent_revision=s.consent_revision + 1)
            if p["enabled"]:
                state.consenting |= {role}
            else:
                state.consenting -= {role}
                self._end(state, effects, EndReason.CONSENT_REVOKED)
                effects.append(("stt.close", "SERVER"))
            return "state_applied"
        if kind == "help.end":
            # N-22가 권한·중복을 승인한 종료만 들어온다. 자녀는 goal_revision을 모르므로(N-21)
            # 범위는 공통 help_id 검사로만 확인한다 (docs/work/D-status.md 결정 메모 1).
            _require(s.help_state != "ENDED", "ALREADY_ENDED")
            reason = EndReason(p["reason"])
            # 안내 끄기는 지원 대상, 도움 완료는 상대 역할 (#6 제안, N-22가 승인)
            stopped = EndReason(f"{s.assisted_role}_STOPPED")
            _require(reason in (stopped, EndReason(f"{_other(s.assisted_role)}_COMPLETED")))
            if reason is stopped:
                state.consenting -= {s.assisted_role}
                state.update(consent_revision=s.consent_revision + 1)
                effects.append(("stt.close", "SERVER"))
            self._end(state, effects, reason)
            return "state_applied"

        _require(state.consenting == _ROLES, "NO_CONSENT")
        _basis(s, basis, "consent_revision", "mode_revision")
        if kind == "help.link_approved":
            # #6 · G-3: 도움이 끝나면 같은 RTC 통화 안에서도 새 코드가 승인돼야 다음 도움을 연다.
            # 새 help_id·빈 상태로 시작하고 지원 대상을 고정한다. 이전 도움 결과는 help_id가 달라 폐기된다.
            _require(s.help_state == "ENDED", "NOT_APPLICABLE")
            self._active_call(s)
            assisted = p.get("assisted_role", "PARENT")
            _require(assisted in _ROLES)
            state.snapshot = HelpSnapshot(s.pair_id, s.call_id, uuid4().hex, call_state=s.call_state,
                                          consent_revision=s.consent_revision, assisted_role=assisted)
            state.latest_turn_id = state.latest_turn_seq = None
            state.draft_kinds = frozenset()
            state.previous_screen_revision = None
            state.last_screen_meaning = None
            state.observation_fence = 0
            state.call_fence = 0
            state.arrival_id = None
            state.used_epochs = frozenset()
            effects.append(("help.opened", "SERVER"))
            return "state_applied"
        if kind == "speech.final":
            self._active_call(s)
            # 끝난 도움에는 새 코드 승인(help.link_approved) 전까지 발화를 해석하지 않는다
            _require(s.help_state != "ENDED", "NOT_APPLICABLE")
            speaker = p["speaker_role"]
            _require(speaker in _ROLES and p.get("call_id", s.call_id) == s.call_id)
            turn, seq = _id(p["utterance_id"]), _uint(p["utterance_seq"])
            if speaker == _other(s.assisted_role):
                if turn == state.latest_turn_id and seq == state.latest_turn_seq:
                    return "duplicate_ignored"
                _require(state.latest_turn_seq is None or seq > state.latest_turn_seq, "STALE_BASIS")
                state.latest_turn_id, state.latest_turn_seq = turn, seq
                state.draft_kinds = frozenset()
                state.update(utterance_seq=seq)
                effects.append(("turn.interpret", "SERVER"))
            return "state_applied"
        if kind in ("instruction.drafted", "goal.drafted"):
            return self._draft(state, kind, p, basis, effects)

        _require(s.help_state != "ENDED", "ALREADY_ENDED")
        if kind == "projection.started":
            _basis(s, basis, "projection_epoch")
            epoch = _id(p["projection_epoch"])
            if epoch == s.projection_epoch:
                return "duplicate_ignored"
            _require(epoch not in state.used_epochs, "STALE_BASIS")
            state.used_epochs |= {epoch}
            state.previous_screen_revision = None
            state.last_screen_meaning = None
            state.update(projection_epoch=epoch, screen=None, observation_health="GAP")
        elif kind == "screen.observed":
            self._observe(state, p, basis, effects)
        elif kind in ("target.located", "action.assessed"):
            self._instruction_result(state, kind, p, basis, effects)
        elif kind.startswith("arrival."):
            _basis(s, basis, "goal_revision")
            _require(s.mode == "POST_CALL_AGENT" and s.goal is not None, "NOT_APPLICABLE")
            arrival = _id(p["arrival_id"])
            if kind == "arrival.revoked":
                # Loss of observation can invalidate a pending arrival.
                # Revoke only that announcement under the current capture scope.
                _basis(s, basis, "projection_epoch", "screen_revision")
                if state.observation_fence:
                    _require(basis.event_seq is not None and basis.event_seq >= state.observation_fence,
                             "STALE_BASIS")
                _require(state.arrival_id == arrival, "STALE_BASIS")
                state.arrival_id = None
                state.update(help_state="ACTIVE")
            else:
                self._fresh_screen(state, basis)
                if state.arrival_id == arrival:
                    return "duplicate_ignored"
                state.arrival_id = arrival
                state.update(help_state="ARRIVAL_PENDING")
            effects.append((kind, _other(s.assisted_role)))
        else:  # guide.shown / guide.suppressed observations from the assisted device
            _id(p["decision_id"])
            _basis(s, basis, "screen_revision", "projection_epoch")
            if s.mode == "CALL_CHILD_LED":
                self._active_call(s)
                _basis(s, basis, "instruction_revision")
                _require(s.instruction is not None, "NOT_APPLICABLE")
            else:
                _basis(s, basis, "goal_revision")
            if kind == "guide.shown":
                self._fresh_screen(state, basis)
        return "state_applied"

    @staticmethod
    def _active_call(s):
        _require(s.mode == "CALL_CHILD_LED" and s.call_state == "ACTIVE", "STALE_BASIS")

    def _call(self, state, kind, effects, role=None):
        s = state.snapshot
        if kind == "call.ended":
            state.update(call_state="ENDED")
            effects.append(("stt.close", "SERVER"))
            if s.help_state == "ENDED":
                return "state_applied"
            if s.goal is None or state.consenting != _ROLES:
                self._end(state, effects, EndReason.NO_GOAL_AT_HANGUP)
                return "state_applied"
            state.previous_screen_revision = None
            state.latest_turn_id = None
            state.draft_kinds = frozenset()
            state.update(mode="POST_CALL_AGENT", mode_revision=s.mode_revision + 1,
                         help_state="ACTIVE", instruction=None, screen=None, observation_health="GAP")
            effects.extend([("instruction.cleared", s.assisted_role), ("mode.changed", s.assisted_role),
                            ("screen.reobserve", s.assisted_role)])
            return "call_ended_help_active"

        if kind == "call.accepted":
            _require(s.call_state == "RINGING", "NOT_APPLICABLE")
            _require(role != state.inviter, "UNAUTHORIZED")  # 건 쪽은 자기 통화를 수락할 수 없다
            if state.acceptance_confirmed:
                return "duplicate_ignored"
            state.acceptance_confirmed = True
            return "state_applied"
        if kind == "call.invite" and s.call_state == "IDLE":
            state.inviter = role
        source, dest = {
            "call.invite": (("IDLE",), "RINGING"),
            "call.connected": (("IDLE", "RINGING", "RECONNECTING"), "ACTIVE"),
            "call.reconnecting": (("ACTIVE",), "RECONNECTING"),
        }[kind]
        if s.call_state == dest:
            return "duplicate_ignored"
        _require(s.call_state in source, "NOT_APPLICABLE")
        state.update(call_state=dest)
        if dest == "RECONNECTING":
            state.call_fence = s.event_seq + 1
            effects.extend([("stt.pause", "SERVER"), ("guide.suspend", s.assisted_role)])
        elif s.call_state == "RECONNECTING" and state.consenting == _ROLES:
            effects.append(("stt.resume", "SERVER"))
        return "state_applied"

    @staticmethod
    def _end(state, effects, reason):
        s = state.snapshot
        state.latest_turn_id = None
        state.draft_kinds = frozenset()
        state.previous_screen_revision = None
        state.last_screen_meaning = None
        state.arrival_id = None
        state.used_epochs = frozenset()
        state.update(help_state="ENDED", end_reason=s.end_reason or reason,
                     goal=None, instruction=None, screen=None, utterance_seq=None,
                     projection_epoch=None, screen_revision=None, observation_health="STOPPED", trail_cursor=0)
        if s.help_state != "ENDED":
            # help.ended는 N-23이 남은 범위를 정리한 뒤 양쪽에 보낸다.
            effects.extend([("instruction.cleared", s.assisted_role), ("trail.clear", "SERVER"),
                            ("observation.stop", s.assisted_role), ("help.purge", "SERVER")])

    def _draft(self, state, kind, p, basis, effects):
        s = state.snapshot
        self._active_call(s)
        if state.call_fence:
            _require(basis.event_seq is not None and basis.event_seq >= state.call_fence, "STALE_BASIS")
        _basis(s, basis, "utterance_seq")
        _require(s.help_state != "ENDED", "NOT_APPLICABLE")  # 새 도움은 help.link_approved로만 연다
        turn = _id(p["source_utterance_id"])
        _require(turn == state.latest_turn_id, "STALE_BASIS")
        if kind in state.draft_kinds:
            return "duplicate_ignored"
        if kind == "instruction.drafted":
            action, target = p["action"], p["target_key"]
            _require(p["supersedes_previous"] is True and action in ("TAP", "BACK", "SCROLL_DOWN"))
            _require(target is None or target in self._targets)
            revision = s.instruction_revision + 1
            instruction = Instruction(uuid4().hex, revision, turn, target, action,
                                      s.screen.screen_revision if s.screen else None)
            if s.instruction is not None:
                effects.append(("instruction.cleared", s.assisted_role))
            state.previous_screen_revision = None
            state.update(instruction=instruction, instruction_revision=revision, help_state="ACTIVE")
            effects.append(("instruction.accepted", s.assisted_role))
        else:
            # A sibling instruction may already have committed for the same turn.
            # Only the goal scope participates in this comparison.
            _require(self._state.snapshot.goal_revision == basis.goal_revision, "STALE_BASIS")
            goal_id, slots = p["goal_id"], p["approved_slots"]
            _require(goal_id in _GOALS and goal_id in self._slots and type(slots) is dict)
            _require(all(type(k) is str and type(v) in (str, int, bool) for k, v in slots.items()))
            _require(self._slots[goal_id](dict(slots)))
            if s.goal is None or (s.goal.goal_id, s.goal.approved_slots) != (goal_id, slots):
                revision = s.goal_revision + 1
                goal = Goal(goal_id, revision, MappingProxyType(dict(slots)), turn)
                state.update(goal=goal, goal_revision=revision, help_state="ACTIVE")
                effects.append(("goal.updated", s.assisted_role))
        state.draft_kinds |= {kind}
        if not state.audio_evaluated:
            state.audio_evaluated = True
            effects.append(("audio.evaluate", s.assisted_role))
        return "state_applied"

    def _observe(self, state, p, basis, effects):
        s = state.snapshot
        _basis(s, basis, "projection_epoch")
        if state.observation_fence:
            _require(basis.event_seq is not None and basis.event_seq >= state.observation_fence, "STALE_BASIS")
        _require(s.projection_epoch is not None and p.get("projection_epoch", s.projection_epoch) == s.projection_epoch,
                 "STALE_BASIS")
        health = p["observation_health"]
        if health == "ACTIVE":
            kind = p["screen_kind"]
            _require(isinstance(kind, str))
            if kind in SCREEN_BOUNDARIES:
                health = SCREEN_BOUNDARIES[kind]
            elif kind not in self._screen_apps:
                # An external or unclassified screen interrupts continuity. Do not
                # retain an unregistered kind or keep exposing the last known screen.
                health = "GAP"
        if health != "ACTIVE":
            _require(health in ("GAP", "PROTECTED", "STOPPED", "NOT_GRANTED"))
            _basis(s, basis, "screen_revision")
            # No screen content is consumed on a protected or empty observation.
            state.previous_screen_revision = None
            state.last_screen_meaning = None
            state.observation_fence = s.event_seq + 1
            state.update(screen=None, observation_health=health)
            if health in ("STOPPED", "NOT_GRANTED"):
                state.update(projection_epoch=None, trail_cursor=0)
                effects.append(("trail.clear", "SERVER"))
            effects.append(("guide.suspend", s.assisted_role))
            return
        revision = _uint(p["screen_revision"])
        _require(s.screen_revision is None or revision >= s.screen_revision, "STALE_BASIS")
        _require(type(p.get("reversible", False)) is bool)
        before = p.get("from_screen_revision")
        if before is not None:
            _uint(before)
        screen = ScreenState(s.projection_epoch, revision, p["screen_kind"],
                             _codes(p["target_ids"], self._targets), _codes(p["evidence_codes"], self._evidence))
        meaning = (screen.screen_kind, _STATE_EVIDENCE.intersection(screen.evidence_codes))
        if s.screen_revision is not None:
            # An actual capture gap forgets the last meaning, so the first fresh
            # sample must advance even for the same screen. A hangup retains it.
            changed = meaning != state.last_screen_meaning
            _require((revision > s.screen_revision) == changed, "STALE_BASIS")
        if s.screen_revision != revision:
            continuous = (s.screen is not None and before == s.screen_revision and revision == before + 1
                          and screen.evidence_codes
                          and self._screen_apps[s.screen.screen_kind] == self._screen_apps[screen.screen_kind])
            state.previous_screen_revision = before if continuous else None
        state.last_screen_meaning = meaning
        state.update(screen=screen, screen_revision=revision, observation_health="ACTIVE")

    @staticmethod
    def _fresh_screen(state, basis):
        s = state.snapshot
        _basis(s, basis, "projection_epoch", "screen_revision")
        _require(s.screen is not None and s.observation_health == "ACTIVE", "CAPTURE_GAP")
        if state.observation_fence:
            _require(basis.event_seq is not None and basis.event_seq >= state.observation_fence, "STALE_BASIS")

    def _instruction_result(self, state, kind, p, basis, effects):
        s = state.snapshot
        self._active_call(s)
        if state.call_fence:
            _require(basis.event_seq is not None and basis.event_seq >= state.call_fence, "STALE_BASIS")
        _basis(s, basis, "instruction_revision")
        _require(s.instruction is not None, "STALE_BASIS")
        self._fresh_screen(state, basis)
        evidence = _codes(p["evidence_codes"], self._evidence)
        if kind == "target.located":
            location, target = p["location_kind"], p["target_id"]
            _require(location in ("visible", "below_fold", "unknown", "blocked"))
            if location in ("visible", "below_fold"):
                _require(target in self._targets and evidence)
                _require(_uint(p["anchor_screen_revision"]) == s.screen_revision, "STALE_BASIS")
                if location == "visible":
                    _require(target in s.screen.target_ids)
            else:
                _require(target is None)
        else:
            verdict = p["verdict"]
            _require(verdict in ("CORRECT", "WRONG", "UNKNOWN"))
            _require(_uint(p["instruction_revision"]) == s.instruction_revision, "STALE_BASIS")
            _require(_uint(p["to_screen_revision"]) == s.screen_revision, "STALE_BASIS")
            if verdict != "UNKNOWN":
                _require(evidence and _uint(p["from_screen_revision"]) == state.previous_screen_revision,
                         "STALE_BASIS")
            if verdict == "CORRECT":
                state.update(instruction=None)
                effects.append(("instruction.cleared", s.assisted_role))
        effects.append((kind, "SERVER"))
