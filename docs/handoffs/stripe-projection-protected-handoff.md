# Stripe Protected Composite — 인계 (2026-08-05)

## 상태
- 브랜치 `feat/garment-consistency-check`, HEAD `9319c07`, main `5580961` (불변)
- 보호 dirty 미변경: `server/uv.lock`, `src/App.jsx`(e11f1b0f…), `.context/`(553ecad2…)
- backend 2498 passed / frontend 224 passed / build PASS
- **실 Gemini 4K 호출 1회** (예산 2회 중 1회 사용). 결과 = 거절.
- HTML: `server/ab_out/frame_lock/stripe-projection-protected-v1/report.html`
- 산출물: 같은 디렉터리 `artifacts/` (carrier·composite·mask·alpha 9종, ab_out 은 gitignored)

## 이번에 고친 것 (커밋 5건)
`6684a09` 좌표 단위 버그 — `validate_geometry` 는 정규화 0..1 을 돌려주는데 `warp_composite`
는 픽셀로 소비. source quad 변이 항상 ≤1.0 이라 `side < MIN_DECAL_RES_PX(48)` 이 무조건 참 →
collar/placket decal 경로가 프로덕션에서 한 번도 실행된 적 없음. carrier quad 도 int 절삭으로
원점 퇴화 폴리곤이라 보호 해제가 무효였다. 실 HEIC 확인: collar 1371×628px, placket 300×4227px.
이어서 D-1 내부 계면 feather, D-2 chroma cast 정합, D-3 해상도 비례 저주파, D-5 landmark 관측,
D-6 mask 위생 게이트.

`fbf23e4` `981e95b` `f6ddca5` `9319c07` — Codex 3라운드 blocker 대응.

## 실 4K 결과 (attempt 1)
파이프라인이 warp 까지 완주. **decal 이 처음으로 실제 적용** — 이미지에 paprika 라벨·단추열·
원본 줄무늬 커프가 보인다. 그러나 육안 거절:
- 몸통 투영 줄무늬가 보호 부위(카라·커프)와 스케일·색이 안 맞음 → 한 벌로 안 보임
- carrier 실루엣이 케이프처럼 무너짐 (aspect 3.538 vs source 1.353) — Gemini 소산
- 하의 없음

**결정적 사실: 이번에 추가한 seam·chroma·drape·direction 게이트가 전부 통과했다.**
```
seam_ramp_excess 0.9585(<1.6) chroma 3.18(<14) severe 0.0(<0.02)
drape_local_amp_p2 0.7314(>0.30) direction 0.036(<0.10) color ΔE 7.79(<16)
outside_drift 0.0  period_rel_err 0.0039
```
잡은 것은 패널 strict 규칙뿐 (sleeve_l purity 0.659 / sleeve_r 0.900).
→ 결정론 QC 단독으로 사용 가능 판정 불가. 육안 게이트가 실제 방어선이다.

## 다음에 고칠 정확한 지점
1. **투영 몸통 ↔ decal 부위 스케일 정합** (`warp_composite.py` scale anchor / `texture_projection.py`)
   — 몸통 target_period 10.67px 는 repeats-on-torso 로 유도되고, decal 은 source 해상도를
   warp 한다. 두 경로의 최종 줄 폭이 일치하는지 재는 지표가 **없다**. 새 게이트 후보 1순위.
2. **carrier 기하 불량의 조기 차단** — `mask_aspect_ratio` 2.616 이 게이트 3.0 을 통과했으나
   실루엣은 육안 불량. Codex 가 2.552 는 정당하다고 증명해 3.0 으로 올린 상태라 여유가 좁다.
   실사진 분포 없이 더 조이면 오거절. 실측 축적 필요.
3. 캘리브레이션 — blinded label 0건. 임계 근거는 합성 6종 + 실표본 1건.

## 금지 사항 (유지)
main merge/rebase/reset/checkout, push/PR, 운영 DB 변경, 기존 migration 수정,
보호 dirty 수정, 실패 이미지를 정상 후보로 노출, 품질 기준 완화.

## 다음 세션 첫 명령
```
cd /Users/nojeong-un/devs/wearless_studio/server
open ab_out/frame_lock/stripe-projection-protected-v1/report.html
```
