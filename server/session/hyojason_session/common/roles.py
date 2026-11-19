"""지원 대상(assisted_role) 기준의 역할 계산 (#6, D 노드 역할 중립화).

가족 역할(부모·자녀)과 이번 도움의 지원 대상은 따로다. 지원 대상 기기에서 화면 관찰·안내를 하고,
상대 역할이 지시를 말하고 도움 완료를 누른다. 기본 지원 대상은 부모다.
"""

from __future__ import annotations

from .enums import EndReason, Role

DEFAULT_ASSISTED = Role.PARENT


def counterpart(role: Role) -> Role:
    return Role.CHILD if role is Role.PARENT else Role.PARENT


def completed_reason(by: Role) -> EndReason:
    """도움 완료를 누른 쪽(지원 대상의 상대)의 종료 사유."""
    return EndReason(f"{Role(by).value}_COMPLETED")


def stopped_reason(by: Role) -> EndReason:
    """안내를 끈 쪽(지원 대상)의 종료 사유."""
    return EndReason(f"{Role(by).value}_STOPPED")


def reason_actor(reason: EndReason) -> Role | None:
    """완료·중단 사유를 낸 가족 역할. 동의 철회·목표 없는 통화 종료는 None."""
    for role in Role:
        if reason in (completed_reason(role), stopped_reason(role)):
            return role
    return None


def is_completion(reason: EndReason | None) -> bool:
    return reason is not None and reason.value.endswith("_COMPLETED")


def is_stop(reason: EndReason | None) -> bool:
    return reason is not None and reason.value.endswith("_STOPPED")
