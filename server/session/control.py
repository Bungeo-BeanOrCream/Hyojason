"""Apply authenticated N-11 events and their N-12 evidence in one critical section."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock

from .contracts import Basis, NodeResult, ResultRef, Run, Source
from .help_state import Actor, Event, HelpSession
from .navigation import (
    InstructionTrace, NavigationCatalog, NavigationTrail, ScreenRef, Transition,
    record_verified_navigation,
)


@dataclass(frozen=True)
class Update:
    state: NodeResult
    navigation: NodeResult | None


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
                 catalog: NavigationCatalog, approved_slots, source: Source = "LIVE"):
        self._session = HelpSession(
            pair_id, call_id, help_id, screen_apps=catalog.screen_apps,
            target_ids=catalog.target_ids, evidence_codes=catalog.evidence_codes,
            approved_slots=approved_slots,
        )
        self._catalog = catalog
        self._source = source
        self._trail = NavigationTrail(help_id)
        self._lock = RLock()

    @property
    def snapshot(self):
        with self._lock:
            return self._session.snapshot

    @property
    def trail(self) -> NavigationTrail:
        with self._lock:
            return self._trail

    def apply(self, event: Event, actor: Actor, basis: Basis, *,
              trace_id: str | None = None, inputs: tuple[NodeResult, ...] = ()) -> Update:
        """Publish returned N-11 effects only after this call succeeds."""
        with self._lock:
            refs = tuple(item.diagnostics.ref for item in inputs)
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
            if state.status == "success" and state.outcome != "duplicate_ignored":
                self._trail = trail
                self._source = source
            return Update(state, navigation)

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
