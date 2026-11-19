# Big Node 목록

Big Node별 실행 단계와 조건부 분기를 규정한다. 개별 함수의 입출력은 [Small Node](../small-nodes/README.md), 역할과 시간은 [작업 배분](../work-plan.md)을 따른다.

| Big Node | 담당 | 주요 역할 | Small Node |
|---|---|---|---|
| [B-01 · 가족 연결·통화·동의](B-01.md) | B, A 협업 | 승인된 1:1 관계에서 부모 발신을 받아 동의된 음성통화를 시작한다. | [N-01](../small-nodes/N-01.md) · [N-02](../small-nodes/N-02.md) · [N-03](../small-nodes/N-03.md) |
| [B-02 · 음성 수신과 한국어 지시 확정](B-02.md) | B, C 협업 | 부모·자녀의 실제 통화 오디오를 역할별로 읽고 확정 발화만 전달한다. | [N-04](../small-nodes/N-04.md) · [N-05](../small-nodes/N-05.md) · [N-06](../small-nodes/N-06.md) |
| [B-03 · 부모 화면 관찰과 전이 확정](B-03.md) | A, D 협업 | 동의한 화면을 단말에서 처리해 검증 가능한 화면·대상·완료 단서만 전달한다. | [N-07](../small-nodes/N-07.md) · [N-08](../small-nodes/N-08.md) · [N-09](../small-nodes/N-09.md) · [N-10](../small-nodes/N-10.md) |
| [B-04 · 도움 상태와 방문 이력](B-04.md) | D, A·B 협업 | 통화와 도움의 생명주기를 나누고 이후 재판단을 위한 현재 위치와 검증된 경로를 유지한다. | [N-11](../small-nodes/N-11.md) · [N-12](../small-nodes/N-12.md) · [N-13](../small-nodes/N-13.md) |
| [B-05 · Zoro-1 판단과 모드별 안내](B-05.md) | C, A·D 협업 | 자녀 지시의 위치·수행과 목표를 판단하고, 통화 종료 뒤에만 다음 행동과 도착을 판단한다. | [N-14](../small-nodes/N-14.md) · [N-15](../small-nodes/N-15.md) · [N-16](../small-nodes/N-16.md) · [N-17](../small-nodes/N-17.md) · [N-18](../small-nodes/N-18.md) · [N-27](../small-nodes/N-27.md) · [N-28](../small-nodes/N-28.md) |
| [B-06 · 부모님 표시와 모드별 3초 게이트](B-06.md) | A, D 협업 | 한 대상만 빨간 테두리와 큰 문구로 표시하고 터치 조작을 보존한다. | [N-19](../small-nodes/N-19.md) · [N-20](../small-nodes/N-20.md) |
| [B-07 · 역할별 상태 전달·완료·삭제](B-07.md) | D, A·B 협업 | 부모 상태와 자녀의 도착·완료 UI를 투영하고 역할별 종료에서 도움을 정리한다. | [N-21](../small-nodes/N-21.md) · [N-22](../small-nodes/N-22.md) · [N-23](../small-nodes/N-23.md) |
| [B-08 · AVD·GPU 운영·검증·시연](B-08.md) | D, C 모델 담당 | 측정과 확인에 필요한 도구·시연 조건을 만들고 실제 성공 여부를 분리한다. | [N-24](../small-nodes/N-24.md) · [N-25](../small-nodes/N-25.md) · [N-26](../small-nodes/N-26.md) |
