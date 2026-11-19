# 전체 구조와 상태 전이

효자손은 자녀가 말한 버튼을 부모님이 3초 안에 못 누르면 찾아 짚어 주고, 통화가 끝나면 마지막 유효 목표의 안내를 이어 간다.

## 실행 위치

| 위치 | 책임 |
|---|---|
| 부모 Android AVD | 전화, 권한 승인, MediaProjection, 로컬 화면 분석, 3초 게이트, Overlay |
| 자녀 Android AVD | 수신과 설명, 도착 추정 확인, 도움 완료. 화면 영상·AI가 이해한 지시·진행 상태는 자녀 UI에 표시하지 않는다 |
| LiveKit | 두 역할 양방향 음성. 전화와 도움 제어 채널은 수명이 다르다 |
| 세션 서버 | 승인, 이벤트 순서, 목표·지시·이력, 역할별 전송. Android 앱은 두 사용자와 Agent를 연결하는 허브 |
| Agent 및 Zoro-1 | 목표, 위치, 지시 수행, 통화 후 다음 행동과 도착을 승인 후보로 판단 |

P0은 같은 Kotlin APK를 ARM64 Android 15 AVD 두 대에서 역할별로 실행한다. 제어 서버 1개와 GPU 런타임을 분리한다. 화면 원본과 기하 정보는 부모 기기에서만 처리한다. 분석 동의 중 두 역할의 확정 발화를 구분한다. 두 역할의 발화 모두 Zoro-1 목표 확률을 갱신하지만, 부모 발화는 지시·개입을 일으키지 않는다.

## 전체 데이터 흐름

~~~mermaid
flowchart TD
    rtc["B-01 · LiveKit 통화와 양쪽 동의"] --> speech["B-02 · 자녀 확정 발화"]
    parent["B-03 · 부모 기기 화면 분석"] -->|ScreenState| state["B-04 · 도움 상태와 방문 이력"]
    parent -->|로컬 위치 및 단조 시각| overlay["B-06 · 재검증과 표시"]
    speech -->|FinalUtterance| state
    state --> mode{"mode와 현재 Basis"}
    mode -->|CALL_CHILD_LED| active["B-05 · 위치 및 수행 여부"]
    mode -->|POST_CALL_AGENT| post["B-05 · 다음 행동 또는 도착"]
    active -->|TargetDecision 및 ActionVerdict| overlay
    post -->|GuideProposal| overlay
    post -->|ArrivalDecision| child["B-07 · 자녀 도착 추정과 완료"]
    overlay -->|표시 또는 억제 확인| state
    child -->|명시적 완료| state
    rtc -->|call.ended| state
~~~

## 통화 중 자녀 주도

~~~mermaid
flowchart TD
    speech["자녀 지시 확정"] --> timer["부모 수신으로 3초 시작"]
    speech --> locate["버튼 위치 미리 찾기"]
    timer --> gate{"3초 경과와 관찰 상태"}
    locate --> gate
    gate -->|3초 안에 맞게 조작| silent["표시 없이 해당 지시 종료"]
    gate -->|"기한 경과 · 보이는 대상"| tap["지시한 버튼 표시"]
    gate -->|"기한 경과 · 아래 위치 확인"| scroll["화면 내리기 표시"]
    gate -->|"기한 경과 · 오선택과 역경로 검증"| back["뒤로 가기 표시"]
    gate -->|증거 부족 또는 보호 화면| wait["대기 또는 위험 확인 문구"]
    tap --> observe["조작 결과 관찰"]
    scroll --> observe
    back --> observe
    observe -->|같은 지시 유효| locate
    observe -->|새 자녀 지시| speech
    silent --> endCall["통화 종료 이벤트"]
    endCall --> post{"유효 목표 보유?"}
    post -->|예| greedy["현재 화면에서 다음 행동 또는 도착 판단"]
    post -->|아니요| close["안내 보류 후 도움 정리"]
~~~

스크롤 또는 뒤로 가기로 원래 지시의 화면을 찾으면 같은 instruction_id로 다시 위치를 확인한다. 현재 지시가 끝나면 새로운 자녀 지시를 기다린다. P1 여러 지시 큐와 통화 후 한 단계 정책은 별개다.

## 상태 전이

| 축 | 상태 | 전이 |
|---|---|---|
| 통화 | IDLE, RINGING, ACTIVE, RECONNECTING, ENDED | 수락과 실제 참가로 ACTIVE. 일시 재연결은 STT와 새 개입 보류. 확정 종료에서 통화 후 전이 |
| 도움 | LISTENING, ACTIVE, ARRIVAL_PENDING, ENDED | 초기 동의 뒤 listening. 자녀 지시 수신 또는 통화 후 유효 목표에서 진행. 도착 추정은 자녀 확인 대기 |
| 모드 | CALL_CHILD_LED, POST_CALL_AGENT | 도움마다 모드가 갈린다. hangup에서 mode_revision 증가와 통화 중 결과 즉시 취소 |
| 관찰 | NOT_GRANTED, ACTIVE, GAP, STOPPED | 캡처 중단이나 화면 공백에서 안내 제거. 해당 시스템 승인이나 새 프레임이 있을 때만 재개 |
| 지시 | WAITING, PENDING, CORRECT, SUPERSEDED | 현재 지시만 유지. 부모 발화는 전이 없음. 새 자녀 지시나 모드 전환이 이전 타이머를 취소 |

통화 중 도착 알림을 발행하지 않는다. RTC가 끝나도 이미 합의한 목표의 도움·제어 채널은 남는다. 목표가 없으면 임의로 정하지 않고 정리한다. 자녀 완료와 부모 중단에서 기록을 비운다. 완료 뒤 통화가 유지되고 부모 동의가 유효한 경우 새 자녀 지시나 목표에서 새 도움 ID를 연다. 부모 중단 이후에는 부모 재활성 동의가 먼저다.

## 구현과 정량 시험의 경계

통화 중 3초는 부모가 정규화된 새 지시를 처음 적용한 시각으로부터, 통화 후 3초는 모드를 적용한 뒤 안정 화면을 관측한 시각으로부터 계산한다. Android는 외부 앱의 전역 터치 로그를 사용하지 않는다. “눌렀나”는 허용 화면의 결과 증거로만 판단하며 확인할 수 없는 입력은 따로 집계한다. 세부 계약은 [구현 계약](contracts.md), 실제 컷오프와 평가 표는 [실험 계획](verification-plan.md)에 있다.
