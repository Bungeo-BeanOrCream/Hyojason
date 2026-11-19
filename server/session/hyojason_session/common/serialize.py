"""dataclass 결과를 JSON 전송용 dict로 바꾼다.

계약상 선언된 키는 값이 없어도 null로 남긴다. StepDiagnostic처럼 `__omit_none__`을
가진 타입만 선택 필드를 생략한다.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from enum import Enum
from typing import Any


def to_jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        omit = set(getattr(type(value), "__omit_none__", ()))
        out: dict[str, Any] = {}
        for f in dataclasses.fields(value):
            item = getattr(value, f.name)
            if item is None and f.name in omit:
                continue
            out[f.name] = to_jsonable(item)
        return out
    if isinstance(value, Mapping):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    return value
