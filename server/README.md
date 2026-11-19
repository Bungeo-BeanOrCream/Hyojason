# 통합 MVP 실행

`server.mvp:create_app`은 통화와 분석 동의, 도움 세션, B-05 판단을 FastAPI 한 프로세스에 연결합니다. 통화 서버가 발급한 `call_id`와 `trace_id`를 도움 세션이 재사용합니다. 두 사람의 페어와 동의 상태는 프로세스 메모리에 있으며, 통화와 도움을 끝내면 다음 도움을 새 코드로 연결합니다.

## 백엔드와 모델

Python 3.11 이상인 가상환경에서 저장소 루트 기준으로 실행합니다. LiveKit·RTZR 환경 변수는 로컬에서 [음성 서버 설정](voice/README.md)을 따라 설정합니다. 모델 주소와 리비전은 [모델 실행 안내](ops/README.md)를 따릅니다.

```bash
python -m pip install -r server/voice/requirements.txt 'uvicorn[standard]==0.54.0'
PYTHONPATH=.:server/session:server/voice \
  python -m uvicorn server.mvp:create_app --factory --host 127.0.0.1 --port 8000
```

`GET /healthz`로 백엔드가 떠 있는지 확인합니다. Zoro-1을 GPU VM에서 실행하면 SSH 터널로 모델의 로컬 포트를 연결할 수 있습니다. 백엔드도 GPU VM에서 실행하는 경우 모델을 VM 내부에서 호출하고, 백엔드 포트를 SSH 터널로 Mac의 포트 8000에 연결합니다. 기본 Android AVD 빌드는 호스트의 포트 8000을 사용합니다.

## Android 앱

1. Android Studio에서 저장소의 `android/`를 열고 API 35 이상의 두 AVD에서 `app` debug 구성을 실행합니다. [Android 빌드](../android/README.md)
2. 각 기기에서 가족 역할을 고르고 새 연결 코드를 사용합니다. 발신 화면에서 이번 도움의 안내 대상을 선택합니다.
3. 통화 수락 뒤 안내 대상 기기에서 **화면 안내 시작**을 누릅니다. Android 오버레이 권한과 화면 공유를 승인한 뒤 기본 설정 앱을 열어 테스트합니다.
4. 화면 인식 프로필은 한국어 API 35 ARM64 에뮬레이터의 기본 설정 앱(버전 `15`)을 지원합니다. 시계 앱의 실제 화면 검증은 알람 카드·아이콘 근거가 등록된 뒤 진행합니다. [라이브 리허설](../demo/fixtures/README.md)

## 통합 시험

```bash
PYTHONPATH=.:server/session:server/voice \
  python -m pytest -q server/tests/test_mvp.py
```

이 시험에서 두 역할의 호출·화면 관찰과 실제 발화는 합성 입력입니다. `HYOJASON_INTEGRATION_LIVE_MODEL=1`을 지정하고 모델 환경 변수를 설정하면 같은 시험에서 실제 Zoro-1을 호출합니다. 모델 실측 시험은 STT와 실제 두 기기의 화면 표시 결과와 구분해 기록합니다.
