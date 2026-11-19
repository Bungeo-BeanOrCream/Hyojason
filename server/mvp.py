"""One local FastAPI process for the voice, control, and model paths."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
import logging
from secrets import token_urlsafe

from fastapi import FastAPI
from hyojason_session.api import wire
from hyojason_session.api.app import ServerConfig, create_app as create_control_app
from hyojason_session.api.catalog import TARGET_IDS, default_catalog
from hyojason_session.api.hub import SessionHub
from hyojason_session.common.enums import Role, Source
from hyojason_session.common.result import Basis as SessionBasis
from hyojason_session.common.serialize import to_jsonable
from hyojason_session.common.types import ActorContext
from hyojason_voice import app as voice

from .agent._execution import ExecutionContext
from .agent.b05_workflow import B05Input, B05Workflow, is_current
from .agent.session_adapter import from_session
from .contracts import FinalUtterance, ScreenState

log = logging.getLogger("hyojason.mvp")


class VoiceAuthentication:
    def authenticate(self, token: str | None) -> ActorContext | None:
        actor = voice.tokens.actor_of(f"Bearer {token}") if token else None
        pair = voice.pairs.pair_of(actor) if actor else None
        role = self.role_of(pair, actor) if pair else None
        return ActorContext(actor, pair, role) if role is not None else None

    def role_of(self, pair_id: str, user_id: str) -> Role | None:
        return voice.pairs.role_of(pair_id, user_id)


class IntegratedHub(SessionHub):
    def __init__(self, config: ServerConfig, workflow: B05Workflow) -> None:
        super().__init__(catalog=config.catalog, approved_slots=config.approved_slots,
                         pairs=config.authenticator, pairing=config.pairing, source=config.source)
        self.workflow = workflow
        self.tasks: set[asyncio.Task] = set()

    def connect(self, actor, queue, loop):
        connection = super().connect(actor, queue, loop)
        control = self.current_control(actor.pair_id)
        if control is not None:
            self._push_views(control)
            if actor.role is Role(control.snapshot.assisted_role):
                self._announce_session(control)
        return connection

    def _announce_session(self, control) -> None:
        view = control.snapshot
        self.deliver(view.pair_id, Role(view.assisted_role), wire.control_event(
            "assist.session", view.pair_id, view.call_id, view.help_id,
            {"trace_id": control.trace_id, "basis": to_jsonable(view.basis)}))

    def voice_event(self, event, trace_id: str) -> None:
        """Use the identifiers and event IDs issued by the voice service."""
        try:
            if event.type == "call.invite":
                assisted = Role(event.payload.get("assisted_role", Role.PARENT))
                control = self.register_call(event.pair_id, event.call_id, trace_id, assisted)
                for role, enabled in voice._consent(event.pair_id).enabled.items():
                    if enabled:
                        self._apply(control, "consent.updated", role.value,
                                    {"purpose": "analysis", "enabled": True})
                self._announce_session(control)
                return
            control = self.control_for(event.call_id)
            if control.snapshot.pair_id != event.pair_id or control.trace_id != trace_id:
                raise wire.WireError("FORBIDDEN", "UNAUTHORIZED")
            role = "CHILD" if event.type == "call.accepted" else "SERVER"
            update = self._apply(control, event.type, role, event.payload, event_id=event.event_id)
            if update.state.status == "error":
                log.warning("voice event rejected: %s %s", event.type, update.state.diagnostics.terminal_error.code)
            elif event.type == "call.ended":
                self._retire_connection(control)
        except wire.WireError as error:
            log.warning("voice event routing failed: %s %s", event.type, error.api_code)

    def voice_consent(self, actor: ActorContext, enabled: bool) -> None:
        control = self.current_control(actor.pair_id)
        if control is not None and control.snapshot.help_state != "ENDED":
            self._apply(control, "consent.updated", actor.role.value,
                        {"purpose": "analysis", "enabled": enabled})

    def handle_client(self, actor: ActorContext, message: object) -> dict:
        if isinstance(message, dict) and message.get("type") == "consent.updated":
            return wire.rejected(message.get("event_id"), "FORBIDDEN", "UNAUTHORIZED")
        reply = super().handle_client(actor, message)
        if isinstance(message, dict) and message.get("type") == "help.stop" and reply["type"] == wire.ACK:
            state = voice._consent(actor.pair_id)
            if state.enabled.get(actor.role):
                state.enabled[actor.role] = False
                state.revision += 1
        if reply["type"] == wire.ACK:
            control = self.current_control(actor.pair_id)
            if control is not None:
                self._retire_connection(control)
        return reply

    @staticmethod
    def _retire_connection(control) -> None:
        view = control.snapshot
        if view.call_state == "ENDED" and view.help_state == "ENDED":
            voice.pairs.members.pop(view.pair_id, None)
            voice.consents.pop(view.pair_id, None)
            voice.pairs.invites = {code: item for code, item in voice.pairs.invites.items()
                                   if item.pair_id != view.pair_id}

    def _apply(self, control, kind, role, payload=None, **kwargs):
        before = control.snapshot
        update = super()._apply(control, kind, role, payload, **kwargs)
        if update.state.status == "success" and update.state.outcome != "duplicate_ignored":
            after = control.snapshot
            if kind == "speech.final" and after.utterance_seq != before.utterance_seq:
                self._schedule(control, kind, payload)
            elif kind == "screen.observed" and after.screen != before.screen and after.screen is not None:
                transition = (before.screen is not None and after.mode == "CALL_CHILD_LED"
                              and after.screen_revision != before.screen_revision)
                self._schedule(control, "observation.transition" if transition else kind,
                               before=before.screen if transition else None)
            elif kind == "instruction.drafted" and after.screen is not None:
                self._schedule(control, "instruction.accepted")
        return update

    def _snapshot(self, control):
        with control._lock:
            view = control.snapshot
            return from_session(view, control.trail, basis=view.basis,
                                processing_allowed=(view.help_state != "ENDED"
                                                    and voice._consent(view.pair_id).view().agent_allowed))

    def _schedule(self, control, event, turn=None, *, before=None) -> None:
        view = self._snapshot(control)
        if not view.processing_allowed:
            return
        speech = None
        source = self._source.value
        if turn is not None:
            source = "LIVE" if source == "LIVE" and turn.get("source") == "stt" else "FIXTURE"
            # D has already verified that the speaker is the helper for this help.
            speech = FinalUtterance(**{**turn, "source": source, "speaker_role": "CHILD"})
        request = B05Input(event=event, snapshot=view, speech=speech,
                           before=ScreenState(**asdict(before)) if before is not None else None,
                           execution=ExecutionContext(trace_id=control.trace_id, source=source))
        task = asyncio.create_task(self._evaluate(control, request))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _evaluate(self, control, request: B05Input) -> None:
        try:
            result = await asyncio.to_thread(self.workflow.run, request,
                                             read_current=lambda: self._snapshot(control))
            log.info("agent %s %s: %s %s", result.diagnostics.source, request.event, result.status,
                     result.outcome or result.diagnostics.terminal_error.code)
            if result.data is not None:
                self._publish_result(control, request, result.data)
        except Exception as error:  # Keep the call alive and avoid logging utterance text.
            if self._source is Source.FIXTURE:
                log.exception("fixture agent integration failed")
            else:
                log.error("agent integration failed: %s", type(error).__name__)

    def _publish_result(self, control, request, data) -> None:
        if not is_current(request, self._snapshot(control)):
            return
        basis = SessionBasis(**asdict(request.snapshot.basis))
        if data.instruction_draft:
            self._apply(control, "instruction.drafted", "SERVER", asdict(data.instruction_draft),
                        basis=basis)
        if data.goal_draft:
            self._apply(control, "goal.drafted", "SERVER", asdict(data.goal_draft), basis=basis)
        if data.target:
            update = self._apply(control, "target.located", "SERVER", asdict(data.target), basis=basis)
            if update.state.status != "success":
                log.warning("target result rejected: %s", update.state.diagnostics.terminal_error.code)
                return
        if data.verdict:
            update = self._apply(control, "action.assessed", "SERVER", asdict(data.verdict), basis=basis)
            if update.state.status != "success":
                return
        if data.arrival:
            self.submit_arrival(basis.call_id, to_jsonable(data.arrival))
        if data.target or data.verdict or data.recovery or data.guide or data.warning:
            current = control.snapshot
            payload = {"basis": to_jsonable(current.basis), "trace_id": control.trace_id,
                       "target": to_jsonable(data.target), "verdict": to_jsonable(data.verdict),
                       "recovery": to_jsonable(data.recovery), "guide": to_jsonable(data.guide),
                       "warning": data.warning}
            self.deliver(current.pair_id, Role(current.assisted_role), wire.control_event(
                "assist.decision", current.pair_id, current.call_id, current.help_id, payload))


def create_app(workflow: B05Workflow | None = None, *, fixture: bool = False) -> FastAPI:
    """Mount control routes beside the voice API. Use one process for its memory state."""
    evidence = ("visible_target", "stable_screen_transition", "alarm_next:tomorrow:06:00",
                *(f"observed:{target}" for target in TARGET_IDS))
    config = ServerConfig(authenticator=VoiceAuthentication(), service_token=token_urlsafe(),
                          catalog=default_catalog(evidence),
                          source=Source.FIXTURE if fixture else Source.LIVE)
    hub = IntegratedHub(config, workflow or B05Workflow())
    control = create_control_app(config, hub=hub)
    app = FastAPI(title="Hyojason MVP")
    voice.app.state.call_event_receiver = hub.voice_event
    voice.app.state.consent_receiver = hub.voice_consent
    app.add_exception_handler(wire.WireError, control.exception_handlers[wire.WireError])
    app.router.routes.extend(route for route in voice.app.router.routes
                             if route.path.startswith("/v1/") and (fixture or not route.path.startswith("/v1/dev/")))
    app.router.routes.extend(route for route in control.router.routes
                             if route.path in {"/v1/events", "/v1/help/current", "/healthz"})
    app.state.help_hub = hub
    return app
