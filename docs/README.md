# 효자손 개발 설계

이 문서는 Trillion Labs Hackathon Track 1용 효자손의 구현 계약이다. PRD v2의 사용자·지원 범위에 자녀 주도 개입과 통화 후 인계 변경사항을 반영했다. 문서 구성은 ReportAnchor JS 브랜치의 Big Node 개요, Small Node 단일 책임 문서, 공통 결과 계약을 참고했다. 현재 상태는 **설계 계약이며 구현 검증 전**이다. [참고한 문서 구조](https://github.com/Jungminii-1114/ReportAnchor/tree/ad430ab123c61332e099765ab3e1a21c81c8dc7e/docs)

| 읽는 목적 | 문서 |
|---|---|
| 두 단말, 서버, 모델이 어떻게 연결되는지 확인 | [전체 구조와 상태 전이](architecture.md) |
| 어떤 책임을 누가 맡고 무엇부터 만드는지 확인 | [Big Node](big-nodes/README.md) · [작업 배분과 일정](work-plan.md) |
| 구현 함수의 입력, 출력, 오류, 테스트 확인 | [Small Node](small-nodes/README.md) |
| 메시지·결과·진단·커밋 규칙 | [공통 계약](contracts.md) · [NodeResult와 Diagnostic](node-result-contract.md) |
| 화면 변화와 3초 경합 처리 | [개입 정책](instruction-policy.md) |
| 구현 담당이 파일을 시작할 때 사용할 경계·API 확인 | [초기 연동 계약](implementation-contract.md) |
| 제품 요구사항이 빠졌는지 확인 | [PRD 추적표](requirements-map.md) |
| 실제 AVD에서 무엇을 검증할지 확인 | [지원 목표·시연](scenarios.md) · [실험 계획과 기대 결과](verification-plan.md) |
| 권한, 오디오, 모델 실행의 검증 근거 확인 | [기술 근거와 미검증 항목](references.md) |

## 팀원 4인 작업 배분

실명 확정 전까지 A–D를 사용한다. 각 행의 담당자는 해당 Small Node의 구현과 팀에 넘길 결과를 책임진다.

| 담당 | Big Node | 담당 Small Node | 팀에 넘길 첫 결과 |
|---|---|---|---|
| **A · Android 화면 관찰·Overlay** | [B-03 화면 관찰](big-nodes/B-03.md) · [B-06 부모 안내](big-nodes/B-06.md) | [N-07](small-nodes/N-07.md) · [N-08](small-nodes/N-08.md) · [N-09](small-nodes/N-09.md) · [N-10](small-nodes/N-10.md) · [N-19](small-nodes/N-19.md) · [N-20](small-nodes/N-20.md) | 설정·시계의 실제 화면에서 버튼을 찾아 빨간 테두리를 표시하고, 테두리 아래 버튼이 눌리는 것을 확인한다. |
| **B · LiveKit 통화·한국어 음성 인식** | [B-01 가족 연결·통화](big-nodes/B-01.md) · [B-02 발화 확정](big-nodes/B-02.md) | [N-01](small-nodes/N-01.md) · [N-02](small-nodes/N-02.md) · [N-03](small-nodes/N-03.md) · [N-04](small-nodes/N-04.md) · [N-05](small-nodes/N-05.md) · [N-06](small-nodes/N-06.md) | 부모님 AVD에서 발신하고 자녀 AVD에서 받는다. 앱 전환 중에도 양방향 음성을 유지하고, 발화자별 확정 문장을 서버에 전달한다. |
| **C · Zoro-1 판단·GPU 운영** | [B-05 System 1 판단](big-nodes/B-05.md) · [B-08 중 모델·GPU 운영](big-nodes/B-08.md) | [N-14](small-nodes/N-14.md) · [N-15](small-nodes/N-15.md) · [N-16](small-nodes/N-16.md) · [N-17](small-nodes/N-17.md) · [N-18](small-nodes/N-18.md) · [N-26](small-nodes/N-26.md) · [N-27](small-nodes/N-27.md) · [N-28](small-nodes/N-28.md) | L40S의 실제 Zoro-1로 목표, 지시 위치, 지시 수행, 통화 후 다음 행동·도착을 판단하고 모델 버전·비용을 기록한다. |
| **D · 도움 상태·완료·통합 검증** | [B-04 상태·이력](big-nodes/B-04.md) · [B-07 역할별 상태·종료](big-nodes/B-07.md) · [B-08 중 환경·전체 검증](big-nodes/B-08.md) | [N-11](small-nodes/N-11.md) · [N-12](small-nodes/N-12.md) · [N-13](small-nodes/N-13.md) · [N-21](small-nodes/N-21.md) · [N-22](small-nodes/N-22.md) · [N-23](small-nodes/N-23.md) · [N-24](small-nodes/N-24.md) · [N-25](small-nodes/N-25.md) | 같은 도움 세션에서 지시 교체와 통화 후 인계를 처리하고, 자녀가 도움 완료를 누를 때 양쪽 상태와 이력이 정리되는지 통합 검증한다. |

B-08은 D가 전체 리허설을 맡고 C가 모델·GPU 환경을 맡는다. A는 부모 화면, B는 자녀 화면의 완료 처리를 D와 연동한다. 착수 순서와 시간별 통과 기준은 [작업 배분과 일정](work-plan.md)을 따른다.

## 제품 기준

- 부모님 AVD가 자녀 AVD에 먼저 전화한다. 두 역할은 같은 네이티브 Android 앱에서 분리한다. LiveKit은 양방향 음성 전송을 맡는다.
- 도움의 수명은 통화와 별개다. 통화가 끝나면 음성 인식을 멈추고 마지막 목표로 안내한다. 자녀의 도움 완료 또는 부모님의 안내 끄기에서 화면 관찰과 도움 상태를 지운다.
- 통화 중에는 자녀가 이끈다. 새 지시의 위치를 미리 찾고 부모가 3초 안에 맞게 조작하면 표시하지 않는다. 근거를 확인한 경우에 한해 버튼·화면 내리기·뒤로 가기 안내로 개입한다.
- **Zoro-1 System 1**의 다섯 판단은 목표, 지시 대상 위치, 지시 수행, 통화 후 다음 행동, 통화 후 도착이다. 자녀는 도착 추정·도움 완료만 확인하고 인식 지시와 진행 화면은 받지 않는다.
- 지원 목표는 설정 2개, 시계·Google 지도·문자·Gmail 각 1개다. 설정에서 3초 개입과 무개입, 시계에서 통화 후 인계를 먼저 통합한다.
- 부모님이 실제 앱을 직접 조작한다. 동의한 화면의 결과 변화로만 수행을 확인하고, 근거 없는 위치·복귀·입력을 단정하지 않는다.

## 문서별로 책임을 나누는 기준

Big Node는 여러 Small Node가 연결되는 기술 구간이다. 실행 순서, 조건부 분기, 구간의 완료 조건은 Big Node 문서에 둔다. 개별 입력 검증과 반환 형태는 해당 Small Node 문서, 여러 실행 구간에 걸치는 메시지·버전 규칙은 공통 계약에 둔다. Big Node와 팀원 수는 독립이며 별도 Tiny Node 계층은 두지 않는다.

하위 설계의 수치·메뉴 경로는 검증할 초기값이다. 역할별 통과 기준을 충족한 뒤 실제 기기 버전, 모델 revision, 측정치를 기록한다. 초기 구현 파일은 아직 없으므로 문서에 나오는 함수명과 경로는 구현 제안이다.
