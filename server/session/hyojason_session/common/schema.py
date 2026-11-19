"""dataclass → JSON Schema(2020-12) 생성기.

서버 타입을 Android(Kotlin)와 맞추기 위한 공유 형식이다 (GitHub 이슈 #2).
계약상 모든 키는 항상 존재하므로 required에 넣고, null 허용 필드는 null 타입을 함께 둔다.
`__omit_none__`에 있는 선택 필드만 required에서 뺀다.
"""

from __future__ import annotations

import dataclasses
import types
import typing
from collections.abc import Mapping
from enum import Enum
from typing import Any

_PRIMITIVES: dict[type, str] = {str: "string", int: "integer", bool: "boolean", float: "number"}


def _with_null(schema: dict[str, Any]) -> dict[str, Any]:
    t = schema.get("type")
    if isinstance(t, str):
        return {**schema, "type": [t, "null"]}
    return {"anyOf": [schema, {"type": "null"}]}


class _Builder:
    def __init__(self) -> None:
        self.defs: dict[str, Any] = {}

    def of(self, tp: Any) -> dict[str, Any]:
        origin = typing.get_origin(tp)
        args = typing.get_args(tp)
        if origin in (typing.Union, types.UnionType):
            non_null = [a for a in args if a is not type(None)]
            inner = self.of(non_null[0]) if len(non_null) == 1 else {"anyOf": [self.of(a) for a in non_null]}
            return _with_null(inner) if len(non_null) < len(args) else inner
        if isinstance(tp, typing.TypeVar):
            return {}
        if origin in (tuple, list):
            item = args[0] if args else Any
            return {"type": "array", "items": self.of(item)}
        if origin is not None and isinstance(origin, type) and issubclass(origin, Mapping):
            return {"type": "object", "additionalProperties": self.of(args[1])}
        if isinstance(tp, type) and issubclass(tp, Enum):
            self.defs.setdefault(tp.__name__, {"type": "string", "enum": [m.value for m in tp]})
            return {"$ref": f"#/$defs/{tp.__name__}"}
        if isinstance(tp, type) and dataclasses.is_dataclass(tp):
            if tp.__name__ not in self.defs:
                self.defs[tp.__name__] = {}  # 재귀 방지용 자리
                self.defs[tp.__name__] = self.object_schema(tp)
            return {"$ref": f"#/$defs/{tp.__name__}"}
        if tp in _PRIMITIVES:
            return {"type": _PRIMITIVES[tp]}
        if tp is Any:
            return {}
        raise TypeError(f"스키마로 바꿀 수 없는 타입: {tp!r}")

    def object_schema(self, cls: type) -> dict[str, Any]:
        hints = typing.get_type_hints(cls)
        omit = set(getattr(cls, "__omit_none__", ()))
        props = {f.name: self.of(hints[f.name]) for f in dataclasses.fields(cls)}
        return {
            "type": "object",
            "properties": props,
            "required": [f.name for f in dataclasses.fields(cls) if f.name not in omit],
            "additionalProperties": False,
        }


def json_schema(cls: type, *, title: str, description: str) -> dict[str, Any]:
    b = _Builder()
    root = b.object_schema(cls)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": title,
        "description": description,
        **root,
        "$defs": dict(sorted(b.defs.items())),
    }
