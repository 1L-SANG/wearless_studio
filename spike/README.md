# AI 품질 스파이크 하네스

backend_integration_plan.md §10 **0단계** 실험 도구. **일회용** — 서비스 코드(`src/`, `server/`)와 무관. 의존성 없음(Node 내장 fetch).

## 목적
인프라 본구현 전에 최대 리스크를 검증한다: **Gemini 이미지 모델이 고정 베이스 마네킹에 상품 의류(상의+하의)를 제대로 착장시키는가? 비용·지연은?**

## 파일 (회원님이 만질 건 2개)
- **`my-prompt.txt`** — 시스템 프롬프트(작성). `my-prompt-en.txt` = 영어본.
- **`run-mine.sh`** — 실행기. 상단 노브(모델·temp·해상도·횟수·입력)만 바꾸고 실행.
- `spike.js` — 엔진(직접 안 건드려도 됨). `base/`·`input/`·`product-ami-tee.json` = 베이스 마네킹·상의·분석정보.

## 사용법
```sh
# 1) 키: spike/.env 의 GEMINI_API_KEY (AI Studio AIza… 키 / VERTEX_PROJECT 비우면 AI Studio)
# 2) 프롬프트: spike/my-prompt.txt 편집
# 3) 실행 (기본 Pro·1K·temp0.4·10회·상의+하의)
bash spike/run-mine.sh
# 빠른 1회·다른 모델로:
RUNS=1 MODEL=gemini-3.1-flash-image bash spike/run-mine.sh
```

엔진 직접 호출:
```sh
node spike/spike.js <상의사진폴더> --base <마네킹> --prompt-file <프롬프트.txt> \
  [--match <하의>] [--product <분석.json>] [--type 티셔츠] [--res 1K|2K|4K] \
  [--temp 0.2] [--model <모델>] [--n 10] [--dry-run]
```
- **첨부 순서**: `[1]base` · `[2..]상의폴더(파일명 정렬순)` · `[마지막]match`(있을 때).
- 프롬프트는 `--prompt-file`에서만 옴(`${clothingType}`/`${productCount}` 치환). 분석정보는 `--product`가 끝에 자동 주입.
- 결과: `spike/runs/<타임스탬프>-mine/` (result-N.jpg + report.html + meta.json).

## 판정 (2026-06-19)
- 6/12: gpt-image-2 베이스 마네킹 확정(`base/`) → gemini-3-pro-image 스왑 **조건부 통과**(로고·구도 잔여 리스크).
- 6/19: Gemini 3.1 Flash / 3 Pro Image로 재검증. **1K에서 상의+하의 풀착장 10/10 통과**(사용자 프롬프트).
  - ⚠️ **2K 생성 경로가 서버측 일시 저하**(유령 이미지) — 동일 요청도 1K=정상/2K=유령. 복구 전까지 **1K 사용**.
  - 후속 설계(QC 게이트 + Pro 승격, 프롬프트 외부화)는 Phase 4 본구현으로 이관.
