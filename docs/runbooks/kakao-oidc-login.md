# 카카오 로그인 — OpenID Connect 전환 런북

코드: `server/app/kakao_oidc.py` · `src/lib/kakaoOidc.js` · `src/features/auth/KakaoCallback.jsx`
테스트: `server/tests/test_kakao_oidc.py` · `tests/frontend/kakao-oidc-login.test.mjs`

플래그 `VITE_KAKAO_OIDC_ENABLED` 가 `false` 인 동안에는 **종전 동작 그대로**다(카카오 버튼이 `supabase.auth.signInWithOAuth('kakao')` 를 부른다). 서버 라우트는 플래그와 무관하게 항상 배포되고, 키가 없으면 503 으로 거절한다.

---

## 0. 무슨 일이 있었나 (되돌리기 전에 읽어라)

2026-09-22, 카카오 로그인이 **전원 KOE205** 로 죽었다.

> KOE205 = "앱에 설정하지 않은 동의항목을 포함해 인가 코드를 요청했다"

원인은 두 사실이 겹친 것이다.

1. **카카오 콘솔 실측**: `profile_nickname` 필수 동의 ✅ · `profile_image` 필수 동의 ✅ · **`account_email` = "권한 없음"** ❌ (비즈앱 전환 + 추가기능 심사 3~5영업일 필요)
2. **Supabase GoTrue 는 카카오 scope 를 하드코딩해서 붙인다** — `auth/internal/api/provider/kakao.go` 가 `account_email profile_image profile_nickname` 을 **append**(replace 아님)한다. 클라이언트에서 뺄 방법이 없다.

prod 실측 authorize 302:

```
https://kauth.kakao.com/oauth/authorize
  ?client_id=7b78ab8b674afa91063744476ab946e9
  &scope=account_email+profile_image+profile_nickname      ← 여기 account_email 이 KOE205
  &redirect_uri=https://ftjxwxuactfjopbokbni.supabase.co/auth/v1/callback
```

그래서 **카카오만** Supabase 의 OAuth provider 경로를 버리고 OpenID Connect 로 직접 로그인한다. 구글은 한 줄도 안 바뀐다.

```
로그인 버튼 → kauth authorize(scope=openid)
            → /auth/kakao/callback (우리 라우트)
            → POST /v1/auth/kakao/token  (우리 서버가 code → id_token 교환)
            → supabase.auth.signInWithIdToken({ provider:'kakao', token, nonce })
            → 세션
```

---

## 1. Supabase 대시보드 — **끄지 마라, 바꾸지 마라**

🔴 **Kakao provider 는 계속 Enabled 이고 client_id 도 지금 값 그대로여야 한다.**

GoTrue 의 `internal/api/token_oidc.go` 가 이렇게 분기한다:

```go
case p.Provider == KakaoProvider || p.Issuer == provider.IssuerKakao:
    cfg = config.External.Kakao
    acceptableClientIDs = []string{config.External.Kakao.ClientID}
```

즉 우리가 보낸 **id_token 의 `aud` 를 대시보드의 client_id 와 대조**한다. provider 를 Disabled 로 바꾸거나 client_id 를 건드리면 `signInWithIdToken` 이 전부 실패한다. 프런트에서 `signInWithOAuth` 호출부가 안 불린다고 해서 설정을 정리하지 말 것.

그리고 **우리 서버·프런트의 카카오 키는 그 값과 같은 값**이어야 한다:

| 곳 | 이름 | 값 |
|---|---|---|
| Supabase 대시보드 | Kakao provider client_id | `7b78ab8b674afa91063744476ab946e9` |
| 서버(ECS) | `KAKAO_REST_API_KEY` | 같은 값 |
| 프런트(Vercel) | `VITE_KAKAO_REST_API_KEY` | 같은 값 |

세 값이 하나라도 갈리면 로그인이 전부 실패한다. `server/tests/test_deploy_manifest_qc_flags.py` 가 매니페스트 쪽 미선언·형식만 잡아 준다(값 대조는 사람 몫이다 — 값을 여러 곳에 복제하면 회전할 때 한쪽만 바뀐다).

---

## 2. 카카오 콘솔 준비

[카카오 developers](https://developers.kakao.com) → 내 애플리케이션 → 해당 앱.

### 2.1 OpenID Connect 활성화

**제품 설정 > 카카오 로그인** 화면 하단의 **OpenID Connect** 를 **ON** 으로 바꾼다.

확인 방법(브라우저에서 바로 열린다):

```
https://kauth.kakao.com/.well-known/openid-configuration
```

2026-09-22 실측:

```
issuer                                  https://kauth.kakao.com
token_endpoint                          https://kauth.kakao.com/oauth/token
token_endpoint_auth_methods_supported   ["client_secret_post"]
code_challenge_methods_supported        ["S256"]
claims_supported                        [iss, aud, sub, auth_time, exp, iat, nonce,
                                         nickname, picture, email]
```

`claims_supported` 에 **`at_hash` 가 없다** ⇒ `signInWithIdToken` 에 access_token 을 같이 넘길 필요가 없다(그래서 안 넘긴다 — 넘기려면 카카오 access_token 을 브라우저로 내보내야 한다).

### 2.2 Redirect URI 등록

**제품 설정 > 카카오 로그인 > Redirect URI** 에 아래 **5개를 추가**한다.

⚠️ 2026-09-22 실측: 이 키에 등록돼 있던 유일한 줄은 **옛 Supabase 프로젝트**(`pedonlvyhoyedzdmmwco`)였다. 프런트가 보는 라이브 프로젝트는 `ftjxwxuactfjopbokbni` 다(prod 번들 `AppProviders-*.js` 에서 확인). us-east-1 이전 때 카카오 콘솔만 안 따라온 것 — 즉 `account_email`(KOE205)을 고쳤어도 그 다음엔 **KOE006(미등록 redirect_uri)** 으로 죽었을 상태였다. 옛 줄은 다른 데서 쓰고 있을 수 있으니 확인 전에는 **지우지 마라**.

```
https://ai.wearless.kr/auth/kakao/callback
https://facemarket.wearless.kr/auth/kakao/callback
https://admin.wearless.kr/auth/kakao/callback
http://localhost:5173/auth/kakao/callback
https://ftjxwxuactfjopbokbni.supabase.co/auth/v1/callback   ← 라이브 Supabase(= 롤백 경로). 지금 등록한다
(이미 있는 줄, 확인 전 삭제 금지) https://pedonlvyhoyedzdmmwco.supabase.co/auth/v1/callback   ← 옛 프로젝트
```

- **호스트마다 한 줄인 이유**: 프로덕션 3호스트는 `.wearless.kr` 공유 쿠키라 세션은 나눠 쓰지만, PKCE verifier·nonce·state 와 로그인 복귀 목표(`wl_postLogin`)는 sessionStorage = **오리진 한정**이다. 콜백은 로그인을 시작한 그 오리진으로 돌아와야 한다.
- **`127.0.0.1` 로 등록하지 마라** — `src/apps/AppProviders.jsx` 가 루프백 접속을 `localhost` 로 정규화한다. 카카오는 정확 일치만 허용하므로 표기를 `localhost` 로 통일한다.
- 병렬 워크트리(`VITE_DEV_PORT=5174`)로 QA 할 일이 생기면 `http://localhost:5174/auth/kakao/callback` 을 콘솔에 더하고, 서버 env `KAKAO_REDIRECT_URIS` 에도 같이 넣는다(서버가 화이트리스트로 다시 검증한다).

### 2.3 동의항목 — `account_email` 은 건드리지 않는다

scope 에는 **`openid` 만** 보낸다. 필수 동의(`profile_nickname`·`profile_image`)는 카카오가 동의 화면에 알아서 포함하므로 적지 않아도 된다.

**`account_email` 을 scope 에 다시 넣는 순간 KOE205 가 그대로 재발한다.** 이메일이 필요해지면 순서는 이렇다:

1. 비즈앱 전환 → 2. 추가기능(이메일) 심사 신청 → 3. **승인(영업일 3~5일)** → 4. 카카오 콘솔에서 `account_email` 이 "권한 없음" 이 아닌 상태가 된 것을 눈으로 확인 → 5. 그때 `src/lib/kakaoOidc.js` 의 `KAKAO_SCOPE` 를 `'openid account_email'` 로 바꾼다.

승인 전에 4번을 건너뛰고 5번을 하면 로그인이 다시 전원 죽는다.

### 2.4 Client Secret — **쓴다** (콘솔이 '사용함' 상태)

카카오 콘솔의 **보안 > Client Secret** 이 이미 '사용함'이다. 그 상태에서는 토큰 교환에 `client_secret` 이 **필수**라, 빼면 카카오가 401 로 거절한다. 그래서 SSM 에 넣고 쓴다. PKCE(S256)도 같이 보낸다 — 둘은 배타가 아니다.

끄는 선택지도 있었지만(끄면 PKCE 가 그 자리를 대신하고 SSM 파라미터가 아예 필요 없다) 택하지 않았다: 콘솔에서 **코드 삭제·재발급을 누르는 순간 값이 바뀌어** Supabase 대시보드 Kakao provider 에 저장된 시크릿도 같이 무의미해지고, 플래그 롤백(= 기존 signInWithOAuth 경로로 되돌리기)이 흔들린다.

**순서를 반드시 지켜라** — SSM 값보다 매니페스트를 먼저 배포하면 ECS 태스크가 기동조차 못 하고 롤백된다(2026-07-17 실경험, 매니페스트 주석에 박혀 있다):

```bash
copilot-aws secret init --name KAKAO_CLIENT_SECRET     # ① 값을 먼저 만든다
# ② 그다음 manifest.yml secrets: 에 줄을 추가하고 배포
```

2026-09-23 이 순서대로 했다. `use1`(라이브, us-east-1)에 SecureString 생성 확인:

```bash
AWS_PROFILE=wearless aws ssm get-parameter --region us-east-1 \
  --name /copilot/wearless/use1/secrets/KAKAO_CLIENT_SECRET \
  --query 'Parameter.[Name,Type]' --output text     # 값은 안 찍는다(--with-decryption 금지)
```

⚠️ `prod` 환경(서울, 롤백용 0대)에는 값이 **없다.** 그 환경으로 배포할 일이 생기면 먼저 만들어라 — 안 그러면 기동 실패한다.

`aws ssm put-parameter` 로 직접 만들면 copilot 태그가 없어 태스크가 파라미터를 못 읽는다. 서버 코드는 양쪽을 지원한다 — `KAKAO_CLIENT_SECRET` 이 있으면 `client_secret_post` 로 함께 보내고, 없으면 PKCE 만으로 간다(`test_client_secret_is_sent_only_when_configured`). 콘솔에서 재발급하면 SSM 값도 같이 갱신해야 한다.

---

## 3. 배포 순서

프런트(Vercel)와 서버(ECS `deploy-server.yml`)는 **독립적으로** 나간다. 프런트가 먼저 나가면 그 사이 카카오 로그인이 전부 실패하므로 순서를 지킨다.

| # | 하는 일 | 확인 |
|---|---|---|
| 1 | 서버 env — `copilot/api/manifest.yml` 의 `KAKAO_REST_API_KEY` 가 Supabase client_id 와 같은지 보고, `KAKAO_CLIENT_SECRET` SSM 값이 **이미 있는지** 확인한다(§2.4) | 값 육안 대조 + `aws ssm get-parameter` 가 이름을 돌려주는지 |
| 2 | 서버 배포 (`deploy-server.yml`) | `curl -s -o /dev/null -w '%{http_code}' -X POST https://api.wearless.kr/v1/auth/kakao/token -H 'content-type: application/json' -d '{"code":"x","redirectUri":"https://ai.wearless.kr/auth/kakao/callback"}'` → **400**(`kakao_token_exchange_failed`)이면 배선 OK. **503** 이면 키 미설정, **404** 면 아직 안 나갔다 |
| 3 | 카카오 콘솔 — OIDC ON + Redirect URI 4개 등록 (§2) | discovery URL 이 열리는지 |
| 4 | 프런트 머지 (Vercel 자동 배포). 이때 플래그는 아직 OFF | `/auth/kakao/callback` 직접 열면 "로그인 정보를 찾지 못했어요" 화면 |
| 5 | Vercel 환경변수 `VITE_KAKAO_OIDC_ENABLED=true` + `VITE_KAKAO_REST_API_KEY=<client_id>` (스코프 **Production**) → **Redeploy** | 빌드타임 인라인이라 재배포 필수. 확인은 번들 grep: `curl -s https://ai.wearless.kr/ | grep -o '/assets/AppProviders-[^"]*\.js'` 로 청크를 찾아 받고 `grep -c '<client_id>'` 가 1 이어야 한다. 값이 안 박히면 `import.meta.env` 자리가 `{}` 로 남는다 — 2026-09-22 실제로 그 상태로 한 번 배포됐다 |
| 6 | §4 검증 | |

### 롤백

**Vercel 환경변수 `VITE_KAKAO_OIDC_ENABLED` 를 `false` 로 바꾸고 Redeploy.** 그게 전부다.

되돌아갈 자리(`supabase.auth.signInWithOAuth`)는 `AuthProvider.signIn` 에 그대로 남아 있고, 카카오 콘솔의 Supabase 콜백 URI 와 대시보드 provider 도 살아 있다. **셋 중 하나라도 지우면 롤백이 죽는다.**

서버 라우트는 롤백 대상이 아니다 — 플래그로 숨기지 않고 항상 등록하되 키가 없으면 503 을 준다(프런트가 404 를 '미배포'와 구분하지 못하는 상황을 만들지 않으려는 레포 관례).

---

## 4. 검증 — 🔴 기존 계정이 그대로 붙는지 **반드시 실측**

### 4.1 이게 왜 배포 차단 조건인가

기존 OAuth 경로가 만든 identity 의 `provider_id` 는 GoTrue 가 `strconv.Itoa(kakao user id)` 로 넣은 **카카오 회원번호**다. OIDC 경로는 `parseKakaoIDToken` 이 `token.Subject`(= `sub` 클레임)로 넣는다.

카카오 회원번호는 (앱, 사용자) 조합마다 고유해서 `sub` 과 같은 값일 **것으로 보이지만, 아직 실측 전이다.** 다르면 기존 카카오 유저 **전원에게 새 계정이 생긴다** — 사용자 눈에는 프로젝트·크레딧·모델 등록이 통째로 사라진 것으로 보인다.

### 4.2 플래그 ON 직후 30분 안에 할 것

카카오로 이미 가입해 본 **테스트 계정 하나**로 한다.

1. 플래그 ON 직전, 그 계정의 현재 상태를 적어 둔다:

```sql
-- 앱 DB(ftjxwxuactfjopbokbni) 에서
select id, user_id, provider, provider_id, created_at
from auth.identities
where provider = 'kakao'
order by created_at desc
limit 10;

select count(*) from auth.identities where provider = 'kakao';
```

2. 그 계정으로 카카오 로그인 (새 경로).
3. 같은 쿼리를 다시 돌린다.

| 결과 | 판정 |
|---|---|
| `count` 가 **그대로**이고 그 계정의 `user_id` 도 그대로 | ✅ 통과 — 계속 진행 |
| `count` 가 **1 늘고** 같은 사람에게 새 `user_id` 가 생김 | ❌ **즉시 플래그 OFF**. 기존 유저가 새 계정으로 떨어진다 |

실패했다면 `provider_id` 두 행의 값을 비교해 보라 — 형식이 다르면(예: 한쪽이 숫자 문자열, 다른 쪽이 다른 표현) identity 를 맞춰 붙이는 마이그레이션이 따로 필요하고, 그건 이 런북의 범위 밖이다. **그 판단이 끝나기 전에는 플래그를 다시 켜지 마라.**

### 4.3 나머지 QA

프로덕션 3호스트는 `.wearless.kr` 쿠키를 공유하므로 한 곳에서 로그인하면 나머지 둘도 로그인 상태가 된다. **호스트별로 진짜 판정하려면 쿠키를 지우고 각각 독립적으로** 테스트해야 한다.

| 시나리오 | 기대 |
|---|---|
| ai.wearless.kr 에서 카카오 로그인 | `/` 로 복귀, 세션 생성 |
| facemarket.wearless.kr 랜딩 CTA → 카카오 로그인 | 로그인 후 `/model/register` (복귀 목표 `wl_postLogin` 소비) |
| admin.wearless.kr 에서 카카오 로그인 | 대시보드. 기기 게이트는 종전대로 |
| 카카오 동의 화면에서 **취소** | "카카오 로그인을 취소했어요" + '로그인 다시 시도' 버튼 |
| 셀러 **회원가입 탭**에서 동의 체크 → 카카오 → 취소 | 가입 동의 표시가 남지 않아야 한다(다음 로그인에 새면 안 된다) |
| 콜백 화면에서 새로고침 | 같은 실패 화면. 인가코드는 일회용이라 정상 |
| 구글 로그인 | **한 줄도 안 바뀌어야 한다** |

### 4.4 로컬 QA

- 로컬 콜백은 항상 **seller 문서**로 열린다(`vite.config.js` 의 dev 문서 디스패처가 쿼리·호스트로만 문서를 고르는데, redirect_uri 는 정확 일치라 `?facemarket=1` 을 붙여 등록할 수 없다). seller 앱의 `IS_FACEMARKET` 가드가 쿼리를 보존한 채 재로드해 주긴 하지만, 확실한 facemarket QA 는 **cloudflared 터널(`https://facemarket.wearless.kr`)** 로 한다(`scripts/start.sh`).
- **admin 로컬 카카오 QA 는 사실상 불가능하다** — `host.js` 의 `detectAdmin` 에는 facemarket 같은 sessionStorage 기억이 없어서, 콜백 URL 에 `?admin=1` 이 없으면 seller 앱이 뜬다. admin 은 구글·이메일 로그인으로 QA 하고 카카오는 prod 에서 확인한다.
- 폰 QA 용 LAN IP(`http://192.168.x.x`)에서는 `crypto.subtle` 이 없어 PKCE 가 생략된다(코드가 알아서 처리한다 — 조용히 죽지 않는다). 다만 그 주소는 카카오에 등록돼 있지 않으니 로그인 자체가 안 된다.

---

## 5. 장애 대응 — 증상별

| 증상 | 원인 후보 | 확인 |
|---|---|---|
| 다시 **KOE205** | scope 에 `account_email` 이 돌아왔다 | 브라우저 주소창의 authorize URL `scope=` 를 본다. `tests/frontend/kakao-oidc-login.test.mjs` 가 CI 에서 잡는다 |
| **KOE006** (등록되지 않은 Redirect URI) | 콘솔에 그 호스트 줄이 없다 | §2.2 의 4줄. 오타·끝 슬래시까지 정확 일치 |
| 서버 **503 `kakao_login_not_configured`** | `KAKAO_REST_API_KEY` 미주입 | 매니페스트 `variables:` 확인. 미선언은 `test_deploy_manifest_qc_flags.py` 가 잡는다 |
| 서버 **400 `invalid_redirect_uri`** | 화이트리스트 밖 주소 | `server/app/kakao_oidc.py` 의 `DEFAULT_REDIRECT_URIS` + env `KAKAO_REDIRECT_URIS` |
| 서버 **400 `kakao_token_exchange_failed`** | 만료·재사용된 인가코드(뒤로가기·새로고침) | 정상 동작이다. 처음부터 다시 로그인 |
| 화면 **"로그인 요청이 만료됐어요"** | state 불일치 — 다른 탭에서 시작했거나 sessionStorage 가 비워졌다 | 사파리 프라이빗·쿠키 차단이면 로그인 시작 단계에서 먼저 막힌다 |
| 화면 **"카카오 계정으로 로그인하지 못했어요"** | `signInWithIdToken` 거절 — **대시보드 provider 가 꺼졌거나 client_id 가 갈렸다** | §1 의 세 값 대조. 브라우저 콘솔에 GoTrue 메시지가 남는다 |
| 콘솔에 **`Nonces mismatch`** | 인가 요청에 원본 nonce 를 보냈다 | GoTrue 는 우리가 준 nonce 를 **sha256** 해서 id_token 클레임과 맞춘다(`token_oidc.go` 301-305) ⇒ 카카오엔 해시, Supabase 엔 원본. `lib/kakaoOidc.js` 의 `sha256Hex`, 회귀 가드는 `kakao-oidc-login.test.mjs` §⑦ |
| 콘솔에 **`Passed nonce and nonce in id_token should either both exist or not.`** | 한쪽에만 nonce 가 있다 | 해시를 못 만드는 컨텍스트(비보안 오리진)에서는 **양쪽 다** 생략해야 한다 |
| 카카오 로그인 뒤 **엉뚱한 화면**(facemarket 에서 랜딩) | `host.js` 의 `FACEMARKET_ROUTES` 에서 `/auth` 가 빠졌다 | 빠지면 콜백이 라우터에 닿기 전에 `/model/register` 로 튕겨 인가코드가 사라진다 |

로그에서는 **code·id_token·access_token·client_secret 을 절대 찾을 수 없다**(그렇게 만들었다 — `test_secrets_never_reach_the_logs`). 남는 것은 status 와 카카오가 준 error code 뿐이다. 그 둘로 위 표를 짚어라.
