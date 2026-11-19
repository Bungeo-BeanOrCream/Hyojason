"""One local FastAPI process for the voice, control, and model paths."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict
import logging
from secrets import token_urlsafe

from fastapi import FastAPI, HTTPException
from hyojason_session.api import wire
from hyojason_session.api.app import ServerConfig, create_app as create_control_app
from hyojason_session.api.catalog import TARGET_IDS, default_catalog
from hyojason_session.api.hub import SessionHub
from hyojason_session.common.enums import Role, Source
from hyojason_session.common.result import Basis as SessionBasis
from hyojason_session.common.serialize import to_jsonable
from hyojason_session.common.types import ActorContext
from hyojason_voice import app as voice
from hyojason_voice import service as voice_service
from hyojason_voice.call import Operation
from pydantic import BaseModel

from .agent._execution import ExecutionContext
from .agent.b05_workflow import B05Input, B05Workflow, is_current
from .agent.session_adapter import from_session
from .contracts import FinalUtterance, ScreenState

log = logging.getLogger("hyojason.mvp")


class StartCallBody(BaseModel):
    assisted_role: Role = Role.PARENT


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

    def voice_event(self, trace_id: str, event) -> None:
        """Use the identifiers and event IDs issued by the voice service."""
        try:
            if event.type == "call.invite":
                assisted = Role(event.payload.get("assisted_role", Role.PARENT))
                inviter = Role(event.payload["role"])
                control = self.register_call(event.pair_id, event.call_id, trace_id, assisted, inviter=inviter)
                for role in voice_service.consenting_roles(event.pair_id):
                    self._apply(control, "consent.updated", role.value, {"purpose": "analysis", "enabled": True})
                self._announce_session(control)
                return
            control = self.control_for(event.call_id)
            if control.snapshot.pair_id != event.pair_id or control.trace_id != trace_id:
                raise wire.WireError("FORBIDDEN", "UNAUTHORIZED")
            role = event.payload["role"] if event.type == "call.accepted" else "SERVER"
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
            self._retire_connection(control)

    def handle_client(self, actor: ActorContext, message: object) -> dict:
        kind = message.get("type") if isinstance(message, dict) else None
        if voice_service.role_of(actor.pair_id, actor.user_id) != actor.role or kind == "consent.updated":
            event_id = message.get("event_id") if isinstance(message, dict) else None
            return wire.rejected(event_id, "FORBIDDEN", "UNAUTHORIZED")
        reply = super().handle_client(actor, message)
        if reply["type"] == wire.ACK:
            control = self.current_control(actor.pair_id)
            if control is not None:
                if kind == "help.stop" and actor.role in voice_service.consenting_roles(actor.pair_id):
                    revision = voice_service.get_consent(actor).consent_revision
                    voice_service.set_consent(actor, False, revision, call_id=control.snapshot.call_id)
                self._retire_connection(control)
        return reply

    @staticmethod
    def _retire_connection(control) -> None:
        view = control.snapshot
        if view.call_state == "ENDED" and view.help_state == "ENDED":
            voice_service.retire_pair(view.pair_id)

    def _apply(self, control, kind, role, payload=None, **kwargs):
        before = control.snapshot
        update = super()._apply(control, kind, role, payload, **kwargs)
        if update.state.status == "success" and update.state.outcome != "duplicate_ignored":
            after = control.snapshot
            interpret = {effect.kind for effect in update.state.data.side_effects} & {
                "turn.interpret", "goal.interpret"}
            if kind == "speech.final" and interpret:
                # turn.interpret: 지시하는 쪽 발화(지시+목표), goal.interpret: 지원 대상 발화(목표만)
                self._schedule(control, kind, payload, instructs="turn.interpret" in interpret)
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
                                                    and voice_service.consenting_roles(view.pair_id) == set(Role)))

    def _schedule(self, control, event, turn=None, *, before=None, instructs=True) -> None:
        view = self._snapshot(control)
        if not view.processing_allowed:
            return
        speech = None
        recent: tuple[FinalUtterance, ...] = ()
        source = self._source.value
        if turn is not None:
            source = "LIVE" if source == "LIVE" and turn.get("source") == "stt" else "FIXTURE"
            # C의 N-15는 지시하는 쪽을 CHILD, 지원 대상을 PARENT로 본다. D가 화자 역할을 이미 확인했다.
            helper = "PARENT" if control.snapshot.assisted_role == "CHILD" else "CHILD"

            def relative(role: str) -> str:
                return "CHILD" if role == helper else "PARENT"

            fields = {key: turn[key] for key in ("utterance_id", "utterance_seq", "text", "call_id")}
            speech = FinalUtterance(**fields, source=source,
                                    speaker_role="CHILD" if instructs else "PARENT")
            recent = tuple(
                FinalUtterance(utterance_id=item["utterance_id"], utterance_seq=item["utterance_seq"],
                               speaker_role=relative(item["speaker_role"]), text=item["text"],
                               call_id=speech.call_id, source=source)
                for item in control.recent_utterances
            )
        request = B05Input(event=event, snapshot=view, speech=speech, recent_utterances=recent,
                           assisted_role=control.snapshot.assisted_role,
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
        if data.goal_belief:
            # 목표 확률을 먼저 저장하고, 1위가 바뀐 경우에만 목표 초안을 넣는다.
            self._apply(control, "goal.belief", "SERVER", asdict(data.goal_belief), basis=basis)
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
    config = ServerConfig(authenticator=voice_service, service_token=token_urlsafe(),
                          catalog=default_catalog(evidence),
                          source=Source.FIXTURE if fixture else Source.LIVE)
    hub = IntegratedHub(config, workflow or B05Workflow())
    control = create_control_app(config, hub=hub)

    @asynccontextmanager
    async def lifespan(_app):
        previous_sink = voice_service.set_event_sink(hub.voice_event)
        try:
            yield
        finally:
            voice_service.set_event_sink(previous_sink)

    app = FastAPI(title="Hyojason MVP", lifespan=lifespan)
    app.add_exception_handler(wire.WireError, control.exception_handlers[wire.WireError])
    app.add_exception_handler(voice_service.ServiceError, voice.app.exception_handlers[voice_service.ServiceError])

    @app.post("/v1/calls")
    async def start_call(body: StartCallBody | None = None, authorization: str | None = voice.Auth):
        actor = voice_service.actor(authorization)
        current = hub.current_control(actor.pair_id)
        if current is not None and current.snapshot.call_state == "ENDED" and current.snapshot.help_state != "ENDED":
            raise HTTPException(status_code=409, detail="DUPLICATE")
        assisted = body.assisted_role if body is not None else Role.PARENT
        return voice._reply(voice_service.call_op(Operation.START, actor, assisted_role=assisted))

    @app.post("/v1/consent")
    async def update_consent(body: voice.ConsentBody, authorization: str | None = voice.Auth):
        actor = voice_service.actor(authorization)
        result = voice_service.set_consent(actor, body.enabled, body.consent_revision,
                                           frozenset(body.device_capabilities), body.call_id)
        reply = voice._reply(result)
        hub.voice_consent(actor, body.enabled)
        return reply

    @app.post("/v1/pairs/unpair")
    async def unpair(authorization: str | None = voice.Auth):
        actor = voice_service.actor(authorization)
        voice_service.unpair(actor)
        hub.voice_consent(actor, False)
        return {"outcome": "unpaired"}

    overridden = {"/v1/calls", "/v1/consent", "/v1/pairs/unpair"}
    app.router.routes.extend(route for route in voice.app.router.routes
                             if route.path.startswith("/v1/")
                             and not ("POST" in route.methods and route.path in overridden)
                             and (fixture or not route.path.startswith("/v1/dev/")))
    app.router.routes.extend(route for route in control.router.routes
                             if route.path in {"/v1/events", "/v1/help/current", "/healthz"})
    app.state.help_hub = hub
    return app
