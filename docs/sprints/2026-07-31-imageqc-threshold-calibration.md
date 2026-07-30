# AG-P2 4축 임계 캘리브레이션 — 2026-07-31

표본: 로컬 54322 실 마네킹컷 30건 (scripts/qc_calibrate_image.py, scored=True)

## 축별 분포

- `product_fidelity` min 45 · 중앙 58 · max 85
- `physical_naturalness` min 72 · 중앙 78 · max 85
- `image_quality` min 75 · 중앙 80 · max 88

- 최저축(게이트 기준) min 45 · 중앙 58 · max 85

## 임계별 분기

| auto_pass / review | 통과 | 검수 | 재생성 | 통과율 |
|---|---|---|---|---|
| 90 / 75 | 0 | 13 | 17 | 0% ← 초기 추측값, 폐기 |
| 85 / 70 | 4 | 9 | 17 | 13% |
| 80 / 65 | 12 | 1 | 17 | 40% ← 채택 |
| 75 / 60 | 13 | 1 | 16 | 43% |

## 결론

- 초기 추측값 90/75 는 **통과 0/30**. MANNEQUIN_QC_ENABLED 가 pass율 0% 로 전 생성을
  막았던 2026-07-07 사고와 같은 조건이라 폐기하고 80/65 채택.
- 병목은 `product_fidelity`(중앙 58). 다른 두 축보다 20점 낮다.
- **임계를 어떻게 바꿔도 재생성률이 ~55% 로 고정된다.** critical_errors 가 17/30건이라
  점수와 무관하게 재생성을 트리거하기 때문. 즉 임계 문제가 아니라 로고·텍스트 재현 품질 문제다.

### critical_errors 내역

- 5건 — text or logo altered
- 3건 — garment type changed
- 3건 — text or logo altered, invented or unreadable
- 2건 — garment length changed
- 2건 — garment shape broken
- 1건 — garment color changed
- 1건 — text or altered text on product text altered text misspelling
- 1건 — text_or_logo_altered_invented_or_unreadable

### enforce 승격 조건

지금 올리면 재생성 콜만 ~1.5배 늘고 결과는 같다. 로고·텍스트 재현 개선이 선행돼야 한다.
정확도(FPR/FNR)는 이 발화 분포로는 못 낸다 — 사람이 붙인 정답 라벨이 필요하다.
