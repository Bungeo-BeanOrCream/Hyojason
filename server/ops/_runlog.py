"""try_* 도구의 에이전트 실행 로그(JSONL). 제출·재현용.

한 줄 = 한 이벤트. 공통 필드: ts(UTC ISO), t_ms(시작 후 ms), trace_id, tool, event.
- run.start / run.end : 입력 발화·옵션, 종료 결과
- zoro.call           : Zoro-1 호출 1회 — 질문 종류·모드·선택지·모델에 보낸 state·선택·전체 확률·지연·revision·출처·상태
- 도구별 단계 이벤트   : goal, screen, candidates, proposal, overlay, user_action 등
비밀값(토큰, 키)은 기록하지 않는다. 엔드포인트는 호스트까지만 남긴다.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import json
import os
import re
from pathlib import Path
import time
from urllib.parse import urlparse


def _plain(v):
    if is_dataclass(v):
        return _plain(asdict(v))
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set, frozenset)):
        return [_plain(x) for x in v]
    return v if isinstance(v, (str, int, float, bool)) or v is None else str(v)


class RunLog:
    def __init__(self, tool: str, trace_id: str, log_dir: str | None, scenario: str | None = None):
        self.tool, self.trace_id, self.t0 = tool, trace_id, time.monotonic()
        # 기본값은 실행 위치와 상관없이 저장소 루트의 logs/agent-runs
        default = Path(__file__).resolve().parents[2] / "logs" / "agent-runs"
        root = Path(log_dir or os.environ.get("HYOJASON_RUN_LOG_DIR") or default)
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        tag = f"_{re.sub(r'[^0-9A-Za-z_-]+', '-', scenario).strip('-')}" if scenario else ""
        self.path = root / f"{stamp}{tag}_{tool}_{trace_id}.jsonl"
        self.scenario = scenario
        self._f = self.path.open("a", encoding="utf-8")
        self.calls = 0

    def event(self, event: str, **data) -> None:
        row = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
               "t_ms": int((time.monotonic() - self.t0) * 1000), "trace_id": self.trace_id,
               "tool": self.tool, "scenario": self.scenario, "event": event, **_plain(data)}
        self._f.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._f.flush()

    def wrap(self, run_model, deadline_ms: int | None = None):
        """InferZoroChoice.run 같은 N-14 실행 함수를 감싸 호출마다 zoro.call을 남긴다.
        deadline_ms를 주면 노드가 정한 제한 시간 대신 그 값으로 호출하고, 실제로 쓴 값을 기록한다."""
        from dataclasses import replace

        def runner(req):
            if deadline_ms is not None:
                req = replace(req, deadline_ms=deadline_ms)
            res = run_model(req)
            self.calls += 1
            out, err = res.data, res.diagnostics.terminal_error
            self.event(
                "zoro.call", n=self.calls, question_kind=req.question_kind, mode=req.mode,
                deadline_ms=req.deadline_ms, choices={c.choice_id: c.description for c in req.choices},
                state=req.context, status=res.status, outcome=res.outcome,
                selected=out.selected_id if out else None,
                probabilities=out.probabilities if out else None,
                latency_ms=out.latency_ms if out else res.diagnostics.duration_ms,
                model_revision=out.model_revision if out else None,
                inference_source=out.inference_source if out else res.diagnostics.source,
                error=err.code if err else None, node_run=res.ref,
            )
            return res
        return runner

    def close(self, **result) -> Path:
        self.event("run.end", zoro_calls=self.calls, **result)
        self._f.close()
        return self.path


def endpoint_host(url: str | None) -> str | None:
    if not url:
        return None
    p = urlparse(url)
    return f"{p.scheme}://{p.hostname}{':' + str(p.port) if p.port else ''}{p.path}"
