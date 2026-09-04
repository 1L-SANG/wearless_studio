# FaceMarket 출처증명(Provenance) prod 배포 런북

설계: `docs/superpowers/specs/2026-09-04-facemarket-provenance-design.md`
계획: `docs/superpowers/plans/2026-09-04-facemarket-provenance.md`

3층 출처증명(①컷 원장 `fm_output_records` · ②C2PA 서명 `fm_publication_records` ·
③온체인 앵커 `FaceMarketProvenance.sol`)을 실제로 켜는 운영자용 순서다. 순서가 어긋나면
"배포는 성공했는데 아무 일도 안 일어난다"가 된다 — 에러도 경고도 없는 조용한 no-op 이
이 기능의 가장 흔한 실패 모드다. 각 단계에 **왜 그 순서인지**를 같이 적는다.

## 배포 원칙 — 반드시 CI 로만

🔴 **로컬에서 `copilot deploy`/`copilot-aws deploy` 를 직접 돌리지 않는다.** 2026-08-26 에
로컬 copilot 배포가 `.env` 평문과 4.7GB 실험 디렉터리를 그대로 프로덕션 이미지에 실은
사고가 있었다. 이 문서의 모든 "배포"는 `main` 에 머지된 커밋을 CI
(`.github/workflows/deploy-server.yml`)가 밀어야 실행된다. 매니페스트·시크릿 변경도
예외 없다 — 시크릿 **값**만 먼저 SSM 에 올리고, 매니페스트 **커밋**은 그 뒤 push 한다
(순서를 어기면 ECS 태스크가 참조하는 SSM 파라미터를 못 찾아 기동 자체가 실패한다 —
이 저장소에서 이미 여러 번 실측된 실패 모드다).

배포툴은 **`copilot-aws`**(일반 `copilot` 바이너리 아님). 리전 **`us-east-1`**, Copilot
환경명 **`use1`**(2026-08-26 서울 → us-east-1 컷오버 이후 정본. 서울 `prod` 환경은
롤백용으로 0대 상태로 남아 있고 더 이상 배포 대상이 아니다).

---

## 순서

### 1. C2PA 서명용 인증서 발급

```bash
cd server && uv run python scripts/gen_c2pa_cert.py --out-dir ./c2pa
```

`server/scripts/gen_c2pa_cert.py` 를 읽고 확인한 산출물(2026-09-04 기준, 4개 파일):

| 파일 | 용도 | 권한 |
|---|---|---|
| `c2pa_cert.pem` | 리프 인증서 → `FM_C2PA_CERT_PEM` | 0644 |
| `c2pa_key.pem`  | 리프 개인키 → `FM_C2PA_KEY_PEM`(운영 서명키) | **0600** |
| `c2pa_root_cert.pem` | 로컬 루트 CA 인증서(참고용) | 0644 |
| `c2pa_root_key.pem`  | 로컬 루트 CA 개인키 | **0600** |

**왜 루트가 따로 있나:** c2pa-python 0.37.8 은 리프가 self-signed(issuer==subject)면
서명 자체를 거부한다("the certificate is invalid"). 그래서 스크립트가 로컬 루트
CA(`c2pa_root_*`)를 하나 만들어 리프를 그 루트로 **발급**한다. SSM 에는 리프 두 장
(`c2pa_cert.pem`/`c2pa_key.pem`)만 올린다 — 서명 경로(`C2paSigner`)는 루트 파일을 아예
읽지 않는다.

```bash
aws ssm put-parameter --profile wearless --region us-east-1 \
  --name /copilot/wearless/use1/secrets/FM_C2PA_CERT_PEM \
  --type SecureString --value "$(cat server/c2pa/c2pa_cert.pem)"
aws ssm put-parameter --profile wearless --region us-east-1 \
  --name /copilot/wearless/use1/secrets/FM_C2PA_KEY_PEM \
  --type SecureString --value "$(cat server/c2pa/c2pa_key.pem)"
```

`aws ssm put-parameter` 로 직접 올리면 `copilot-application`/`copilot-environment`
태그가 안 붙어 ECS 태스크가 값을 못 읽는 사고가 이 저장소에 실제로 있었다(2026-07-17,
`SAM_INTERNAL_TOKEN`). 태그를 위 명령에 `--tags Key=copilot-application,Value=wearless
Key=copilot-environment,Value=use1` 로 직접 챙기거나, `copilot-aws secret init --name
FM_C2PA_CERT_PEM`(대화형, 태그 자동)을 쓴다.

🔑 **루트 키(`c2pa_root_key.pem`) 처리 — 반드시 읽을 것.** 루트는 리프 **발급 전용**이다.
이미 발급된 리프 서명은 루트 키 없이도 계속 유효하므로, 서명 경로 자체에는 루트가
필요 없다 — prod 어디에도 올리지 않는다. 인증서 갱신(재발급) 계획이 있다면 이 스크립트를
다시 돌려 새 루트+리프 쌍을 통째로 재발급하는 것이 유일한 경로이므로, 루트 키를 완전히
버리기보다 **오프라인(예: 팀 금고/1Password) 에 보관**해 두는 쪽을 권한다. 재발급
계획이 없다면 보관 없이 폐기해도 무방하다 — 어느 쪽이든 로컬 디스크에 그대로 두지 않는다.

🔴 **로컬 작업 사본 삭제.** `server/c2pa/` 는 개인키 두 장(0600, 리프+루트)을 평문으로
담고 있다. SSM 업로드를 확인한 뒤 `rm -rf server/c2pa`(또는 루트 키만 보관소로 옮기고
나머지 삭제)로 지운다. 커밋 전에 `git status` 로 `server/c2pa/` 가 스테이징되지
않았는지 직접 확인할 것 — 개인키 커밋은 즉시 회전(재발급) 대상이다.

### 2. `FaceMarketProvenance` 컨트랙트 배포

`contracts/FaceMarketProvenance.sol` 은 CI 가 없다 — `FaceMarketSettlement.sol` 과
같은 방식으로, OmniOne Chain 콘솔에 **단일 파일**을 그대로 업로드해서 배포한다.

- 배포자 계정은 **`FM_CHAIN_PRIVATE_KEY` 가 가리키는 계정과 같아야 한다.** 컨트랙트
  생성자가 `owner = msg.sender` 로 고정하고(`constructor()`), `recordPublication` 은
  `onlyOwner` 다. 다른 계정으로 배포하면 이후 서버가 영원히 `recordPublication` 을
  호출할 수 없다 — 재배포 외에는 복구할 방법이 없다.
- 배포된 주소를 `FM_PROVENANCE_ADDRESS` 로 SSM 에 올린다:

```bash
aws ssm put-parameter --profile wearless --region us-east-1 \
  --name /copilot/wearless/use1/secrets/FM_PROVENANCE_ADDRESS \
  --type SecureString --value "0x..."
```

값 자체는 공개 정보(체인 익스플로러에서 누구나 본다)지만, 배포 전까지는 값이 없고
컨트랙트를 재배포하면 값이 바뀐다 — 매니페스트를 다시 커밋하지 않고 SSM 값만 갱신해
반영할 수 있어야 하므로 평문 `variables:` 대신 `secrets:`(SSM SecureString 참조)로
배선한다. 기존 `FM_SETTLEMENT_ADDRESS`(**별개 컨트랙트** — 정산용,
`FaceMarketSettlement.sol`)는 이미 평문 `variables:` 로 배선돼 있으니 혼동하지 말 것;
이번 태스크는 그 값을 건드리지 않는다.

RPC 호스트 함정(실측 2026-07-13): OmniOne Chain 게이트웨이 호스트는 `test.` 를 **뺀**
주소다. chainId 는 **`201210`**.

### 3. ⚠️ 체인 3종 — "prod 에 없다"가 지금도 참인지 직접 확인할 것

`FM_CHAIN_RPC_URL` · `FM_SETTLEMENT_ADDRESS` · `FM_CHAIN_PRIVATE_KEY` 셋 다 있어야
`FaceMarketChain.from_settings`(`server/app/facemarket_chain.py`)가 활성 recorder 를
돌려준다. 하나라도 없으면(빈 문자열 포함) `from_settings` 가 `None` 을 돌려주고
**③앵커 계층 전체가 조용한 no-op 이 된다** — 에러도, 실패한 헬스체크도 없다. 서명·
다운로드는 정상으로 보이고, `chain_status` 만 영원히 `pending` 에 머문다. 이게 "기능을
켰는데 아무 일도 안 일어난다"의 가장 유력한 원인이다.

**이 태스크가 사전 브리핑과 다르게 실제로 발견한 것:** `copilot/api/manifest.yml`·
`copilot/detail-worker/manifest.yml` 은 이미 2026-07-20(커밋 `bd70d267`,
`d1fa9dd4`)부터 `FM_CHAIN_RPC_URL`·`FM_CHAIN_PRIVATE_KEY` 를 `secrets:` 로,
`FM_CHAIN_ID`·`FM_SETTLEMENT_ADDRESS` 를 `variables:` 로 이미 참조하고 있다 —
"매니페스트에 아예 안 쓰여 있다"는 더 이상 사실이 아니다. 그렇다고 안심할 일도 아니다.
진짜 리스크는 두 가지로 바뀌었을 뿐이다:

1. **환경 컷오버로 SSM 값 자체가 비어 있거나 안 옮겨졌을 수 있다.** 2026-08-26 에
   서울(옛 `prod`, `ap-northeast-2`)에서 `us-east-1`(`use1`)로 Copilot 환경 자체를
   옮겼다. 매니페스트의 `${COPILOT_ENVIRONMENT_NAME}` 템플릿은 새 환경에서
   `/copilot/wearless/use1/secrets/FM_CHAIN_RPC_URL` 을 찾는다. api 서비스가 `use1`
   에서 이미 정상 기동 중이라는 사실은(다른 필수 시크릿도 같은 경로 규칙을 쓰므로)
   이 값도 SSM 에 **존재는** 한다는 강한 정황이지만, 그 값이 **지금도 유효한
   RPC/개인키인지**는 컷오버 작업이 체인 시크릿까지 챙겼는지에 달려 있고, 이 태스크는
   AWS 를 조회할 권한이 없어 직접 확인하지 못했다 — 배선 검증(§켠 뒤 확인) 전까지
   가정하지 말 것.
2. **기존 프로비저닝 스크립트가 옛 환경을 하드코딩하고 있다.** `scripts/fm-chain-secrets.sh`
   는 `REGION=ap-northeast-2`·`PREFIX=/copilot/wearless/prod/secrets` 를
   **하드코딩**한다(2026-07-20 작성, 컷오버 이전이라 `use1` 을 모른다). 이 스크립트를
   지금 그대로 돌리면 서울의 이미 폐기 대상인 `prod` 환경 SSM 에 값을 쓰고, 실제로
   서비스 중인 `use1` 은 전혀 바뀌지 않는다 — 실행한 사람은 "처리했다"고 믿는데
   프로덕션은 그대로인, 두 번째 조용한 실패 지점이다. `use1`/`us-east-1` 용으로 값을
   새로 넣거나 갱신하려면 이 스크립트를 쓰지 말고 `copilot-aws secret init --name
   FM_CHAIN_RPC_URL`(환경 선택 프롬프트가 뜬다) 또는 리전·경로를 `use1`/`us-east-1`
   로 바꾼 `aws ssm put-parameter` 를 직접 쓴다.

**존재 여부만 값 노출 없이 확인:**

```bash
aws ssm get-parameter --profile wearless --region us-east-1 \
  --name /copilot/wearless/use1/secrets/FM_CHAIN_RPC_URL --with-decryption \
  --query Parameter.LastModifiedDate
```

날짜만으로는 "작동하는 값인지"까지 보장 못 한다 — 최종 확인은 반드시 **켠 뒤 확인(§7)**
에서 `chain_status: pending → confirmed` 전환을 직접 눈으로 보는 것이다. 그게 유일한
진짜 증거다.

### 4. R2 버킷 CORS

브라우저가 배포본을 presigned PUT 으로 직접 R2 에 올린다(`server/app/r2.py:131
presigned_put()`, 버킷 `wearless`). 버킷에 **CORS** 규칙이 없으면 브라우저 preflight
(`OPTIONS`)가 막혀 업로드 자체가 실패한다 — 그런데 `POST
/v1/facemarket/publications/presign` 호출 자체는 성공했으므로 서버 로그에는 아무 에러도
안 남는다. 그 결과 셀러는 **경고 문구만 보고 다운로드는 원본(공증 없는) 그대로**
받는다 — 눈에 보이는 에러가 아니라 조용한 품질 저하다.

Cloudflare R2 대시보드(또는 API)에서 버킷 `wearless` 에 CORS 규칙을 추가한다:

```json
[{"AllowedOrigins": ["https://ai.wearless.kr", "https://facemarket.wearless.kr"],
  "AllowedMethods": ["PUT", "GET"],
  "AllowedHeaders": ["content-type"],
  "MaxAgeSeconds": 3600}]
```

두 origin 을 다 넣는다 — `/verify/p/:publicationId` 라우트가 `src/apps/seller/App.jsx`
와 `src/apps/facemarket/App.jsx` 양쪽에 등록돼 있고, 다운로드/업로드를 실제로 트리거하는
화면이 어느 프론트에서 뜨는지는 이 태스크(백엔드·배포 설정 전용) 범위에서 프론트
배선까지는 확인하지 못했다. CORS 는 origin 을 넉넉히 허용해도 보안 위험이 크지 않은
쪽이니 좁혀서 재발 위험을 만들지 말 것.

### 5. `PUBLIC_WEB_ORIGIN` — 아무것도 켜기 전에 먼저 확인

`server/app/facemarket_provenance.py` 가 서명 시점에 아래 값을 만들어 C2PA 매니페스트
안에 **영구히** 박는다:

```python
verify_url = f"{s.public_web_origin}/verify/p/{publication_id}"
```

`/verify/p/:publicationId` 는 프론트 라우트(`src/apps/seller/App.jsx`,
`src/apps/facemarket/App.jsx` 둘 다 등록)이고, `PublicVerifyPublication.jsx` 가 그걸
렌더한다. `config.py` 의 `public_web_origin` 기본값은 `"https://wearless.kr"` —
이 도메인이 실제로 Vercel 프로젝트(`vercel.json`: `admin.wearless.kr`→admin.html,
`facemarket.wearless.kr`→facemarket.html, **그 외 모든 host**→seller.html)에 연결돼
seller 번들을 서빙하는지 **배포 전에 직접 열어서** 확인한다. 값이 틀리면 이미 서명·
배포된 모든 파일 안 링크가 죽은 채로 남고, 그 파일들은 셀러가 이미 받아간 이상 회수할
방법이 없다.

```bash
open https://wearless.kr/verify/p/00000000-0000-0000-0000-000000000000
```

("기록을 찾을 수 없습니다"/404 응답은 정상이다 — 그 UUID 가 실존하지 않을 뿐이다.
확인할 것은 **seller 앱 화면 자체가 뜨는가**다. DNS 에러·다른 앱 화면·빈 화면이면
`PUBLIC_WEB_ORIGIN` 값을 바로잡기 전에는 아래 어느 것도 켜지 않는다.)

```yaml
PUBLIC_WEB_ORIGIN: "https://wearless.kr"
```

### 6. `FM_PROVENANCE_ENABLED=true` — 맨 마지막

위 1~5 를 전부 확인한 뒤에만 켠다. `server/app/main.py` 가 이 플래그 하나로 세 가지를
동시에 켠다:

- `/v1/facemarket/publications/*` 라우트 등록(꺼져 있으면 404 — 회귀 테스트
  `test_routes_absent_when_flag_off` 가 이걸 잠근다)
- `PublicationAnchorReconciler` 워커 기동(층③ 앵커 폴링)
- `C2paSigner.from_settings` 로 서명기 활성화(cert/key 둘 다 있어야 진짜 활성 — 하나만
  있으면 `c2pa_status='skipped'` 로 조용히 넘어간다)

**층①(컷 원장 `fm_output_records`)은 이 플래그와 무관하게 이미 항상 켜져 있다** — REAL
라이선스 컷이 생성될 때마다 `finalize_detail_page_success` 트랜잭션 안에서 insert
된다(`server/app/repo.py:insert_output_records`, 호출부
`server/app/workers/detail_page_job.py`). 이 플래그가 게이팅하는 건 ②서명·③앵커
뿐이다 — 이 사실은 롤백 절에서 다시 중요해진다.

매니페스트는 배선 검증 전까지 반드시 `"false"` 로 배포한다:

```yaml
FM_PROVENANCE_ENABLED: "false"   # 배선 검증 후 true 로 올린다
```

### 7. 마이그레이션

`supabase/migrations/20260904000000_facemarket_provenance.sql` 이 층①②③ 테이블을
만든다. ⚠️ **2026-08-29 에 CI 시크릿 `SUPABASE_DB_URL`**(`.github/workflows/deploy-server.yml`
의 `supabase db push --db-url "$SUPABASE_DB_URL"`)이 **앱이 실제로 쓰는 DB가 아닌 옛
DB** 를 가리키고 있어서 마이그레이션이 조용히 prod 에 안 붙었고, 그게 그대로 prod
장애로 번졌다. 앱 DB 는 **`ftjxwxuactfjopbokbni`**(`copilot/api/manifest.yml` 의
`SUPABASE_URL: https://ftjxwxuactfjopbokbni.supabase.co` 로 재확인 가능) — `server/.env`
에 적힌 프로젝트가 아니다(라이브 = `ftjxwxuactfjopbokbni`, 옛것 = `pedonlvyhoyedzdmmwco`,
`server/.env` 는 옛것을 가리킨다).

적용 전 반드시 **눈으로** GitHub Actions 시크릿 `SUPABASE_DB_URL` 이 가리키는 프로젝트
ref 가 `ftjxwxuactfjopbokbni` 인지 확인한다. GitHub UI 는 시크릿 값을 다시 보여주지
않으므로, (a) 시크릿을 새로 설정하는 사람이 값을 붙여넣기 직전 프로젝트 ref 를 육안
대조하거나, (b) `supabase projects list`(로컬 CLI, `ftjxwxuactfjopbokbni` 접근 권한이
있는 계정으로 로그인)로 그 프로젝트가 실제로 존재/접근 가능한지 확인한다. CI 마이그레이션
스텝은 시크릿이 **비어 있으면** `exit 1` 로 실패하도록 이미 짜여 있다(조용한 skip 은
없다) — 하지만 **값이 있는데 잘못된 DB 를 가리키는 경우**까지는 못 잡는다. 그게
2026-08-29 사고의 정확한 모양이었다.

---

## 켠 뒤 확인 (post-enable)

1. REAL 모델로 상세페이지 컷 N 장을 생성한다 →
   `select count(*) from fm_output_records where job_id = '<job_id>'` 가 N 과 같은가.
2. 상세페이지에서 파일을 다운로드(공증 트리거)한다 → `fm_publication_records` 에 1행이
   생기고 `c2pa_status = 'signed'` 인가.
3. 받은 파일을 C2PA 검증 도구(예: `c2patool`, 또는 https://contentcredentials.org/verify)
   에 넣어 매니페스트가 파싱되고 `modelId`·`licenseId`·`verifyUrl` 클레임이 보이는지
   확인한다. **`signingCredential.untrusted`(발급자 미확인) 표시는 정상이다** — 자체
   서명 루트가 공개 신뢰 목록에 없어서 나오는, 알려진 한계다
   (`server/app/services/c2pa_signer.py` 클래스 docstring 참고). 매니페스트 내용은
   읽히고 변조 감지는 그대로 동작한다 — 이걸 "서명 실패"·"배포 실패"로 오판하지 말 것.
4. `fm_publication_records.chain_status` 를 몇 초 ~ 최대 90여 초 간격으로 재조회한다 —
   `pending` → `confirmed` 로 바뀌는가(`PublicationAnchorReconciler` 유휴 주기 5초 +
   `FaceMarketChain._CONFIRM_TIMEOUT` 최대 90초가 상한). 안 바뀌면 §3 의 체인 3종부터
   의심한다 — 조용한 no-op 의 유일한 관측 가능한 증상이 이것이다.
5. 매니페스트 안 `verifyUrl` 을 실제 publicationId 로 열어 공개 검증 페이지가 뜨는지
   최종 확인한다(§5 에서 미리 한 확인의 실전 재확인).

## 롤백

`FM_PROVENANCE_ENABLED=false` 로 되돌리면 `/v1/facemarket/publications/*` 라우트가
다시 미등록되고, 앵커 워커가 뜨지 않고, 서명기가 비활성화된다 — 이 기능이 없던
이전(prior) 동작으로 정확히 돌아간다. **되돌린다고 데이터가 지워지지는 않는다**: 이미
쓰인 `fm_output_records`·`fm_publication_records` 행, 이미 체인에 기록된
`recordPublication` 트랜잭션은 전부 그대로 남는다 — 깨끗한 되돌리기를 기대하지 말 것.
롤백은 "더 이상 새로 만들지 않는다"이지 "지금까지 만든 걸 지운다"가 아니다. 또한 §6 에서
적었듯 층①(원장 insert)은 이 플래그와 무관하게 계속 동작한다 — 롤백해도 REAL 라이선스
컷의 `fm_output_records` 적재는 멈추지 않는다.

---

## 부록 — 매니페스트에 실제로 추가/확인한 항목

`copilot/api/manifest.yml`·`copilot/detail-worker/manifest.yml` 둘 다 동일하게(라우트는
api 가, 앵커 reconciler 는 api 의 lifespan 이 돌지만 detail-worker 도 같은 이미지·같은
설정 로딩 경로를 쓰므로 한쪽만 넣으면 기동 시 두 서비스의 설정이 갈린다):

```yaml
secrets:
  FM_C2PA_CERT_PEM: /copilot/${COPILOT_APPLICATION_NAME}/${COPILOT_ENVIRONMENT_NAME}/secrets/FM_C2PA_CERT_PEM
  FM_C2PA_KEY_PEM: /copilot/${COPILOT_APPLICATION_NAME}/${COPILOT_ENVIRONMENT_NAME}/secrets/FM_C2PA_KEY_PEM
  FM_PROVENANCE_ADDRESS: /copilot/${COPILOT_APPLICATION_NAME}/${COPILOT_ENVIRONMENT_NAME}/secrets/FM_PROVENANCE_ADDRESS

variables:
  FM_PROVENANCE_ENABLED: "false"   # 배선 검증 후 true 로 올린다
  PUBLIC_WEB_ORIGIN: "https://wearless.kr"
```

**이미 있던 것(이번 태스크에서 손대지 않음, 2026-07-20 커밋 `bd70d267`/`d1fa9dd4`부터):**
`FM_CHAIN_RPC_URL`·`FM_CHAIN_PRIVATE_KEY`(`secrets:`), `FM_CHAIN_ID`·
`FM_SETTLEMENT_ADDRESS`(`variables:`, 값 `"201210"`/`"0x39445B04d8F588Ea5E447ff03D8A4b253a6d67A3"`).
§3 이 설명하듯 "매니페스트 참조 존재"와 "SSM 값이 `use1` 환경에서 유효"는 별개 질문이다.
