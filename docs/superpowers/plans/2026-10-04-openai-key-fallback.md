# OpenAI Key Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 학교 계정 OpenAI 키가 결제·잔액·폐기 오류로 영구 실패할 때 같은 요청을 오너 계정 키로 한 번 재전송하고, 프로세스 재시작 전까지 이후 OpenAI 호출을 오너 계정으로 보낸다.

**Architecture:** `app.openai_key_fallback`이 키 존재 판정, 계정 선택, 영구 오류 판정, 프로세스 전역 전환 상태와 최초 알림을 단독 소유한다. 이미지와 텍스트 호출은 HTTP 전송 콜백만 이 모듈에 넘기고 응답과 사용 계정 표식을 돌려받으며, 기존 rate-limit 백오프와 provider 폴백은 호출부에 그대로 둔다. 이미지 계정 표식은 기존 `image_usage_events.usage_raw`에 넣어 스키마를 바꾸지 않는다.

**Tech Stack:** Python 3.12, FastAPI, httpx, pytest, AWS Copilot YAML

**Spec:** 사용자 작업지시서 E, 2026-10-04 현재 대화

## Global Constraints

- 실제 OpenAI 호출은 금지하고 HTTP를 목으로 검증한다.
- `JOB_CONCURRENCY` 등 동시 처리 설정을 변경하지 않는다.
- 새 패키지와 DB 스키마 변경을 추가하지 않는다.
- 작업 범위 밖 리팩터링과 포매팅을 하지 않는다.
- 커밋하지 않고 워킹 트리에만 변경을 남긴다.
- fallback 미설정 시 현재 동작을 보존한다.

## Review Focus

- 일반 `rate_limit_exceeded` 429가 전역 전환을 일으키지 않고 이미지 기존 백오프를 계속 타는지 확인한다.
- fallback 미설정과 두 키 모두 영구 실패인 경우 원래 OpenAI 오류가 그대로 호출자에게 도달하는지 확인한다.
- `billing_hard_limit_reached`와 `invalid_api_key`도 잔액 코드와 같은 단일 전환 경로를 타는지 확인한다.
- 동시 또는 연속 영구 실패에도 전환 로그와 운영 Slack 경로가 프로세스당 한 번만 동작하는지 확인한다.
- 예외, 로그, 메타데이터에 실제 키 문자열이 기록되지 않는지 확인한다.

---

### Task 1: 중앙 키 전환 상태와 설정

**Files:**
- Create: `server/app/openai_key_fallback.py`
- Modify: `server/app/config.py`
- Test: `server/tests/test_openai_key_fallback.py`

**Interfaces:**
- Produces: `has_openai_api_key(settings) -> bool`
- Produces: `request_with_openai_key_fallback(settings, send) -> OpenAIResponse`
- Produces: `OpenAIResponse.response`, `OpenAIResponse.account`

- [ ] 실패하는 테스트로 잔액 429 즉시 fallback 성공, 이후 fallback 선사용, 일반 429 무전환, fallback 없음, 두 키 실패, 401과 hard limit, 단일 알림, 키 비노출을 고정한다.
- [ ] 새 테스트가 중앙 모듈 부재 또는 동작 부재로 실패하는지 확인한다.
- [ ] 최소 중앙 모듈과 `OPENAI_API_KEY_FALLBACK` 설정 로딩을 구현한다.
- [ ] 중앙 모듈과 설정 테스트를 통과시킨다.

### Task 2: 이미지 호출과 비용 메타데이터

**Files:**
- Modify: `server/app/agents/gemini_image.py`
- Modify: `server/app/image_usage.py`
- Modify: `server/tests/test_gemini_image.py`
- Modify: `server/tests/test_image_cost.py`

**Interfaces:**
- Consumes: Task 1의 중앙 요청 함수와 `primary | fallback` 계정 표식
- Produces: 기존 이미지 반환·백오프 계약, `usage_raw.openai_account`

- [ ] 이미지 경로의 전환 성공, 일반 429 백오프, fallback 없음, 두 키 실패, 계정 비용 기록 테스트를 먼저 추가한다.
- [ ] 새 테스트가 직접 primary 키 사용과 계정 메타데이터 부재로 실패하는지 확인한다.
- [ ] 이미지 전송을 중앙 요청 함수로 연결하고 비용 기록에 계정 표식을 전달한다.
- [ ] 이미지와 비용 테스트를 통과시킨다.

### Task 3: 텍스트·검수와 키 존재 게이트

**Files:**
- Modify: `server/app/agents/vision_llm.py`
- Modify: `server/app/agents/detail_output_qc.py`
- Modify: `server/app/agents/cut_generator.py`
- Modify: `server/app/personalization_qc.py`
- Modify: `server/app/main.py`
- Modify: `server/tests/test_vision_llm.py`
- Modify: `server/tests/test_detail_output_qc.py`
- Test or modify: 관련 설정·앱 게이트 테스트

**Interfaces:**
- Consumes: Task 1의 중앙 요청 함수와 키 존재 판정
- Produces: 텍스트와 검수 호출의 동일 전환 동작, fallback-only 설정도 인식하는 provider 게이트

- [ ] 텍스트와 검수 경로에서 잔액 전환, 일반 429 무전환, fallback 없음, 두 키 실패를 검증하는 테스트를 먼저 추가한다.
- [ ] 새 테스트가 직접 primary 키 사용 또는 fallback 키 미인식으로 실패하는지 확인한다.
- [ ] 모든 키 존재 판정을 중앙 helper로 바꾸고 `_call_gpt` 전송을 중앙 요청 함수로 연결한다.
- [ ] 텍스트, 검수, 관련 게이트 테스트를 통과시킨다.

### Task 4: 배포 설정과 런북

**Files:**
- Modify: `copilot/api/manifest.yml`
- Modify: `copilot/detail-worker/manifest.yml`
- Create: `docs/runbooks/openai-keys.md`
- Modify: `server/tests/test_output_qc_config.py`

**Interfaces:**
- Produces: 두 ECS 서비스의 같은 환경별 SSM fallback secret 배선과 운영 절차

- [ ] 두 manifest가 환경별 fallback secret을 참조하는 동작 테스트를 먼저 추가하고 실패를 확인한다.
- [ ] 두 manifest에 요청된 경고 주석과 secret을 추가하고 키 역할, 전환 조건, 확인 로그, 교체 절차를 런북에 적는다.
- [ ] manifest 관련 테스트를 통과시킨다.

### Task 5: 전수 검토와 전체 검증

**Files:**
- Review: 전체 변경 diff와 모든 `openai_api_key` 참조

**Interfaces:**
- Consumes: Tasks 1~4의 완성 동작
- Produces: 누락 없는 호출 배선과 실제 검증 결과

- [ ] `rg`로 OpenAI 직접 키 참조와 HTTP 호출이 중앙 로직을 우회하지 않는지 재검사한다.
- [ ] 서버 변경 집중 테스트를 실행한다.
- [ ] `pnpm test:frontend`, `pnpm build`, 서버 전체 pytest를 실행하고 기준 실패 외 추가 실패가 없는지 수치로 기록한다.
- [ ] 커밋하지 않았고 동시 처리 설정을 바꾸지 않았는지 diff로 확인한다.
