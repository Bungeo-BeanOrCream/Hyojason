# A 슬롯 구현 확인 사항

현재 담당: Codex (2026-11-19)

## N-19에서 최신성 검증 실패 시 반환할 결과

- A 슬롯 브리프 4절과 [`instruction-policy.md`](../instruction-policy.md)는 이전 모드·지시·화면 등으로 현재 표시 조건을 충족하지 못하면 `suppressed`를 지정한다.
- [`N-19.md`](../small-nodes/N-19.md)의 반환 표와 19.8은 무효 스코프·권한·로컬 유효성 실패를 `error`로 지정한다. 이 경우 `outcome`과 `data`는 null이다. [`B-06.md`](../big-nodes/B-06.md)는 상위 결과에 `success/suppressed`를 정의한다.
- 확인할 사항은 이전 제안의 `Basis`가 최신 상태와 다른 경우 N-19에서 `error`와 `STALE_BASIS`를 반환하고 B-06에서 표시 억제를 처리하는지, N-19 자체에서 `success/suppressed`를 반환하는지다. 두 경우 모두 표시를 즉시 지운다.

## 진행 상태

- 현재 상태: 원격 `main`의 `ce7dc38`에서 로컬 `A`를 만들었다. 해당 기준에 Android 골격이 없다. 구현과 테스트는 시작 전이다.
- 다음 할 일: N-19 결과 경계를 확인한 뒤 0단계 Gradle 골격 빌드부터 진행한다.
- 하면 안 되는 일: 계약 문서와 다른 슬롯 경로 수정, `main` 직접 커밋, 확인 전 푸시·PR, fixture의 LIVE 처리.
- 막힌 것: 브리프의 계약 불일치 중단 규칙에 따라 N-19 결과 경계 확인이 필요하다.
