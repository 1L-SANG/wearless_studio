# FaceMarket 협찬 동의 VC 배포

협찬을 켜면 협찬 동의 VC(`fmsponsorship-v1`)를 발급하고, 끄면 폐기한다. 설계는
`docs/superpowers/specs/2026-09-25-facemarket-sponsorship-vc-design.md`.

| 항목 | 값 |
|---|---|
| holder 요청 plan | `fmsponsorship-v1` |
| Issuer vcPlanId | `vcplanspons000000001` (20자, `issue_profile.vc_plan_id` varchar(20)) |
| vc schema / namespace | `fmsponsorship-v1` / `kr.wearless.fmsponsorship.v1` |
| 멱등키 | `fm-sponsorship:{credential uuid}` |
| 클레임 6개 | `modelDid` `credentialId` `consentDocVersion` `participationDocSha256` `profileDocSha256` `consentedAt` |
| 스위치 | `FM_SPONSORSHIP_VC=off\|on` (api, 기본 off) |

라이선스 VC(`facelicense-v2`)의 namespace·schema·plan·멱등 기록은 건드리지 않는다. holder 의 라이선스
멱등 digest 도 바이트 그대로라 EFS 에 남은 기존 결과는 그대로 재생된다.

## 순서가 중요한 이유

⚠️ 2026-09-13 사고: PR #280 이 API·Holder·프로비저닝을 한 PR 에 담았는데 API 만 CI 로 먼저 나가
하루 동안 신규 라이선스 발급이 전부 503(`unknown plan 'facelicense-v2'`)이었다. opendid(holder)는
**CI 가 안 낸다**(수동 배포). 그래서 이번엔 스위치를 끈 채로 API 를 먼저 내고, holder 계약(Issuer 플랜 +
새 holder)을 갖춘 **뒤에만** 스위치를 켠다. 켜진 API 가 옛 holder 를 부르면 발급은 실패·재시도만 쌓인다
(토글 자체는 성공하지만 VC 가 안 생긴다).

## 1. API 배포 (스위치 off)

PR 머지 → CI 배포. `copilot/api/manifest.yml` 에 `FM_SPONSORSHIP_VC` 가 없거나 `"off"` 인지 확인한다.
off 면 동작은 지금과 같다(증서 행·게이트 없음). 마이그레이션(`fm_sponsorship_credentials`,
`fm_vc_revocation_jobs.kind`)이 앱 DB 에 붙었는지 확인한다.

## 2. Issuer 에 협찬 플랜 프로비저닝 (ECS exec)

opendid 태스크가 1대 떠 있어야 한다(0대면 `desired=1` 로 먼저 깨운다). 멱등이라 재실행해도 안전하다.

```sh
AWS_PROFILE=wearless AWS_REGION=us-east-1 DRY_RUN=1 bash scripts/issuer-provision-sponsorship-ecs.sh   # 읽기만
AWS_PROFILE=wearless AWS_REGION=us-east-1 bash scripts/issuer-provision-sponsorship-ecs.sh
```

`sponsorship_plan=present` 가 나와야 한다(TAS `list_vc_plan` 에 `vcplanspons000000001` 연결 확인).
ECS exec 세션이 ~1KB 에서 끊기는 문제는 스크립트가 재조회로 흡수한다. 로컬/docker 호스트는
`PG_USER=… bash scripts/issuer-provision-sponsorship.sh`.

## 3. holder 수동 배포

JDK 21 로 테스트·빌드 후, 기존 opendid 이미지 절차 그대로 교체한다. Holder data-dir(EFS)은 유지한다.

```sh
cd services/fm-holder && ./gradlew test bootJar --no-daemon && cd -
TAG=$(git rev-parse --short=8 HEAD)
DOCKER_DEFAULT_PLATFORM=linux/amd64 IMAGE=439328746001.dkr.ecr.us-east-1.amazonaws.com/wearless/opendid:$TAG \
  bash deploy/opendid/container/build.sh
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin 439328746001.dkr.ecr.us-east-1.amazonaws.com
docker push 439328746001.dkr.ecr.us-east-1.amazonaws.com/wearless/opendid:$TAG
# copilot/opendid/manifest.yml 의 image.location 태그를 $TAG 로 바꾼 뒤(반드시 최신 main — EFS 지갑 마운트 포함):
AWS_PROFILE=wearless AWS_REGION=ap-northeast-2 copilot-aws svc deploy --name opendid --env use1
```

맥(arm64)은 `linux/amd64` 강제 필수. 앱 메타데이터가 서울 리전이라 copilot 은 `ap-northeast-2` 로 부른다.
manifest 태그 변경은 커밋해 둔다(다음 배포가 옛 이미지로 되돌리지 않게).

배포 뒤 라이선스 회귀부터 본다: 기존 모델 하나로 라이선스 재발급 요청이 같은 vcId 를 돌려주는지.

## 4. 스위치 켜기 + 스모크

`copilot/api/manifest.yml` `variables` 에 `FM_SPONSORSHIP_VC: "on"` → PR → CI 배포.
발급 워커는 api 에서만 돈다(detail-worker 아님). 승인된 테스트 모델(노*운)로:

1. 모델 화면에서 협찬 **켜기** → 30초 안팎에 증서가 `pending` → `active`, `vc_id` 채워짐.
   ```sql
   select status, vc_id, attempts, last_error_code from fm_sponsorship_credentials
   where model_id = '<model uuid>' order by created_at desc limit 3;
   ```
2. 공개 검증 `GET /verify/{license_id}` 응답에 `sponsorship.active = true`, 셀러 협찬 요청이 열린다.
3. 협찬 **끄기** → 증서 `revoked`, `fm_vc_revocation_jobs` 에 `kind='sponsorship'` 잡이 생기고 완료된다.
4. holder `POST /holder/vc/verify {vcId}` → `status: "revoked"`, 검증 페이지 `sponsorship` 은 `null`/비활성.

`attempts` 가 계속 오르면 `last_error_code` 와 opendid 로그(`issue-vc FAILED`)를 본다. 흔한 원인:
플랜 미프로비저닝(2단계 누락), 옛 holder(3단계 누락 → 400/500 `unknown plan`), 모델 DID 없음(라이선스 VC 전 — 대기가 정상).

## 롤백

`FM_SPONSORSHIP_VC` 를 `"off"` 로 되돌리면 게이트·신규 발급이 멈춘다. 이미 발급된 VC 와 Issuer 리소스,
holder 멱등 기록은 지우지 않는다. 같은 `fm-sponsorship:` 키를 다른 클레임으로 다시 발급하지 않는다(holder 가 충돌로 거절한다).
