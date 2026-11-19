"""N-12. In-memory navigation evidence from authenticated, N-11-approved events."""

from __future__ import annotations

from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Collection, Mapping

from .contracts import Basis, NodeResult, Run, SCREEN_BOUNDARIES


RETAINED_RECORDS = 60
SEARCH_EDGES = 20
_HEALTH = {"ACTIVE", "GAP", "PROTECTED", "STOPPED", "NOT_GRANTED"}
_OUTCOMES = {
    "instruction": {"ACCEPTED"},
    "location": {"VISIBLE", "BELOW_FOLD", "UNKNOWN", "BLOCKED"},
    "assessment": {"CORRECT", "WRONG", "UNKNOWN"},
}


@dataclass(frozen=True)
class NavigationCatalog:
    """Installed app profiles, supplied by the server, never by the event."""

    screen_apps: Mapping[str, str]
    target_ids: Collection[str]
    evidence_codes: Collection[str]
    reversible_pairs: Collection[tuple[str, str]] = ()

    def __post_init__(self):
        screens = {kind: app for kind, app in self.screen_apps.items()
                   if kind not in SCREEN_BOUNDARIES}
        object.__setattr__(self, "screen_apps", MappingProxyType(screens))
        for name in ("target_ids", "evidence_codes", "reversible_pairs"):
            object.__setattr__(self, name, frozenset(getattr(self, name)))


@dataclass(frozen=True)
class ScreenRef:
    screen_kind: str
    projection_epoch: str
    screen_revision: int


@dataclass(frozen=True)
class Transition:
    """N-10's stable samples and its evidence of a semantic transition."""

    before: ScreenRef | None
    after: ScreenRef
    evidence_codes: tuple[str, ...]
    reversible: bool = False


@dataclass(frozen=True)
class InstructionTrace:
    instruction_id: str
    instruction_revision: int
    target_id: str | None
    origin_screen_revision: int | None
    current_screen_revision: int | None
    evidence_codes: tuple[str, ...]
    outcome: str
    kind: str
    projection_epoch: str | None = None


@dataclass(frozen=True)
class TrailEntry:
    from_screen: ScreenRef
    to_screen: ScreenRef
    projection_epoch: str
    evidence_codes: tuple[str, ...]
    reversible: bool
    observed_seq: int


@dataclass(frozen=True)
class Gap:
    observed_seq: int


@dataclass(frozen=True)
class NavigationTrail:
    help_id: str
    records: tuple[TrailEntry | InstructionTrace | Gap, ...] = ()
    # Used to detect missing observations. A gap invalidates the previous anchor.
    anchor: ScreenRef | None = None
    history_truncated: bool = False
    last_event_seq: int | None = None
    gap_after_seq: int | None = None

    @property
    def entries(self) -> tuple[TrailEntry, ...]:
        return tuple(record for record in self.records if isinstance(record, TrailEntry))

    @property
    def directives(self) -> tuple[InstructionTrace, ...]:
        return tuple(record for record in self.records if isinstance(record, InstructionTrace))

    def to_wire_fields(self) -> Mapping[str, object]:
        return {"entries": self.entries, "directives": self.directives, "gap_after_seq": self.gap_after_seq}

    def searchable_entries(self) -> tuple[TrailEntry, ...]:
        """Newest-first verified transitions connected to the current anchor."""
        result = []
        endpoint = self.anchor
        for record in reversed(self.records):
            if isinstance(record, Gap):
                break
            if isinstance(record, TrailEntry):
                # Within a contiguous segment, controls may change revision
                # while the user remains on the same navigable screen.
                target = record.to_screen
                if (endpoint is None or target.projection_epoch != endpoint.projection_epoch
                        or target.screen_kind != endpoint.screen_kind
                        or target.screen_revision > endpoint.screen_revision):
                    break
                result.append(record)
                endpoint = record.from_screen
                if len(result) == SEARCH_EDGES:
                    break
        return tuple(result)


@dataclass(frozen=True)
class EdgeRef:
    help_id: str
    projection_epoch: str
    observed_seq: int


@dataclass(frozen=True)
class NavigationData:
    trail: NavigationTrail
    trace_gap: bool
    recorded_edge: EdgeRef | None
    retained_count: int


def _uint(value):
    return type(value) is int and value >= 0


def _valid_codes(codes, catalog):
    return (isinstance(codes, tuple)
            and all(isinstance(code, str) and code in catalog.evidence_codes for code in codes))


def _valid_screen(ref):
    return (isinstance(ref, ScreenRef) and isinstance(ref.screen_kind, str)
            and isinstance(ref.projection_epoch, str) and bool(ref.projection_epoch)
            and _uint(ref.screen_revision))


def _transition_error(transition, basis, catalog):
    if not isinstance(transition, Transition) or not _valid_screen(transition.after):
        return "INVALID_INPUT"
    before, after = transition.before, transition.after
    if before is not None and not _valid_screen(before):
        return "INVALID_INPUT"
    if type(transition.reversible) is not bool or not _valid_codes(transition.evidence_codes, catalog):
        return "INVALID_INPUT"
    if (after.projection_epoch, after.screen_revision) != (basis.projection_epoch, basis.screen_revision):
        return "STALE_BASIS"
    if before and (before.projection_epoch != after.projection_epoch
                   or before.screen_revision > after.screen_revision):
        return "STALE_BASIS"
    return None


def _directive_error(record, basis, current_id, health, catalog):
    if (not isinstance(record, InstructionTrace) or not isinstance(record.kind, str)
            or not isinstance(record.outcome, str) or record.outcome not in _OUTCOMES.get(record.kind, ())
            or not isinstance(record.instruction_id, str)
            or not record.instruction_id or not _uint(record.instruction_revision)
            or not _valid_codes(record.evidence_codes, catalog)):
        return "INVALID_INPUT"
    if record.target_id is not None and (not isinstance(record.target_id, str)
                                         or record.target_id not in catalog.target_ids):
        return "INVALID_INPUT"
    if any(value is not None and not _uint(value) for value in
           (record.origin_screen_revision, record.current_screen_revision)):
        return "INVALID_INPUT"
    if record.instruction_id != current_id or record.instruction_revision != basis.instruction_revision:
        return "STALE_BASIS"
    # The session retains a revision high-water mark across a capture gap.
    # Only a healthy observation identifies a screen for this directive.
    observed_revision = basis.screen_revision if health == "ACTIVE" else None
    if record.current_screen_revision != observed_revision:
        return "STALE_BASIS"
    if record.projection_epoch is not None and record.projection_epoch != basis.projection_epoch:
        return "STALE_BASIS"
    if record.outcome in {"VISIBLE", "BELOW_FOLD", "CORRECT", "WRONG"}:
        if health != "ACTIVE" or basis.projection_epoch is None or basis.screen_revision is None:
            return "CAPTURE_GAP"
        if not record.evidence_codes or (record.outcome == "VISIBLE" and record.target_id is None):
            return "INVALID_INPUT"
    return None


def _observed_record(transition, trail, catalog, seq):
    """Return the next anchor and either an observed edge, a boundary, or neither."""
    before, after = transition.before, transition.after
    if after.screen_kind not in catalog.screen_apps:
        return None, Gap(seq)
    if after == trail.anchor:
        return after, None
    if before is None:
        return after, Gap(seq) if trail.anchor is not None else None
    # The first healthy sample after a gap cannot prove where the user came from.
    if trail.anchor is None and trail.gap_after_seq is not None:
        return after, None
    if trail.anchor is not None and before != trail.anchor:
        return after, Gap(seq)
    if (not transition.evidence_codes or before.screen_kind not in catalog.screen_apps
            or catalog.screen_apps[before.screen_kind] != catalog.screen_apps[after.screen_kind]):
        return after, Gap(seq)
    if before == after:
        return after, None
    if after.screen_revision != before.screen_revision + 1:
        return after, Gap(seq)
    if before.screen_kind == after.screen_kind:
        return after, None
    reversible = transition.reversible and (before.screen_kind, after.screen_kind) in catalog.reversible_pairs
    return after, TrailEntry(before, after, after.projection_epoch, transition.evidence_codes, reversible, seq)


def record_verified_navigation(
    *, run: Run, current_basis: Basis, trail: NavigationTrail, help_id: str,
    goal_revision: int, observation_health: str, catalog: NavigationCatalog,
    observed_transition: Transition | None = None, directive_record: InstructionTrace | None = None,
    current_instruction_id: str | None = None,
) -> NodeResult:
    """Apply one serialized N-11 decision, comparing only the relevant scope."""
    node = "N-12"
    if (not isinstance(current_basis, Basis) or not isinstance(trail, NavigationTrail)
            or not isinstance(catalog, NavigationCatalog) or not isinstance(help_id, str) or not help_id
            or not _uint(goal_revision) or not isinstance(observation_health, str)
            or observation_health not in _HEALTH or not _uint(current_basis.event_seq)
            or not _uint(current_basis.consent_revision)
            or not all(isinstance(value, str) and value for value in
                       (current_basis.pair_id, current_basis.call_id, current_basis.help_id))):
        return run.error(node, "INVALID_INPUT")
    scope = ["pair_id", "call_id", "help_id", "consent_revision", "goal_revision", "projection_epoch"]
    if observed_transition or observation_health != "ACTIVE" or directive_record:
        scope.append("screen_revision")
    if directive_record is not None:
        if not _uint(current_basis.mode_revision):
            return run.error(node, "INVALID_INPUT")
        scope.extend(("mode_revision", "instruction_revision"))
    if (trail.help_id != help_id or current_basis.help_id != help_id
            or current_basis.goal_revision != goal_revision
            or any(getattr(run.basis, key) != getattr(current_basis, key) for key in scope)
            or (trail.last_event_seq is not None and current_basis.event_seq < trail.last_event_seq)):
        return run.error(node, "STALE_BASIS")
    if observed_transition is not None:
        error = _transition_error(observed_transition, current_basis, catalog)
        if error:
            return run.error(node, error)
    if directive_record is not None:
        error = _directive_error(directive_record, current_basis, current_instruction_id,
                                 observation_health, catalog)
        if error:
            return run.error(node, error)

    seq = current_basis.event_seq
    anchor = trail.anchor
    observation = None
    if observation_health != "ACTIVE" and (directive_record is None or observed_transition is not None):
        if anchor is not None or trail.gap_after_seq is None:
            observation = Gap(seq)
        anchor = None
    elif observed_transition is not None:
        anchor, observation = _observed_record(observed_transition, trail, catalog, seq)

    additions = [] if observation is None else [observation]
    if directive_record is not None:
        directive_record = replace(directive_record, projection_epoch=current_basis.projection_epoch)
        last = None
        # A fresh location after capture resumes is new evidence, even if the
        # same target and screen revision were last seen before the gap.
        for record in reversed(trail.records):
            if isinstance(record, Gap):
                break
            if (isinstance(record, InstructionTrace) and record.instruction_id == directive_record.instruction_id
                    and record.kind == directive_record.kind):
                last = record
                break
        if last != directive_record or isinstance(observation, Gap):
            additions.append(directive_record)
    if additions or anchor != trail.anchor:
        records = trail.records + tuple(additions)
        trail = replace(trail, records=records[-RETAINED_RECORDS:], anchor=anchor, last_event_seq=seq,
                        gap_after_seq=seq if isinstance(observation, Gap) else trail.gap_after_seq,
                        history_truncated=trail.history_truncated or len(records) > RETAINED_RECORDS)
    edge = None
    if isinstance(observation, TrailEntry):
        outcome = "edge_recorded"
        edge = EdgeRef(help_id, observation.projection_epoch, seq)
    elif isinstance(observation, Gap):
        outcome = "gap_recorded"
    elif additions:
        outcome = "directive_recorded"
    else:
        outcome = "unmodified"
    data = NavigationData(trail, isinstance(observation, Gap), edge, len(trail.records))
    return run.success(node, outcome, data)
