"""One local FastAPI process for the voice, control, and model paths.

This module only assembles the apps and routes B voice-service events into the help session.
Model calls and result publishing live in the session package (hyojason_session.api.assist, #27).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import logging
from secrets import token_urlsafe

from fastapi import FastAPI, HTTPException
from hyojason_session.api import wire
from hyojason_session.api.app import ServerConfig, create_app as create_control_app
from hyojason_session.api.catalog import TARGET_IDS, default_catalog
from hyojason_session.api.assist import AssistedSessionHub
from hyojason_session.common.enums import Role, Source
from hyojason_session.common.serialize import to_jsonable
from hyojason_session.common.types import ActorContext
from hyojason_voice import app as voice
from hyojason_voice import service as voice_service
from hyojason_voice.call import Operation
from pydantic import BaseModel

from .agent.b05_workflow import B05Workflow
from .agent.profiles import TARGETS

log = logging.getLogger("hyojason.mvp")


class StartCallBody(BaseModel):
    assisted_role: Role = Role.PARENT


class IntegratedHub(AssistedSessionHub):
    def __init__(self, config: ServerConfig, workflow: B05Workflow) -> None:
        super().__init__(catalog=config.catalog, approved_slots=config.approved_slots,
                         pairs=config.authenticator, pairing=config.pairing, source=config.source,
                         workflow=workflow)

    def analysis_allowed(self, pair_id: str) -> bool:
        # B 음성 서비스가 두 역할의 분석 동의를 가진다.
        return voice_service.consenting_roles(pair_id) == set(Role)

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


def below_fold_codes() -> tuple[str, ...]:
    """below_fold:<screen>:<target> + scroll_down:<screen>: a device reports a known target that is
    off-screen in a fixed list, so B-05 can offer SCROLL_DOWN (profiles.below_fold_evidence)."""
    return tuple(dict.fromkeys(
        code for target_id, profile in TARGETS.items() if target_id in TARGET_IDS
        for code in (f"below_fold:{profile.screen}:{target_id}", f"scroll_down:{profile.screen}")))


def create_app(workflow: B05Workflow | None = None, *, fixture: bool = False) -> FastAPI:
    """Mount control routes beside the voice API. Use one process for its memory state."""
    # EXPERIMENT (#37): alarm cards the parent device can read (HH:00, HH:30 today/tomorrow).
    alarms = tuple(f"alarm_next:{day}:{hour:02d}:{minute:02d}" for day in ("today", "tomorrow")
                   for hour in range(24) for minute in (0, 30))
    evidence = ("visible_target", "stable_screen_transition", *alarms,
                *(f"observed:{target}" for target in TARGET_IDS), *below_fold_codes())
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
