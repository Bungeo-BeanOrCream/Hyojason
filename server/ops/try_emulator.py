"""터미널에서 넣은 발화로 Zoro-1이 고른 행동을 실제 Android 에뮬레이터 화면에 안내 테두리로 표시한다. 개인 테스트용.

흐름: 발화 → N-15 목표(Zoro-1) → [반복] adb로 화면 읽기(uiautomator) → 화면 종류·보이는 버튼 판별
      → N-16 후보 → N-17 다음 행동(Zoro-1) → 효자손 앱(debug)의 실제 안내 테두리(AndroidSurfaceHost)로
      그 버튼에 빨간 테두리 + 배너 → 사용자가 직접 누르면 화면 변화를 감지해 다음 단계
      버튼이 화면에 없으면 스크롤 화살표 오버레이를 띄우고 사용자가 스크롤할 때까지 기다린다.
테두리·화살표는 android/app/src/debug의 DebugGuideReceiver가 그린다(효자손 debug 빌드 + 오버레이 권한 필요).
통신(서버·LiveKit·STT)은 거치지 않는다. 화면 판별은 android observer의 DemoProfiles 텍스트 규칙을
단순화해 옮긴 것(한국어·영어 라벨)으로, 앱의 실제 화면 인식(프레임 기반)과는 다르다.

지원 시나리오
  SET_FONT / SET_TIMEOUT : 설정 앱 (팀 목표 카탈로그)
  CLOCK_ALARM            : 시계 앱 알람 (팀 목표 카탈로그, 날짜+시각 필요: "내일 아침 6시에 알람 맞춰줘")
  MAIL_OPEN              : Gmail에서 시연 메일 열기 (팀 목표 카탈로그, 제목에 --mail-subject 글자가 든 메일)
  VOICE_SEARCH           : 구글 마이크 음성 검색 — **이 스크립트 실행 중에만 추가하는 실험 목표** (팀 코드에 없음)

실행(저장소 루트, 에뮬레이터 1대 실행 중):
  python3 -m server.ops.try_emulator "글자가 너무 작아"
  python3 -m server.ops.try_emulator "내일 아침 6시에 알람 맞춰줘"
  python3 -m server.ops.try_emulator "구글에서 말로 검색하고 싶어"
  옵션: --dry-run(표시 없이 좌표만) --tap(테두리 대신 adb로 눌러 진행) --serial --wait-s
필요: ZORO_DECIDE_URL, ZORO_MODEL_REVISION (+ port-forward). adb는 ANDROID_HOME 또는 ~/Library/Android/sdk에서 찾는다.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time
from uuid import uuid4
import xml.etree.ElementTree as ET

from ..agent import enumerate_action_candidate as _enum_mod
from ..agent import interpret_child_turn as _turn_mod
from ..agent import profiles as P
from ..agent._execution import ExecutionContext
from ..agent.choose_post_call_action import ChoosePostCallAction, ChoosePostCallActionInput
from ..agent.enumerate_action_candidate import EnumerateActionCandidates, EnumerateActionCandidatesInput
from ..agent.infer_zoro_choice import InferZoroChoice
from ..agent.interpret_child_turn import InterpretChildTurn, InterpretChildTurnInput
from ..contracts import Basis, FinalUtterance, Goal, NavigationTrail, ScreenState
from ._runlog import RunLog, endpoint_host

RECEIVER = "com.hyojason.app/.debug.DebugGuideReceiver"
SHOW, CLEAR, SCROLL = ("com.hyojason.app.debug.SHOW_GUIDE", "com.hyojason.app.debug.CLEAR_GUIDE",
                       "com.hyojason.app.debug.SHOW_SCROLL_HINT")
SETTINGS_NOTE = ("설정 앱은 HIDE_NON_SYSTEM_OVERLAY_WINDOWS로 타사 오버레이를 숨김(팀 확인 #37): "
                 "창은 만들어졌지만(border=true) 화면에는 보이지 않을 수 있음")
PKG_CLOCK, PKG_GOOGLE = "com.google.android.deskclock", "com.google.android.googlequicksearchbox"
PKG_GMAIL = "com.google.android.gm"

# ---- 실험 목표: 구글 음성 검색 (실행 중에만 등록, 팀 파일은 바꾸지 않음) ----------------------------


def register_voice_search() -> None:
    P.GOAL_LABELS["VOICE_SEARCH"] = "구글 검색에서 마이크를 눌러 음성으로 검색하기"
    P.TARGETS["launcher.google_search"] = P.TargetProfile("home", "구글 검색창", (), "google_search")
    P.TARGETS["google.voice_search"] = P.TargetProfile("google_search", "음성 검색(마이크)", (), "google_listening")
    P.TARGETS["launcher.google_mic"] = P.TargetProfile("home", "검색창의 마이크", (), "google_listening")
    # N-16은 경로 끝에 가까울수록(거리 작을수록) 우선한다. 홈의 마이크는 바로 듣기 화면으로 가므로 맨 끝에 둔다.
    P.GOAL_ROUTES["VOICE_SEARCH"] = ("launcher.google_search", "google.voice_search", "launcher.google_mic")
    P._ARRIVAL_SCREENS["VOICE_SEARCH"] = {"google_listening"}
    _turn_mod._FIXED_SLOTS["VOICE_SEARCH"] = {}
    original = P.required_arrival_evidence

    def required(goal):
        return ("voice_listening",) if goal.goal_id == "VOICE_SEARCH" else original(goal)

    P.required_arrival_evidence = required
    _enum_mod.required_arrival_evidence = required


# ---- 화면 판별 규칙 (target_id → 라벨 후보, 한국어/영어) -----------------------------------------------
SETTINGS = [
    ("settings_display_size_text", (("표시 크기 및 텍스트", "Display size and text"), ("미리보기", "Preview")),
     {"settings.font_size_larger": ("글꼴 크기 늘리기", "글자 크기 늘리기", "Increase font size",
                                    "Make font larger", "늘리기", "크게")}),
    ("settings_screen_timeout", (("화면 자동 꺼짐", "Screen timeout"), ("1분", "1 minute")),
     {"settings.timeout.10m": ("10분", "10 minutes")}),
    ("settings_display", (("밝기", "Brightness"),),
     {"settings.display_size_text": ("표시 크기 및 텍스트", "Display size and text"),
      "settings.screen_timeout": ("화면 자동 꺼짐", "Screen timeout")}),
    ("settings_home", (("네트워크 및 인터넷", "Network & internet"),),
     {"settings.display": ("디스플레이", "Display")}),
]
HOME_TARGETS = {
    "launcher.settings": ("설정", "Settings"),
    "launcher.clock": ("시계", "Clock"),
    "launcher.google_mic": ("음성 검색", "Voice search", "Google 음성 검색", "Start voice search"),
    "launcher.google_search": ("Google 검색", "Google search", "Search", "검색"),
    "launcher.gmail": ("Gmail", "지메일"),
}
GMAIL = {
    "inbox_anchor": ("편지쓰기", "Compose", "메일 검색", "Search in mail", "Search in emails"),
    "detail_anchor": ("답장", "Reply", "전달", "Forward"),
    "inbox": ("받은편지함", "Inbox", "Primary", "기본"),
}
CLOCK = {
    "add": ("알람 추가", "Add alarm"),
    "tab": ("알람", "Alarm", "Alarms"),
    "picker": ("시간 선택", "Select time", "시간 설정", "Set time"),
    "ok": ("확인", "OK"),
}
GOOGLE = {
    "mic": ("음성 검색", "Voice search", "Search by voice", "마이크"),
    "listening": ("말씀하세요", "듣는 중", "Listening", "Speak now", "Try saying"),
}
NAMES = {"home": "홈/앱 목록", "settings_home": "설정", "settings_display": "디스플레이",
         "settings_display_size_text": "표시 크기 및 텍스트", "settings_screen_timeout": "화면 자동 꺼짐",
         "clock_other_tab": "시계(다른 탭)", "clock_alarm_list": "시계 > 알람 목록",
         "clock_alarm_edit": "시계 > 시간 선택", "gmail_inbox": "Gmail 받은편지함",
         "gmail_mail_detail": "Gmail 메일 보기", "gmail_nav_drawer": "Gmail (받은편지함 아님)", "google_search": "구글 검색", "google_listening": "구글 음성 듣는 중"}

Rect = tuple[int, int, int, int]


def find_adb() -> str:
    for c in (shutil.which("adb"),
              os.path.join(os.environ.get("ANDROID_HOME", ""), "platform-tools", "adb"),
              str(Path.home() / "Library/Android/sdk/platform-tools/adb")):
        if c and os.path.exists(c):
            return c
    sys.exit("adb를 찾지 못했습니다. Android Studio SDK의 platform-tools 경로를 ANDROID_HOME으로 지정하세요.")


class Device:
    def __init__(self, adb: str, serial: str | None):
        listed = subprocess.run([adb, "devices"], capture_output=True, text=True, timeout=20).stdout
        devices = [l.split()[0] for l in listed.splitlines()[1:] if l.strip().endswith("device")]
        if serial is None:
            if not devices:
                sys.exit("[adb] 연결된 에뮬레이터가 없어요. Device Manager에서 AVD를 켜고 다시 실행하세요.")
            if len(devices) > 1:
                sys.exit(f"[adb] 에뮬레이터가 {len(devices)}대 켜져 있어요: {', '.join(devices)}\n"
                         "      안내할 부모 기기를 --serial 로 지정하세요 (예: --serial emulator-5554).")
            serial = devices[0]
        elif serial not in devices:
            sys.exit(f"[adb] {serial} 를 찾지 못했어요. 연결된 기기: {', '.join(devices) or '없음'}")
        self.serial = serial
        self.base = [adb, "-s", serial]
        size = re.search(r"(\d+)x(\d+)", self.sh("wm", "size"))
        self.width, self.height = (int(size.group(1)), int(size.group(2))) if size else (1080, 2400)

    def sh(self, *cmd: str) -> str:
        # adb shell은 인자를 다시 기기 셸에서 해석하므로 공백·괄호가 든 배너 문구를 따옴표로 감싼다.
        remote = " ".join(shlex.quote(c) for c in cmd)
        return subprocess.run(self.base + ["shell", remote], capture_output=True, text=True, timeout=20).stdout

    def nodes(self) -> list[tuple[str, Rect]]:
        self.sh("uiautomator", "dump", "/sdcard/hyo_ui.xml")
        xml = subprocess.run(self.base + ["exec-out", "cat", "/sdcard/hyo_ui.xml"],
                             capture_output=True, timeout=20).stdout.decode("utf-8", "replace")
        out: list[tuple[str, Rect]] = []
        try:
            root = ET.fromstring(xml[xml.find("<"):])
        except ET.ParseError:
            return out
        for n in root.iter("node"):
            m = re.findall(r"\d+", n.get("bounds", ""))
            if len(m) != 4:
                continue
            rect = tuple(map(int, m))
            for label in {(n.get("text") or "").strip(), (n.get("content-desc") or "").strip()}:
                if label:
                    out.append((label, rect))  # type: ignore[arg-type]
        return out

    def focused_package(self) -> str:
        m = re.search(r"mCurrentFocus=.*?\s([\w.]+)/", self.sh("dumpsys", "window"))
        return m.group(1) if m else ""

    def setting(self, key: str) -> str:
        return self.sh("settings", "get", "system", key).strip()

    def broadcast(self, action: str, **extras) -> str:
        # 설치 후 한 번도 안 켰거나 강제 종료된 앱(stopped 상태)에도 전달되도록 한다.
        args = ["am", "broadcast", "--include-stopped-packages", "-n", RECEIVER, "-a", action]
        for k, v in extras.items():
            args += (["--ei", k, str(v)] if isinstance(v, int) else ["--es", k, str(v)])
        reply = self.sh(*args)
        m = re.search(r'data="([^"]*)"', reply)
        return m.group(1) if m else (reply.strip() or "응답 없음")


def locate(nodes, words) -> Rect | None:
    for w in words:
        for t, rect in nodes:
            if t == w or (len(w) > 2 and not w.isascii() and w in t) or (w.isascii() and len(w) > 9 and t.startswith(w + " ")):
                return rect
    return None


def has(nodes, words) -> bool:
    return any(w in t for w in words for t, _ in nodes)


MAIL_SUBJECT = "효자손"  # main()에서 --mail-subject로 바꾼다


def classify(dev: Device) -> tuple[str, dict[str, Rect], list[tuple[str, Rect]]]:
    nodes = dev.nodes()
    pkg = dev.focused_package()
    if "launcher" in pkg:
        return "home", {k: r for k, w in HOME_TARGETS.items() if (r := locate(nodes, w))}, nodes
    if pkg == PKG_CLOCK:
        if (picker := locate(nodes, CLOCK["picker"])) or (has(nodes, ("취소", "Cancel")) and locate(nodes, CLOCK["ok"])):
            found = {"clock.save_alarm": r} if (r := locate(nodes, CLOCK["ok"])) else {}
            found["clock.time_picker"] = picker or (0, dev.height // 4, dev.width, dev.height // 2)
            return "clock_alarm_edit", found, nodes
        if (add := locate(nodes, CLOCK["add"])):
            return "clock_alarm_list", {"clock.add_alarm": add}, nodes
        return "clock_other_tab", ({"clock.alarm_tab": r} if (r := locate(nodes, CLOCK["tab"])) else {}), nodes
    if pkg == PKG_GMAIL:
        if has(nodes, GMAIL["detail_anchor"]):
            return "gmail_mail_detail", {}, nodes
        if has(nodes, GMAIL["inbox_anchor"]):
            mail = next((r for t, r in nodes if MAIL_SUBJECT in t), None)
            return "gmail_inbox", ({"gmail.demo_mail": mail} if mail else {}), nodes
        return "gmail_nav_drawer", ({"gmail.inbox": r} if (r := locate(nodes, GMAIL["inbox"])) else {}), nodes
    if pkg == PKG_GOOGLE:
        if has(nodes, GOOGLE["listening"]):
            return "google_listening", {}, nodes
        return "google_search", ({"google.voice_search": r} if (r := locate(nodes, GOOGLE["mic"])) else {}), nodes
    for kind, anchors, targets in SETTINGS:
        if all(has(nodes, group) for group in anchors):
            return kind, {k: r for k, w in targets.items() if (r := locate(nodes, w))}, nodes
    return "unknown", {}, nodes


def check_overlay(dev: "Device") -> int:
    """테두리가 안 보일 때 원인을 순서대로 확인한다."""
    ok = True
    print(f"[1] 기기: {dev.serial}  화면 {dev.width}x{dev.height}")
    pkg = dev.sh("pm", "list", "packages", "com.hyojason.app").strip()
    print(f"[2] 효자손 앱 설치: {'예' if pkg else '아니요 → ./gradlew :app:installDebug'}")
    ok &= bool(pkg)
    recv = "DebugGuideReceiver" in dev.sh("dumpsys", "package", "com.hyojason.app")
    print(f"[3] DebugGuideReceiver 포함(debug 빌드, 최신 코드): {'예' if recv else '아니요 → 이 브랜치로 :app:installDebug 다시'}")
    ok &= recv
    op = dev.sh("appops", "get", "com.hyojason.app", "SYSTEM_ALERT_WINDOW").strip()
    allowed = "allow" in op
    print(f"[4] 오버레이 권한: {op or '응답 없음'}"
          + ("" if allowed else "  → adb shell appops set com.hyojason.app SYSTEM_ALERT_WINDOW allow"))
    ok &= allowed
    front = dev.focused_package()
    print(f"[5] 지금 앞 화면 앱: {front or '알 수 없음'}"
          + ("  ⚠ 설정 앱은 타사 오버레이를 숨김(#37) → 홈이나 다른 앱에서 확인" if front == "com.android.settings" else ""))
    w, h = dev.width, dev.height
    reply = dev.broadcast(SHOW, left=w // 8, top=h * 3 // 8, right=w * 7 // 8, bottom=h * 4 // 8,
                          text="효자손 테두리 점검")
    print(f"[6] 테스트 테두리 요청: {reply}")
    if "border=true" not in reply:
        ok = False
        print("    → 수신기가 응답하지 않거나 실패. 위 2~4를 먼저 고치세요. 'no_overlay_permission'이면 4번.")
    else:
        print("    → 화면 가운데에 빨간 테두리와 위쪽 검은 배너가 보이면 정상. 5초 뒤 지웁니다.")
        time.sleep(5)
        dev.broadcast(CLEAR)
    print("점검 결과:", "통과" if ok else "문제 있음")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("text", nargs="?", help="자녀 발화 (--check 일 때는 생략)")
    ap.add_argument("--check", action="store_true", help="테두리 표시 환경만 점검하고 종료 (모델 호출 없음)")
    ap.add_argument("--serial", help="에뮬레이터가 여러 대일 때 (예: emulator-5554)")
    ap.add_argument("--tap", action="store_true", help="테두리 대신 adb로 직접 눌러 진행")
    ap.add_argument("--dry-run", action="store_true", help="표시 없이 좌표만 출력")
    ap.add_argument("--wait-s", type=int, default=60, help="안내 표시 후 사용자 조작을 기다릴 시간")
    ap.add_argument("--deadline-ms", type=int, default=5000)
    ap.add_argument("--max-steps", type=int, default=10)
    ap.add_argument("--log-dir", help="실행 로그(JSONL) 폴더 (기본 logs/agent-runs)")
    ap.add_argument("--scenario", help="로그에 붙일 시나리오 이름 (예: S1-font, S2-alarm)")
    ap.add_argument("--mail-subject", default="효자손", help="MAIL_OPEN에서 시연 메일로 볼 제목 글자 (기본 '효자손')")
    args = ap.parse_args(argv)

    if args.check:
        return check_overlay(Device(find_adb(), args.serial))
    if not args.text:
        ap.error("자녀 발화를 넣으세요 (또는 --check)")
    register_voice_search()
    global MAIL_SUBJECT
    MAIL_SUBJECT = args.mail_subject
    model = InferZoroChoice.from_environment()
    if not model.endpoint or not model.model_revision:
        ap.error("ZORO_DECIDE_URL과 ZORO_MODEL_REVISION을 설정하세요")
    ex = ExecutionContext(trace_id=f"emu-{uuid4().hex[:8]}", source="LIVE")
    log = RunLog("try_emulator", ex.trace_id, args.log_dir, scenario=args.scenario)
    runner = log.wrap(model.run, deadline_ms=args.deadline_ms)
    dev = Device(find_adb(), args.serial)
    font0 = dev.setting("font_scale")
    print(f"[기기] 화면 {dev.width}x{dev.height}, font_scale={font0}")
    print(f"[로그] {log.path}")
    log.event("run.start", text=args.text, endpoint=endpoint_host(model.endpoint), model_revision=model.model_revision,
              device={"serial": dev.serial, "width": dev.width, "height": dev.height, "font_scale": font0},
              options={"mail_subject": args.mail_subject, "tap": args.tap, "dry_run": args.dry_run, "wait_s": args.wait_s, "deadline_ms": args.deadline_ms})

    def finish(code: int, result: str, **extra) -> int:
        print(f"[로그] {log.close(result=result, **extra)}")
        return code

    speech = FinalUtterance(utterance_id="u1", utterance_seq=1, speaker_role="CHILD",
                            text=args.text, call_id="emu-call", source="LIVE")
    turn = InterpretChildTurn(infer_zoro_choice=runner).run(InterpretChildTurnInput(
        mode="CALL_CHILD_LED", speech=speech, existing_goal=None, goal_catalog_revision=P.POLICY_REVISION,
        basis=Basis(call_id="emu-call", utterance_seq=1, mode_revision=1), execution=ex))
    d = turn.data
    if d and d.goal_belief:
        top = sorted(d.goal_belief.probabilities.items(), key=lambda kv: kv[1], reverse=True)[:3]
        print("[목표 확률] " + ", ".join(f"{g} {p:.3f}" for g, p in top))
    if not d or not d.goal_draft:
        err = turn.diagnostics.terminal_error
        skipped = turn.diagnostics.steps.get("goal_slots") if hasattr(turn.diagnostics, "steps") else None
        print(f"[목표] 채택된 목표 없음 ({turn.outcome}{', ' + err.code if err else ''})")
        print("       알람은 날짜와 시각이 모두 필요해요(예: '내일 아침 6시'). 확률이 0.70 미만이어도 채택되지 않아요.")
        log.event("goal", adopted=False, outcome=turn.outcome, error=err.code if err else None,
                  probabilities=d.goal_belief.probabilities if d and d.goal_belief else None)
        return finish(1, "no_goal")
    goal = Goal(goal_id=d.goal_draft.goal_id, goal_revision=1,
                approved_slots=d.goal_draft.approved_slots, source_utterance_id="u1")
    print(f"[목표] {goal.goal_id} ({P.GOAL_LABELS[goal.goal_id]}) 확률 {d.confidence:.3f} 슬롯 {goal.approved_slots}")
    log.event("goal", adopted=True, goal_id=goal.goal_id, confidence=d.confidence, slots=goal.approved_slots,
              probabilities=d.goal_belief.probabilities if d.goal_belief else None,
              experimental=goal.goal_id == "VOICE_SEARCH", route=P.GOAL_ROUTES.get(goal.goal_id))
    if goal.goal_id not in ("SET_FONT", "SET_TIMEOUT", "CLOCK_ALARM", "VOICE_SEARCH", "MAIL_OPEN"):
        print("  이 스크립트는 설정(글자·자동 꺼짐), 시계 알람, Gmail 메일 열기, 구글 음성 검색만 화면 판별을 지원합니다.")
        return finish(1, "unsupported_goal")
    print("[경로] " + " → ".join(P.TARGETS[t].label for t in P.GOAL_ROUTES[goal.goal_id]))

    enum, choose = EnumerateActionCandidates(), ChoosePostCallAction(infer_zoro_choice=runner)
    trail = NavigationTrail(entries=(), directives=(), gap_after_seq=None)
    token = P.alarm_token(goal.approved_slots) if goal.goal_id == "CLOCK_ALARM" else None
    time_set = False

    def evidence_for(kind: str, nodes) -> tuple[str, ...]:
        if goal.goal_id == "SET_FONT" and font0 not in ("", "null") and dev.setting("font_scale") not in ("", "null") \
                and float(dev.setting("font_scale")) > float(font0):
            return ("font_scale_changed",)
        if goal.goal_id == "SET_TIMEOUT" and dev.setting("screen_off_timeout") == "600000":
            return ("timeout_10m_selected",)
        if goal.goal_id == "CLOCK_ALARM" and token:
            hh, mm = goal.approved_slots["time"].split(":")
            h12 = int(hh) % 12 or 12
            shown = has(nodes, (f"{h12}:{mm}", f"{int(hh)}:{mm}"))
            if kind == "clock_alarm_list" and shown and time_set:
                return ("alarm_card_active", f"alarm_next:{token}")  # 날짜(내일)는 화면에서 확인하지 않음
            if kind == "clock_alarm_edit" and time_set:
                return (f"alarm_edit:{token}",)
        if goal.goal_id == "MAIL_OPEN" and kind == "gmail_mail_detail" and has(nodes, (MAIL_SUBJECT,)):
            return ("mail_detail_open",)
        if goal.goal_id == "VOICE_SEARCH" and kind == "google_listening":
            return ("voice_listening",)
        return ()

    def clear():
        if not args.dry_run:
            dev.broadcast(CLEAR)

    for step in range(1, args.max_steps + 1):
        kind, found, nodes = classify(dev)
        ev = evidence_for(kind, nodes)
        screen = ScreenState(projection_epoch="emu", screen_revision=step, screen_kind=kind,
                             target_ids=tuple(found), evidence_codes=ev, observation_health="STABLE")
        print(f"\n[{step}] 화면: {NAMES.get(kind, kind)}  보이는 대상: {list(found) or '없음'}  근거: {ev or '없음'}")
        log.event("screen", step=step, package=dev.focused_package(), screen_kind=kind,
                  targets={k: list(r) for k, r in found.items()}, evidence=ev, observation="adb uiautomator text")
        if P.arrival_evidence(goal, screen):
            print("    ✅ 도착 근거 확인 — 목표 완료")
            clear()
            return finish(0, "arrived", steps=step, evidence=ev)
        if kind == "unknown":
            print("    화면을 판별하지 못했습니다(지원 범위 밖이거나 글자가 다름).")
            print(f"    현재 앱: {dev.focused_package() or '알 수 없음'}")
            print("    화면에서 읽은 글자: " + " | ".join(t for t, _ in nodes[:40]))
            return finish(1, "unknown_screen", labels=[t for t, _ in nodes[:40]])
        basis = Basis(help_id="emu-help", mode_revision=2, goal_revision=1, projection_epoch="emu", screen_revision=step)
        cs = enum.run(EnumerateActionCandidatesInput(
            mode="POST_CALL_AGENT", screen=screen, instruction=None, goal=goal, trail=trail,
            policy_revision=P.POLICY_REVISION, basis=basis, execution=ex)).data
        if not cs or not cs.candidates:
            print("    화면에서 읽은 글자: " + " | ".join(t for t, _ in nodes[:30]))
            msg = "앱 목록을 위로 밀어 올려 주세요" if kind == "home" else "아래로 스크롤해 주세요"
            print(f"    목표로 가는 버튼이 지금 화면에 안 보여요 → 화살표 표시, {msg} (최대 {args.wait_s}초 대기)")
            log.event("candidates", step=step, candidates=[], coverage=cs.coverage if cs else None)
            if not args.dry_run:
                hint = dev.broadcast(SCROLL, direction='down' if kind != 'home' else 'up', text=msg)
                print(f"    → 스크롤 화살표: {hint}")
                log.event("overlay", step=step, kind="scroll_hint", text=msg, receiver_reply=hint,
                          visibility_note=SETTINGS_NOTE if dev.focused_package() == "com.android.settings" else None)
            seen = (kind, set(found))
            for _ in range(args.wait_s):
                time.sleep(1)
                k2, f2, _n = classify(dev)
                if (k2, set(f2)) != seen:
                    break
            else:
                print("    시간 안에 버튼이 나타나지 않아 종료합니다.")
                clear()
                return finish(1, "scroll_timeout", steps=step)
            log.event("user_action", step=step, kind="scrolled_or_screen_changed")
            clear()
            continue
        res = choose.run(ChoosePostCallActionInput(mode="POST_CALL_AGENT", candidate_set=cs, goal=goal,
                                                   basis=basis, threshold_profile="p0", execution=ex))
        out = res.data
        if out is None or out.proposal is None:
            err = res.diagnostics.terminal_error
            print(f"    [Zoro-1] 안내 보류: {out.reason if out else (err.code if err else res.status)}")
            log.event("proposal", step=step, proposal=None, reason=out.reason if out else None,
                      selected=out.selected_id if out else None, error=err.code if err else None)
            clear()
            return finish(1, "held", steps=step)
        p = out.proposal
        rect = found.get(p.target_id)
        label = P.TARGETS[p.target_id].label
        print(f"    [코드 후보] {', '.join(c.choice_id for c in cs.candidates)}")
        print(f"    [Zoro-1] 다음 행동: {p.action} '{label}' 확신 {p.confidence:.3f} → 화면 영역 {rect}")
        log.event("candidates", step=step, candidates=[(c.choice_id, c.path_distance) for c in cs.candidates],
                  coverage=cs.coverage)
        log.event("proposal", step=step, action=p.action, target_id=p.target_id, label=label,
                  confidence=p.confidence, rect=list(rect) if rect else None, decision_id=p.decision_id)
        if args.dry_run or rect is None:
            return finish(0, "dry_run" if args.dry_run else "no_rect", steps=step)
        if p.action == "INPUT_HINT" and p.target_id == "clock.time_picker":
            hint = f"{goal.approved_slots['time']}으로 시각을 맞춰 주세요 (Zoro-1 {p.confidence:.2f})"
            reply = dev.broadcast(SHOW, left=rect[0], top=rect[1], right=rect[2], bottom=rect[3], text=hint)
            print(f"    → 시각 입력 안내: {reply}")
            log.event("overlay", step=step, kind="border+banner", target_id=p.target_id, text=hint, receiver_reply=reply)
            input(f"    에뮬레이터에서 시각을 {goal.approved_slots['time']}으로 맞춘 뒤(아직 '확인'은 누르지 말고) Enter ")
            log.event("user_action", step=step, kind="time_set_confirmed_in_terminal")
            time_set = True
            clear()
            continue
        if args.tap:
            dev.sh("input", "tap", str((rect[0] + rect[2]) // 2), str((rect[1] + rect[3]) // 2))
            log.event("user_action", step=step, kind="adb_tap", target_id=p.target_id)
            time.sleep(1.5)
            continue
        status = dev.broadcast(SHOW, left=rect[0], top=rect[1], right=rect[2], bottom=rect[3],
                               text=f"'{label}'을(를) 눌러 주세요 (Zoro-1 {p.confidence:.2f})")
        print(f"    → 빨간 테두리 표시: {status}")
        log.event("overlay", step=step, kind="border+banner", target_id=p.target_id, receiver_reply=status,
                  visibility_note=SETTINGS_NOTE if dev.focused_package() == "com.android.settings" else None)
        if "border=true" not in status:
            print("      테두리를 못 그렸어요. 효자손 debug 앱 설치·오버레이 권한을 확인하세요.")
            return finish(1, "overlay_failed", steps=step)
        print(f"    에뮬레이터에서 테두리 친 버튼을 직접 눌러 보세요 (최대 {args.wait_s}초 대기)")
        before = (kind, set(found))
        for _ in range(args.wait_s):
            time.sleep(1)
            k2, f2, n2 = classify(dev)
            if (k2, set(f2)) != before or evidence_for(k2, n2) != ev:
                break
        else:
            print("    시간 안에 화면 변화가 없어 종료합니다.")
            clear()
            return finish(1, "tap_timeout", steps=step)
        log.event("user_action", step=step, kind="screen_changed_after_guide")
        clear()
        time.sleep(0.5)
    print("최대 단계 수에 도달했습니다.")
    clear()
    return finish(1, "max_steps")


if __name__ == "__main__":
    sys.exit(main())
