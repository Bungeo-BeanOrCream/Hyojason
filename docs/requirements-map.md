# PRD v2 요구사항 추적표

요구 ID는 PRD v2 pp. 4–8의 번호다. 문서가 기능 구현 완료를 뜻하지는 않는다. 시연·정량 기준은 [검증표](scenarios.md#검증-표본과-티켓)를 사용한다.

| 요구사항 | 설계 책임 | 확인 방법 |
|---|---|---|
| A-1 부모의 한 번 발신 | [N-01](small-nodes/N-01.md) · [N-03](small-nodes/N-03.md) | 자녀 한 명의 사진과 발신 확인 |
| A-2 양쪽 효자손 앱 통화 | [N-03](small-nodes/N-03.md) | 두 AVD에서 발신·수신·양방향 음성 |
| A-3 페어 통화만 분석 | [N-02](small-nodes/N-02.md) · [N-04](small-nodes/N-04.md) | 제3 참가자·동의 한쪽 철회 거절 |
| A-4 앱 밖에서도 통화 | [N-03](small-nodes/N-03.md) | Settings·Clock 전환 중 양쪽 음성 실측 |
| A-5 자녀 발신 P1 | [B-01](big-nodes/B-01.md) | 우선순위 P1로 유지 |
| B-1 자동 분석·부모 표시 | [N-02](small-nodes/N-02.md) · [N-04](small-nodes/N-04.md) · [N-20](small-nodes/N-20.md) | 동의 뒤 듣는 상태 표시, 종료 뒤 제거 |
| B-2 단일 목표 · B-3 슬롯 | [N-15](small-nodes/N-15.md) | 다중 발화에서 하나의 목표, 알람·지도·문자 슬롯 |
| B-4 변경 전파 | [N-11](small-nodes/N-11.md) · [N-15](small-nodes/N-15.md) · [N-21](small-nodes/N-21.md) | 이전 강조 삭제, 양쪽 goal_revision 5초 내 갱신 목표 |
| B-5 보류 · B-6 바로 안내 | [N-15](small-nodes/N-15.md) · [N-17](small-nodes/N-17.md) | 낮은 확신은 대기, 명확한 목표는 확인 질문 없이 시작 |
| B-7 미지원 안내 P1 | [N-15](small-nodes/N-15.md) | NONE 분류를 지원하며 설명 일반 UI는 P1 |
| C-1 현재 화면의 한 단계 | [N-09](small-nodes/N-09.md) · [N-16](small-nodes/N-16.md) · [N-17](small-nodes/N-17.md) | 후보 중 선택하며 유효 대상 1개 검증 |
| C-2 빨간 테두리·문구 | [N-20](small-nodes/N-20.md) | 아래 버튼을 가리지 않고 안내 표시 |
| C-3 3초 · C-4 구 안내 제거 | [N-10](small-nodes/N-10.md) · [N-19](small-nodes/N-19.md) | 화면/목표 변경 직후 취소, 새 조건 후 3초 ±1초 |
| C-5 터치 통과 | [N-20](small-nodes/N-20.md) | Settings·Clock 포함 앱별 실제 버튼 터치 |
| C-6 P0 안내 · C-7 조작 확장 P1 | [N-17](small-nodes/N-17.md) · [N-20](small-nodes/N-20.md) | 탭/뒤로/스크롤/입력 안내를 검증. P1 종류를 따로 기록 |
| C-8 오조작 재판단 · C-9 낮은 확신 대기 | [N-10](small-nodes/N-10.md) · [N-16](small-nodes/N-16.md) · [N-17](small-nodes/N-17.md) | 부모가 다른 메뉴를 눌러도 현재 화면에서 다시 판단 |
| C-10 도착 추정 | [N-18](small-nodes/N-18.md) · [N-22](small-nodes/N-22.md) | 도착 추정 알림만 보내고 최종 완료 대기 |
| C-11 위험 동작 차단 | [N-08](small-nodes/N-08.md) · [N-16](small-nodes/N-16.md) | 허용되지 않는 요청은 표시 도구를 차단하고 확인 메시지 |
| D-1 이력 · D-2 이력 기반 복귀 | [N-12](small-nodes/N-12.md) · [N-16](small-nodes/N-16.md) | 실제 방문한 디스플레이 화면을 근거로 복귀 |
| D-3 반복 감지 P1 · D-4 삭제 | [N-12](small-nodes/N-12.md) · [N-23](small-nodes/N-23.md) | 종료 수명·재사용 없음 검사. 반복 경로 최적화는 P1 |
| E-1 상단 띠 · E-3 완료 문구 · E-4 접근성 | [N-20](small-nodes/N-20.md) · [N-21](small-nodes/N-21.md) | 상대/목표/안내 끄기/복귀, 큰 글씨, 안내 1개 확인 |
| E-2 오디오 출력 전환 | [N-03](small-nodes/N-03.md) | 이어폰 예외와 스피커폰을 각각 검사 |
| F-1 현재 목표 · F-3 화면 비공개 · F-4 통화 후 첫 화면 | [N-21](small-nodes/N-21.md) | 자녀 앱은 목표와 완료 상태만 수신 |
| F-2 도움 완료 · F-5 도착 알림 | [N-22](small-nodes/N-22.md) · [N-23](small-nodes/N-23.md) | 도착 알림 표시와 자녀의 명시적 완료 명령을 분리 |
| G-1 종료 상태 분리 | [N-03](small-nodes/N-03.md) · [N-11](small-nodes/N-11.md) | hangup → 다른 앱 → 완료 알림까지 |
| G-2 부모님 안내 끄기 | [N-20](small-nodes/N-20.md) · [N-23](small-nodes/N-23.md) | 네트워크 공백과 관계없이 부모 표시부터 정리 |
| G-3 통화 중 새 목표 · G-4 비활성 종료 P1 | [N-23](small-nodes/N-23.md) | 새 help_id, 새 이력. 30분 게이트는 P1로 검증 |
| H-1 첫 설정 · H-2 양쪽 동의 | [N-01](small-nodes/N-01.md) · [N-02](small-nodes/N-02.md) | 화면별 단계적 안내와 철회 |
| H-3 원본 비보관 | [N-04](small-nodes/N-04.md) · [N-08](small-nodes/N-08.md) · [N-25](small-nodes/N-25.md) | 원본의 서버/파일/로그 유입 검사 |

위험 요청에는 안내 표시 대신 자녀와 직접 확인하라는 문구를 표시한다. 권한이나 시스템의 보호 화면을 회피하는 기능은 없다. 같은 대상을 여러 요구 사항이 사용하는 경우 위 노드를 공유한다.
