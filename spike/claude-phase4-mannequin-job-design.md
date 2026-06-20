# Phase 4 — generateMannequins(AG-04) 백엔드 job 설계 (Claude 독립안)

> 정본: backend_integration_plan §4·§5·§6, ai_agent_modules AG-04/§1, common_data_contract §6.
> 기존 인프라 위에 얹는다: server/app/{main,db,config,r2,repo,routes,models,auth}.py · 스키마(jobs/job_events/credit_accounts/credit_ledger/mannequin_cuts/assets) 전부 존재. R2 `ai_key()`/`put_bytes()`/`public_url()` 준비됨.

## 0. 핵심 원칙
- job은 **요청 핸들러 밖**에서 실행(§5). 엔드포인트는 job row 생성 + 크레딧 예약 + `202 {jobId}`만.
- 프롬프트·모델은 **코드 하드코딩 금지** — `agent_config.py` 한 곳(tier→모델, agentId→프롬프트). 추후 DB 테이블로 이전해 무중단 교체.
- **1K 사용**(2K 서버경로 일시 저하). 해상도도 config.
- Flash 기본 + QC 실패 시 Pro 승격. 크레딧 reserve-confirm은 임시 단가.

## 1. 추가 파일 (server/app)
| 파일 | 역할 |
|---|---|
| `agent_config.py` | `MODEL_ROUTING={image_high, image_light, text}`, `PROMPTS={'AG-04':...}`, `RES`, `CREDIT_COSTS`(임시). 교체 단일 소스 |
| `gemini.py` | 서버 Gemini 이미지 클라이언트 — 스파이크 callGemini 이식([base,...photos]+prompt, generativelanguage/vertex 분기, 최대 image part 채택). `to_thread` 격리 |
| `jobs_repo.py` | create_job(dedupe/idempotency), claim_pending(FOR UPDATE SKIP LOCKED + lease), set_progress, append_event, finish(done/error), reclaim_stale |
| `credits.py` | reserve(account FOR UPDATE→available 검증→reserved+=cost, 부족 402), confirm(balance-=실제, reserved-=예약, ledger append), release(reserved-=예약) |
| `dispatcher.py` | lifespan asyncio 태스크: pending claim→handler 실행→done/error. graceful shutdown + stale lease 재큐 |
| `agents/mannequin.py` | AG-04 핸들러 (아래 §3) |
| `config.py`(수정) | gemini_api_key, vertex_project/location 추가 |
| `routes.py`(수정) | POST `…/mannequins:generate`, GET `/jobs/{id}`, GET `/jobs/{id}/events`(SSE) |
| `lib/api/httpAdapter.js`(프론트) | generateMannequins: POST→202→SSE/폴링 구독→onProgress→{data,credits} |

## 2. Job 생명주기
```
[POST mannequins:generate]  (요청 tx)
  소유권 확인 + analysis.locked + 정면 사진 존재(TODO A-6 guard) 검증
  → 기존 active job(partial unique) 있으면 200 + 기존 jobId (멱등 합류)
  → credit_accounts FOR UPDATE → available≥예상비용? 아니면 402
  → reserved += 예상비용(A/B 2컷), jobs INSERT(kind='mannequin', status='pending',
     payload={candidates:[A,B], baseFit}, credits_reserved, dedupe_key=proj:mannequin,
     idempotency_key=헤더) → commit → 202 {jobId}

[dispatcher]  claim: UPDATE jobs SET status=running, locked_by, locked_at
              WHERE id=(SELECT id FROM jobs WHERE status='pending'
              ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *

[worker = agents/mannequin.handle]
  입력 로드: 성별 베이스(agent_config seed asset by gender) + 상품 사진(assets→R2 bytes)
            + 분석 맥락(analyses.payload → productBlock)
  for cand in [A,B]:
     prompt = PROMPTS['AG-04'](analysis, cand)        # 외부화
     img = gemini(image_light, 1K, [base, *photos], prompt)   # Flash 기본
     verdict = qc(img)                                # 값싼 규칙검사 (AG-P2 슬롯)
     while fail and tries<N: re-roll
     if still fail: img = gemini(image_high, …)       # Pro 승격
     put_bytes(ai_key) → assets row(source=ai) → mannequin_cuts row(cand,version=1,asset_id,base_fit)
     append_event(step/progress)
  성공 tx: balance-=성공컷수×단가, reserved-=예약, ledger append, jobs.result={mannequinCuts:[…]},
           credits_charged, status=done, append done event
  전체 실패 tx: reserved-=예약(release), status=error, append error event
```

## 3. 세부 결정
- **A/B 후보**: 같은 성별 베이스 + baseFit 변주(AG-04 계약). 한 컷만 성공해도 부분 반환(미차감은 실패분).
- **QC(AG-P2 슬롯)**: MVP=값싼 결정적 검사 — 디코딩 가능? 세로비(≥3:4)? 거의 균일/반투명(픽셀 std-dev 임계) 아닌가? 실패→재굴림(최대 N)→Pro 승격→최종. 비전 QC(동일성)는 P1.
- **진행 전달**: worker가 `job_events` append. GET `/jobs/{id}/events`=SSE(`Last-Event-ID` replay), GET `/jobs/{id}`=폴링. 진행률 매핑 ai_pipeline_spec §3.
- **복구**: dispatcher startup/주기 점검에서 lease timeout 초과 running job → pending 재큐 또는 error('서버 재시작').
- **관측**: jobs.metadata에 {agentId, tier, model, latency, count, assetIds} (§6 로깅).
- **봉투**: 프론트 어댑터가 job 완료 결과를 `{data, credits}`로 변환(계약 §6). credits = balance−reserved.

## 4. 미해결·리스크
- 정면 사진 필수 guard (TODO A-6) — sync 우회 경로 점검 필요.
- 2K 복구 시 RES만 config에서 2K로(재배포 최소).
- 프롬프트 DB 이전(무중단 교체)은 P1 — MVP는 config 파일.
- dispatcher 단일 web 프로세스 가정 — replica 증설 시 worker 분리(같은 claim 코드).
