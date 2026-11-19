# 인수인계: 통신 없이 Zoro-1 판단 → 에뮬레이터 빨간 테두리 확인 도구

작성: 2026-11-20 · 담당 B(민서) → 팀원 · 상태: **개인 테스트 도구, 커밋 전**

이 문서는 사람과 코딩 에이전트가 함께 읽도록 썼다. 에이전트는 "에이전트 작업 규칙"을 먼저 따른다.

## 1. 왜 만들었나

- 앱의 **코드 만들기**를 누르면 앱이 꺼졌다. Logcat 원인: `java.net.ConnectException: Failed to connect to /10.0.2.2:8000` (Mac에 백엔드가 없음) + `Api.kt`의 `call()`이 예외를 잡지 않아 크래시(`RtcToken.kt:39` ← `Api.kt:51`).
- 통신(서버·LiveKit·STT)과 상관없이 **Zoro-1 판단 → 부모 화면 안내 테두리**가 실제로 어떻게 동작하는지 보고 싶어서, 기존 판단 노드(N-14~N-17)를 그대로 부르는 실행 도구를 만들었다. **판단 로직(server/agent)은 한 줄도 바꾸지 않았다.**

## 2. 추가·수정 파일

| 파일 | 종류 | 역할 |
|---|---|---|
| `server/ops/try_instruction.py` | 새 파일 | 문장 → N-15(목표, Zoro-1 GOAL). 대화형 다중 발화, `--fake`, `--show-payload`, `--deadline-ms` |
| `server/ops/try_navigation.py` | 새 파일 | 문장 → 목표 → `profiles.py` 시연 화면으로 만든 **가짜 화면**에서 N-16 후보 + N-17(Zoro-1 NEXT_ACTION) 단계별 시뮬레이션 |
| `server/ops/try_emulator.py` | 새 파일 | 문장 → 목표 → **실제 에뮬레이터 화면**을 adb(uiautomator)로 읽어 화면 판별 → N-16/N-17 → 고른 버튼에 앱의 실제 테두리 표시 요청 → 사용자가 누르면 다음 단계 → 기기 설정값(font_scale / screen_off_timeout) 변화로 완료 판정 |
| `android/app/src/debug/kotlin/com/hyojason/app/debug/DebugGuideReceiver.kt` | 새 파일 (debug 전용) | adb broadcast로 받은 좌표에 `overlay/surface/AndroidSurfaceHost.showBorder/showBanner`(앱의 실제 테두리 코드)로 그림 |
| `android/app/src/debug/AndroidManifest.xml` | 수정 (+4줄) | 위 수신기 등록 |

- Python 도구는 **표준 라이브러리만** 사용, Python 3.11+ (`typing.Never`). venv 불필요.
- Android 변경은 `src/debug`에만 있어 release 빌드에 포함되지 않는다.

## 3. 확인된 것 / 확인 안 된 것

### 확인됨
- `server/agent/tests` 22개 통과(합성 응답).
- 실제 Zoro-1(L40S, revision `bf1b76b9b4df03b05fcbb73444e1cd4db0f566da`) 호출 결과 (`try_instruction.py`, 각 1회):
  - "글자가 너무 작아" → `SET_FONT` 0.967 (NONE 0.032) → 채택
  - "내일 아침 여섯 시에 일어나야해" → `CLOCK_ALARM` 0.791 (NONE 0.204) → 채택, 슬롯 `{"day":"tomorrow","time":"06:00"}`(슬롯은 규칙이 채움)
- Brev 인스턴스 `hyojason-zoro`(org `TL-Hackathon-team1`, ID `rq2jv3z7t`) 내부 포트:
  - `127.0.0.1:30001` — `decider_http.py` (Zoro-1 `/v1/decide`, 작업 폴더 `~/workspace/hyojason-zoro-smoke`)
  - `127.0.0.1:18002` — `server.mvp` 통합 백엔드 (`~/workspace/hyojason-mvp/releases/9a15587-integration1`)
  - `0.0.0.0:8888` — JupyterLab (`ljm03090` 계정만 접근)
- VM 백엔드(18002) 프로세스 환경에 `LIVEKIT_*`, `RTZR_*` **없음** → 통화 토큰(`rtc-token`) 503, STT worker 미실행. 페어·동의까지만 가능.
- `server.mvp`는 `.env`를 자동으로 읽지 않는다(셸 환경 변수만). `/v1/dev/speech-final`(텍스트 발화 주입)은 `create_app(fixture=True)`일 때만 열린다.
- `try_emulator.py` 화면 판별 단위 확인(합성 노드). 영어 AVD 설정 첫 화면에서 실제로 `com.android.settings`와 화면 글자를 읽는 것까지 확인.

### 확인 안 됨 (다음 사람이 확인)
- `DebugGuideReceiver`가 들어간 debug 빌드의 **컴파일·설치 성공 여부**(작성 환경에 Android SDK 없음).
- **설정 앱 위에서 테두리가 보이는지.** 사용자 보고: 설정 화면에 들어가면 빨간 박스가 안 보임. 가설: Android 12+ 설정 앱의 비시스템 오버레이 숨김(탭재킹 방지). **미검증.** 맞다면 팀 앱의 실제 시연 구조(설정 앱 위 테두리)에도 같은 문제가 있다 → A 담당과 공유 필요.
- 테두리 세로 위치: 상태 표시줄 높이만큼 보정(`y_offset`). 실제로 맞는지 미확인.
- "글자 크게" 버튼의 실제 라벨(영어 추정 `Increase font size`). 해당 화면에서 멈출 수 있음.
- 같은 문장 반복 시 확률 변동, 간접 표현에서의 안정성.

## 4. 실행 환경 준비 (Mac)

```bash
# Brev CLI (처음 1회)
brew install brevdev/homebrew-brev/brev
brev login
brev ls --all            # hyojason-zoro RUNNING 확인 (본인 인스턴스가 아니면 --all 필요)
brev refresh             # SSH 설정 갱신. brev shell이 안 되면 인스턴스 소유자가 SSH 접근을 공유해야 함
```

터미널 1 — Zoro-1 포트 연결(켜 둠)
```bash
brev port-forward hyojason-zoro -p 9000:30001
```

터미널 2 — 앱 debug 빌드·설치(에뮬레이터 켠 상태)
```bash
cd <repo>/android
export JAVA_HOME=$(/usr/libexec/java_home -v 21)   # Gradle 8.14는 Java 25 불가. Android Studio 내장 JBR이 25일 수 있음
./gradlew --stop && ./gradlew :app:installDebug
ADB=~/Library/Android/sdk/platform-tools/adb
$ADB shell appops set com.hyojason.app SYSTEM_ALERT_WINDOW allow
$ADB shell dumpsys package com.hyojason.app | grep -i DebugGuide     # 수신기 설치 확인
$ADB shell am broadcast -n com.hyojason.app/.debug.DebugGuideReceiver \
  -a com.hyojason.app.debug.SHOW_GUIDE --ei left 100 --ei top 400 --ei right 900 --ei bottom 550 --es text "테스트"
# 기대: Broadcast completed: result=0, data="border=true banner=true"
```

터미널 3 — 실행(저장소 루트)
```bash
export ZORO_DECIDE_URL=http://localhost:9000/v1/decide
export ZORO_MODEL_REVISION=bf1b76b9b4df03b05fcbb73444e1cd4db0f566da
# ZORO_DECIDE_TOKEN: VM의 18002 백엔드 환경에 있으면 같은 값을 쓴다. 값은 채팅·문서·커밋에 남기지 않는다.
python3 -m server.ops.try_instruction --deadline-ms 5000 "글자가 너무 작아"
python3 -m server.ops.try_navigation "글자가 너무 작아"
python3 -m server.ops.try_emulator "글자가 너무 작아"     # 에뮬레이터를 설정 첫 화면에 두고 실행
```
- `try_emulator.py` 옵션: `--dry-run`(표시 없이 좌표만), `--tap`(테두리 대신 adb로 눌러 진행), `--serial`, `--wait-s`.
- 판별 실패 시 현재 앱과 화면에서 읽은 글자를 출력한다 → 그 글자로 `SCREENS` 표를 고친다.
- 지원 범위: 설정 앱의 글자 크기(`SET_FONT`)·화면 자동 꺼짐(`SET_TIMEOUT`), 시계 알람(`CLOCK_ALARM`, 날짜+시각 필요: "내일 아침 6시에 알람 맞춰줘"),
  구글 마이크 음성 검색(`VOICE_SEARCH`). 한국어/영어 라벨.
- `VOICE_SEARCH`는 **팀 목표 카탈로그에 없는 실험 목표**다. `try_emulator.py`가 실행 중에만 `profiles.GOAL_LABELS/TARGETS/GOAL_ROUTES`에 끼워 넣는다(팀 파일 변경 없음). 정식으로 넣으려면 `contracts.GoalId`, `profiles`, 세션 카탈로그, 문서(N-15 등)를 함께 바꿔야 한다.
- 알람의 시각 입력 단계(INPUT_HINT)는 시간 선택 영역에 테두리를 띄운 뒤, 사용자가 맞추고 터미널에서 Enter를 누르면 진행한다(시각 다이얼 값은 화면에서 읽지 않음). 도착 판정은 알람 목록에 그 시각이 보이는지로만 본다(날짜 미확인).
- 버튼이 화면에 없으면 `SHOW_SCROLL_HINT`로 큰 화살표+배너를 띄우고 사용자의 스크롤을 기다린다(화살표는 디버그 수신기 전용 그림, 앱 오버레이 모듈에는 없음).

## 5. 다음 작업 제안 (우선순위 순)

1. **설정 앱 위 오버레이 가시성 확인** (가장 중요, 시연 구조에 영향)
   - 설정 화면에서 위 broadcast 테스트 → `border=true`인데 안 보이고 홈에서는 보이면 가설 확정.
   - 확정되면 A 담당과 대안 논의: 접근성 서비스 오버레이(`TYPE_ACCESSIBILITY_OVERLAY`), 시연 대상 앱 변경, 테두리 대신 다른 안내(진동/음성/배너 위치) 등. 대안별 정책·심사 영향 검토 필요.
2. **`Api.kt` 크래시 수정**: `call()`에서 `IOException`을 잡아 실패 `Result`로 돌려 UI에 "서버에 연결할 수 없어요" 표시. 서버가 꺼져 있어도 앱이 죽지 않게.
3. **통화·STT 키 준비**(B 담당 범위): LiveKit `LIVEKIT_URL/API_KEY/API_SECRET`, RTZR `RTZR_CLIENT_ID/SECRET`. 백엔드는 셸에 환경 변수로 올려야 한다. 팀원 VM 백엔드를 재시작하기 전에 팀과 합의.
4. **실제 앱 경로로 전환**: 키가 생기면 Mac에서 `server.mvp`를 띄우고(아래), 두 AVD로 `android/README.md` 절차 실행.
   ```bash
   PYTHONPATH=.:server/session:server/voice \
     python -m uvicorn server.mvp:create_app --factory --host 127.0.0.1 --port 8000
   ```
   이때 `brev port-forward ... -p 8000:18002`와 포트가 겹치니 둘 중 하나만.
5. **설계 논점 공유**: N-16이 목표 경로(`GOAL_ROUTES`)의 가장 가까운 버튼만 후보로 남겨서, N-17에서 Zoro-1은 사실상 "그 버튼 vs WAIT"만 고른다. 안정성에는 유리하지만 "Decision 모델이 판단한다"를 발표에서 강조하려면 구조를 정확히 설명하거나 후보 범위를 넓히는 안을 검토.
6. 도구를 팀 저장소에 남길지 결정: 남긴다면 `server/ops/README.md`에 사용법 추가, 커밋 전에 `__pycache__/` 제외 확인.

## 6. 에이전트 작업 규칙

- **비밀값 금지**: `ZORO_DECIDE_TOKEN`, LiveKit·RTZR 키, 기기 토큰을 출력·문서·커밋에 남기지 않는다. `.env`는 커밋 금지.
- **비용**: Zoro-1 호출마다 크레딧 차감. 팀 한도 $100, **누적 $70 이상이면 새 실험 중지**(`server/ops/README.md`). `try_emulator`/`try_navigation`은 목표당 5~6회 호출.
- **공유 인스턴스**: `hyojason-zoro`는 팀원 소유·공용. `brev stop/delete` 금지, VM 안 프로세스(30001, 18002) 재시작 금지 — 필요하면 사람에게 먼저 묻는다. VM에서는 읽기 위주 명령만.
- Brev 웹의 **Share SSH Access → Add User는 되돌릴 수 없는 베타 기능**이라고 표시되어 있다. 에이전트가 누르지 않는다.
- `server/agent/` 판단 로직을 바꿀 때는 `python3.11 -B -m unittest discover -s server/agent/tests -v`를 돌리고, 노드 문서(`docs/small-nodes/`)와 맞는지 확인한다.
- 커밋·푸시는 사람이 요청할 때만. 브랜치를 따서 작업한다.

## 7. 통합 담당 질문에 대한 답 (2026-11-20)

### 7-1. 시험 문장과 Zoro-1 결과 (실모델 LIVE만, revision `bf1b76b9…`)

| 문장 | 기대 목표 | 결과 1위 (확률) | 2위 | 채택 | 반복 |
|---|---|---|---|---|---|
| 글자가 너무 작아 | SET_FONT | SET_FONT 0.967 | NONE 0.032 | 채택, 슬롯 `{}` | 같은 문장을 `try_emulator`에서 한 번 더 호출 → SET_FONT 0.967 (같은 값). 그 외 반복 없음 |
| 내일 아침 여섯 시에 일어나야해 | CLOCK_ALARM | CLOCK_ALARM 0.791 | NONE 0.204 | 채택(기준 0.70을 0.09 차이로 넘음), 슬롯 `{"day":"tomorrow","time":"06:00"}`(규칙) | 1회만 |

- `--fake` 실행 결과(FIXTURE)는 위 표에 넣지 않았다.
- 간접 표현("일어나야 해")은 확률이 기준선 근처라, 시연 문장을 정하기 전에 같은 문장 반복·유사 표현 여러 개를 돌려 보는 것을 권장한다.
- `try_navigation`/`try_emulator`의 화면별 NEXT_ACTION 실모델 결과는 아직 받지 못했다.

### 7-2. LiveKit 오류

- 화면: 자녀 AVD `통화 중 00:09` 상태에서 `통화에 연결하지 못했어요 (rtc-token DEPENDENCY_UNAVAILABLE)`.
- 실패 단계: 통화 수락 뒤 `child/HomeActivity.join()` → `Api.token()` → `POST /v1/calls/{call_id}/rtc-token`. 서버 `voice/service.py`가 `LIVEKIT_URL/API_KEY/API_SECRET` 중 하나라도 없으면 `DEPENDENCY_UNAVAILABLE`을 돌려주는 경로(274–276행). 서버상 통화는 수락됐고 LiveKit 방 입장만 실패.
- 붙은 백엔드: **VM `hyojason-zoro`의 18002**(`brev port-forward hyojason-zoro -p 8000:18002`로 Mac 8000에 연결하도록 안내). 그 프로세스 환경에는 `LIVEKIT_*`, `RTZR_*`가 없음을 `/proc/<pid>/environ` 이름 목록으로 확인. 다만 오류 순간에 port-forward가 정확히 18002였는지는 사용자 화면으로 따로 확인하지 않았다(오류 코드와 환경은 일치).
- 그보다 앞서 백엔드가 아예 없을 때는 `코드 만들기`에서 `ConnectException`으로 앱이 꺼졌다(`Api.kt` `call()` 미처리) → 별도 수정 필요.

### 7-3. 키 공유 제안 (레포·채팅 사용 안 함)

1. **LiveKit: 같은 프로젝트에 멤버로 초대 → 각자 개인 API 키 발급** (권장). 키를 옮기지 않고, 사람별로 폐기할 수 있다.
2. **RTZR**: 콘솔에서 사람별 Client를 만들 수 있으면 각자 발급. 하나만 써야 하면 비밀번호 관리자 공유(예: 1Password/Bitwarden Send, 만료·1회 열람)로 전달.
3. 받은 값은 각자 `server/voice/.env`(gitignore 됨)에만 두고 `bash server/ops/run_local_backend.sh`로 띄운다. 이 스크립트는 값을 출력하지 않고 빠진 이름만 알려 준다.
4. VM 18002 백엔드에 키를 넣는 것은 소유자가 서버를 재시작해야 하므로 소유자 판단에 맡긴다.
- 민서 쪽에서 LiveKit Cloud 새 프로젝트 발급을 시작하려다 멈춘 상태(로그인 화면까지). 1번으로 가면 새로 만들 필요 없음.

### 7-4. AVD·라벨·y_offset

- AVD: `Hyojason_API_35`(부모, emulator-5554), `Hyojason_API_35_chi…`(자녀). **API 35, 시스템 언어 영어**(설정 첫 화면 `Network & internet`, `Display`는 첫 화면 아래 스크롤 위치).
- "글자 크게"의 실제 라벨: **미확인**(그 화면까지 가지 못함). 스크립트는 `Increase font size` 등으로 추정.
- `y_offset`(상태 표시줄 높이 보정): **미확인**. `DebugGuideReceiver`의 `border=true` 응답과 실제 위치를 아직 받지 못했다.
- 설정 앱 테두리 숨김은 통합 쪽 #37 확인과 같은 현상으로 본다(이쪽에서는 가설 단계였음). 그래서 `try_emulator`의 설정 시나리오는 테두리가 안 보이는 게 정상이고, 시계·Google 앱 시나리오로 확인하는 게 맞다.

### 7-5. "통화 중 직접 버튼 지시가 없을 때 목표 기반으로 다음 버튼 안내" 정책 의견

찬성하되 조건을 두는 쪽을 제안한다.

- 장점: 자녀가 목표만 말하고("글자 좀 키워 드려") 단계를 말하지 않는 경우가 흔할 것. 이미 통화 후 모드에서 쓰는 N-16/N-17과 `GOAL_ROUTES`를 재사용하므로, 판단 위험은 기존 경로 수준이다(버튼은 코드 경로가 정하고 Zoro-1은 안내/WAIT만 고름).
- 위험·확인할 점
  - **역할 충돌**: 통화 중은 자녀 주도(CALL_CHILD_LED)라, 에이전트 안내가 자녀의 다음 말과 겹치거나 다른 버튼을 가리킬 수 있다 → 자녀 발화(확정·부분 모두)가 들어오면 즉시 취소, 지시 우선.
  - **목표 확신**: 간접 표현은 0.79처럼 기준선 근처였다. 통화 중 자동 안내는 목표 채택 기준을 통화 후보다 높이거나, 목표가 승인된(goal_revision 확정) 뒤에만 켠다.
  - **타이밍**: 마지막 지시·발화 후 일정 시간(예: 기존 3초 게이트보다 긴 값) 동안 아무 지시가 없을 때만, 한 번에 한 단계.
  - **표시 구분**: 자녀 앱에 "AI 제안 중: ○○" 상태를 보여 자녀가 끄거나 바로잡을 수 있게.
  - **구현 범위**: 지금 N-17은 `mode == POST_CALL_AGENT`만 받고 N-14의 NEXT_ACTION도 POST_CALL_AGENT 모드에 묶여 있다. 통화 중에 쓰려면 계약(Mode·질문 종류)과 노드 문서(N-17, N-14)를 같이 바꿔야 한다. 테스트(`server/agent/tests`)에 "지시 도착 시 제안 취소" 경우를 추가.
  - 설정 앱 안에서는 테두리 대신 알림 안내(#37)이므로 같은 정책이 알림으로 나간다는 점을 UX에 반영.
