# FaceMarket Mandatory VC Cutover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** FaceMarket 실물 모델 라이선스를 OpenDID FaceLicense VC와 강제 결속하고, 인증된 Holder 통신·durable revoke·재시작 검증을 통과한 뒤 Server 1을 Server 3으로 안전하게 cutover한다.

**Architecture:** 이미 구현된 OpenDID V2.0.0 Holder, 단일 Server 3 Compose/systemd, export/restore/bootstrap 도구와 lifecycle smoke를 그대로 재사용한다. 애플리케이션에는 `pending -> active` VC 상태 전이, HMAC 서명 Holder client, durable revoke reconciler, API/worker 공통 fail-closed 검증만 추가한다. 운영 cutover 전까지 실제 서버·비밀·방화벽·운영 데이터는 건드리지 않는다.

**Tech Stack:** Python 3.12, FastAPI, httpx, psycopg 3, PostgreSQL/Supabase migrations, Java 21, Spring Boot 3.2.4, JUnit 5, Bash, Docker Compose, systemd, OpenDID V2.0.0, Besu 25.5.0.

**Spec:** `docs/superpowers/specs/2026-08-21-facemarket-biometric-runtime-hardening-design.md`

## Global Constraints

- Server 3은 PostgreSQL, Besu, TAS `8090`, Issuer `8091`, CAS `8094`, fm-holder `8100`만 운영한다.
- Orchestrator `9001`은 bootstrap/recovery에만 사용하고 자동 기동하지 않는다.
- Holder 요청은 공유 HMAC secret, timestamp, nonce를 사용하며 body 변조와 재전송을 모두 거절한다.
- 새 라이선스는 `pending`으로 생성하고 FaceLicense VC 발급 성공 뒤에만 `active`가 된다.
- Holder 누락·timeout·장애·invalid·revoked는 실물 모델 사용을 fail closed한다. 장애는 503, 자격 불충족은 409다.
- revoke 요청은 local license를 먼저 non-active로 만들고 durable queue로 retry/reconcile한다.
- Server 1은 Server 3 private `8100`에만 접근한다. `5432/8545/8090/8091/8094/9001`에는 접근하지 않는다.
- production에서 FaceMarket이 켜졌으면 mandatory VC flag, Holder URL, HMAC secret 중 하나라도 빠질 때 startup을 실패시킨다.
- 실제 원화/토큰 송금과 모델 지급은 이 계획의 범위가 아니다.
- 새 dependency는 추가하지 않는다. Python/Java 표준 라이브러리와 이미 설치된 httpx/Spring Web만 사용한다.
- 기존 라이선스 값은 migration에서 자동 변경하지 않는다. 기존 모델 freeze/reverification과 license status check 확장은 선행 biometric runtime migration이 소유한다.
- 이 계획은 biometric enrollment 계획 다음에 실행한다. `create_license`와 `_issue_face_vc`를 수정할 때 승인된 `enrollment_id` 검증, current asset evidence 결속, model/enrollment atomic activation을 제거하지 않으며 biometric 회귀 테스트를 함께 통과시킨다.

## Reuse Baseline — Do Not Reimplement

다음 자산은 현재 브랜치에서 구현·검증됐으므로 수정 이유가 생기지 않는 한 그대로 사용한다.

- `services/fm-holder/build.gradle`과 `services/fm-holder/src/main/java/org/omnione/did/base/**`: OpenDID V2.0.0 DTO 및 Java 21 clean build.
- `deploy/opendid/infra.compose.yml`, `deploy/opendid/config/*.yml`, `deploy/opendid/systemd/*.service`: Server 3 배포 골격.
- `deploy/opendid/export-state.sh`, `restore-state.sh`, `inventory-state.sh`, `verify-vcmeta.py`: synchronized state migration과 read-only chain 검증.
- `scripts/opendid-provision.sh`, `scripts/issuer-provision-facelicense.sh`: fresh bootstrap과 FaceLicense plan 검증.
- `deploy/opendid/smoke.sh`: issue/revoke/restart lifecycle 골격. 이 계획에서는 HMAC과 Holder verify arm만 확장한다.
- `docs/runbooks/facemarket-opendid-single-server.md`: source freeze, restore, chain/DB 비교 순서.

---

### Task 1: Mandatory VC Schema and Durable Revoke Queue

**Files:**

- Create: `supabase/migrations/20260821010000_facemarket_mandatory_vc.sql`
- Create: `server/tests/test_facemarket_mandatory_vc_migration.py`

**Interfaces:**

- Consumes: `supabase/migrations/20260821000000_facemarket_biometric_runtime.sql`, which already expands the `fm_licenses_status_check` constraint to accept `pending` and `reverification_required`.
- Consumes: existing `public.fm_licenses(id, model_id, status, vc_id)` rows and UUID support from PostgreSQL.
- Produces: `fm_licenses.status` default `pending`; it does not replace or narrow the predecessor's status check.
- Produces: `public.fm_vc_revocation_jobs` with one durable row per `vc_id` and claim/retry fields used by Task 5.

- [ ] **Step 1: Write static migration contract tests**

Create `server/tests/test_facemarket_mandatory_vc_migration.py`:

```python
from pathlib import Path

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "supabase/migrations/20260821010000_facemarket_mandatory_vc.sql"
)
PREDECESSOR = (
    Path(__file__).resolve().parents[2]
    / "supabase/migrations/20260821000000_facemarket_biometric_runtime.sql"
)


def _sql() -> str:
    return " ".join(MIGRATION.read_text(encoding="utf-8").split()).lower()


def test_predecessor_owns_the_expanded_license_status_check():
    sql = " ".join(PREDECESSOR.read_text(encoding="utf-8").split()).lower()
    assert "fm_licenses_status_check" in sql
    assert "'pending'" in sql and "'reverification_required'" in sql


def test_license_status_defaults_pending_without_rewriting_existing_rows():
    sql = _sql()
    assert "alter column status set default 'pending'" in sql
    assert "fm_licenses_status_check" not in sql
    assert "update public.fm_licenses" not in sql


def test_revocation_queue_is_durable_idempotent_and_service_private():
    sql = _sql()
    assert "create table if not exists public.fm_vc_revocation_jobs" in sql
    assert "vc_id text not null unique" in sql
    assert "status in ('pending', 'processing', 'retry', 'revoked')" in sql
    assert "next_attempt_at" in sql and "lease_expires_at" in sql
    assert "enable row level security" in sql
```

- [ ] **Step 2: Run the tests and verify the missing migration fails**

Run:

```bash
cd server
.venv/bin/pytest -q tests/test_facemarket_mandatory_vc_migration.py
```

Expected: FAIL because `20260821010000_facemarket_mandatory_vc.sql` does not exist.

- [ ] **Step 3: Add the forward-only migration**

Create `supabase/migrations/20260821010000_facemarket_mandatory_vc.sql` with this schema. Do not drop or recreate `fm_licenses_status_check`; migration `20260821000000_facemarket_biometric_runtime.sql` owns it.

```sql
alter table public.fm_licenses
  alter column status set default 'pending';

create table if not exists public.fm_vc_revocation_jobs (
  id                uuid primary key default gen_random_uuid(),
  license_id        uuid not null references public.fm_licenses(id) on delete restrict,
  model_id          uuid not null references public.fm_models(id) on delete restrict,
  vc_id             text not null unique,
  status            text not null default 'pending'
                      check (status in ('pending', 'processing', 'retry', 'revoked')),
  attempts          integer not null default 0 check (attempts >= 0),
  next_attempt_at   timestamptz not null default now(),
  lease_token       uuid,
  lease_expires_at  timestamptz,
  last_error_code   text,
  revoked_at        timestamptz,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);

create index if not exists fm_vc_revocation_jobs_claim_idx
  on public.fm_vc_revocation_jobs (next_attempt_at, created_at)
  where status in ('pending', 'retry');

alter table public.fm_vc_revocation_jobs enable row level security;

drop trigger if exists fm_vc_revocation_jobs_set_updated_at
  on public.fm_vc_revocation_jobs;
create trigger fm_vc_revocation_jobs_set_updated_at
  before update on public.fm_vc_revocation_jobs
  for each row execute function public.set_updated_at();
```

- [ ] **Step 4: Run the migration contract tests**

Run:

```bash
cd server
.venv/bin/pytest -q tests/test_facemarket_mandatory_vc_migration.py
```

Expected: 3 passed.

- [ ] **Step 5: Commit the schema decision**

```bash
git add supabase/migrations/20260821010000_facemarket_mandatory_vc.sql \
  server/tests/test_facemarket_mandatory_vc_migration.py
git commit -m "Make VC state authoritative before model use

Constraint: Existing license rows must keep their current status during migration.
Rejected: Reusing active with a null vc_id | It preserves the current fail-open state.
Confidence: high
Scope-risk: narrow
Directive: Never activate a license without storing a non-empty FaceLicense vc_id in the same transition.
Tested: Static migration contract tests.
Not-tested: Live Supabase migration execution requires FACEMARKET_TEST_DATABASE_URL."
```

---

### Task 2: Replay-Resistant HMAC Authentication in fm-holder

**Files:**

- Create: `services/fm-holder/src/main/java/kr/wearless/fmholder/security/HolderHmacFilter.java`
- Create: `services/fm-holder/src/test/java/kr/wearless/fmholder/security/HolderHmacFilterTest.java`
- Modify: `deploy/opendid/config/holder.yml`
- Modify: `deploy/opendid/env.example`

**Interfaces:**

- Consumes headers `X-FM-Timestamp`, `X-FM-Nonce`, `X-FM-Signature`.
- Canonical bytes: `v1\n<METHOD>\n<PATH_AND_QUERY>\n<TIMESTAMP>\n<NONCE>\n<SHA256_BODY_HEX>` encoded as UTF-8.
- Signature: lower-case hex `HMAC-SHA256(FM_HOLDER_HMAC_SECRET, canonical_bytes)`.
- Timestamp: Unix epoch seconds, maximum absolute skew 60 seconds.
- Nonce: URL-safe `[A-Za-z0-9_-]{22,128}`; its SHA-256 digest is atomically created below `${holder.data-dir}/auth-nonces` so replay remains blocked across Holder restart.
- Exempt endpoint: `GET /holder/health` only.
- Produces: authenticated request with its original body still readable by Spring MVC, or uniform HTTP 401 without controller execution.

- [ ] **Step 1: Write filter tests first**

Create `HolderHmacFilterTest.java` using `MockHttpServletRequest`, `MockHttpServletResponse`, `MockFilterChain`, `@TempDir`, and a fixed `Clock`. Include these tests:

```java
@Test
void validSignaturePassesAndPreservesBody(@TempDir Path dir) throws Exception {
    Clock clock = Clock.fixed(Instant.ofEpochSecond(1_800_000_000L), ZoneOffset.UTC);
    HolderHmacFilter filter = new HolderHmacFilter("shared-secret", dir, clock);
    byte[] body = "{\"vcId\":\"vc-1\"}".getBytes(StandardCharsets.UTF_8);
    MockHttpServletRequest request = signedRequest(
            "shared-secret", clock, "POST", "/holder/vc/verify", body, "nonce_value_123456789012");
    MockHttpServletResponse response = new MockHttpServletResponse();
    AtomicReference<String> observed = new AtomicReference<>();

    filter.doFilter(request, response, (req, res) ->
            observed.set(new String(req.getInputStream().readAllBytes(), StandardCharsets.UTF_8)));

    assertEquals(200, response.getStatus());
    assertEquals(new String(body, StandardCharsets.UTF_8), observed.get());
}

@Test
void duplicateNonceIsRejectedAfterFilterRecreation(@TempDir Path dir) throws Exception {
    Clock clock = Clock.fixed(Instant.ofEpochSecond(1_800_000_000L), ZoneOffset.UTC);
    byte[] body = "{}".getBytes(StandardCharsets.UTF_8);
    MockHttpServletRequest first = signedRequest(
            "shared-secret", clock, "POST", "/holder/models/m-1/wallet", body,
            "nonce_value_123456789012");
    new HolderHmacFilter("shared-secret", dir, clock)
            .doFilter(first, new MockHttpServletResponse(), new MockFilterChain());

    MockHttpServletResponse replay = new MockHttpServletResponse();
    MockHttpServletRequest second = signedRequest(
            "shared-secret", clock, "POST", "/holder/models/m-1/wallet", body,
            "nonce_value_123456789012");
    new HolderHmacFilter("shared-secret", dir, clock)
            .doFilter(second, replay, new MockFilterChain());

    assertEquals(401, replay.getStatus());
}
```

Also add tests for missing signature, stale timestamp, body tampering, malformed nonce, and health bypass.

- [ ] **Step 2: Run the focused Java test and verify it fails**

Run:

```bash
cd services/fm-holder
./gradlew test --tests '*HolderHmacFilterTest'
```

Expected: FAIL because `HolderHmacFilter` does not exist.

- [ ] **Step 3: Implement the filter with standard library crypto and atomic nonce files**

Create `HolderHmacFilter.java` with these exact public/package interfaces:

```java
@Component
public final class HolderHmacFilter extends OncePerRequestFilter {
    static final long MAX_SKEW_SECONDS = 60;
    private final byte[] secret;
    private final Path nonceDir;
    private final Clock clock;

    public HolderHmacFilter(
            @Value("${holder.api-hmac-secret}") String secret,
            @Value("${holder.data-dir}") String dataDir) {
        this(secret, Path.of(dataDir), Clock.systemUTC());
    }

    HolderHmacFilter(String secret, Path dataDir, Clock clock) {
        if (secret == null || secret.isBlank()) {
            throw new IllegalArgumentException("holder.api-hmac-secret is required");
        }
        this.secret = secret.getBytes(StandardCharsets.UTF_8);
        this.nonceDir = dataDir.resolve("auth-nonces");
        this.clock = Objects.requireNonNull(clock);
        try {
            Files.createDirectories(nonceDir);
        } catch (IOException error) {
            throw new IllegalStateException("cannot initialize Holder nonce directory", error);
        }
    }

    static String signature(
            String secret, String method, String target,
            String timestamp, String nonce, byte[] body) {
        String digest = HexFormat.of().formatHex(sha256(body));
        String canonical = String.join(
                "\n", "v1", method.toUpperCase(Locale.ROOT), target,
                timestamp, nonce, digest);
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
            return HexFormat.of().formatHex(
                    mac.doFinal(canonical.getBytes(StandardCharsets.UTF_8)));
        } catch (GeneralSecurityException error) {
            throw new IllegalStateException("HmacSHA256 is unavailable", error);
        }
    }

    private static byte[] sha256(byte[] value) {
        try {
            return MessageDigest.getInstance("SHA-256").digest(value);
        } catch (GeneralSecurityException error) {
            throw new IllegalStateException("SHA-256 is unavailable", error);
        }
    }
}
```

Implementation rules:

1. Cache the request bytes in a small `HttpServletRequestWrapper` so the controller receives the same bytes verified by the filter.
2. Build `target` from `request.getRequestURI()` and the raw query string when present.
3. Parse timestamp with `Long.parseLong`; reject overflow, malformed values, or skew over 60 seconds.
4. Validate nonce length/characters before hashing it.
5. Verify the signature with `MessageDigest.isEqual` before consuming the nonce.
6. Persist `sha256(nonce).hex` with `Files.writeString(path, timestamp, CREATE_NEW, WRITE)`; `FileAlreadyExistsException` means replay and returns 401.
7. Delete nonce files older than 120 seconds at most once per minute. Keep cleanup in the same class; no cache dependency or repository layer.
8. Return only `{"error":"unauthorized"}` and never log signature, nonce, body, or secret.

- [ ] **Step 4: Require the shared secret in Holder deployment config**

Add to `deploy/opendid/config/holder.yml`:

```yaml
holder:
  data-dir: ${FM_HOLDER_DATA_DIR}
  wallet-pepper: ${FM_HOLDER_PEPPER}
  api-hmac-secret: ${FM_HOLDER_HMAC_SECRET}
```

Add to `deploy/opendid/env.example` immediately after `FM_HOLDER_PEPPER=`:

```dotenv
FM_HOLDER_HMAC_SECRET=
```

The unresolved Spring placeholder must fail Holder startup; do not add a development default.

- [ ] **Step 5: Run focused and full Holder tests**

Run:

```bash
cd services/fm-holder
./gradlew test --tests '*HolderHmacFilterTest'
./gradlew clean test
```

Expected: all tests pass and the Holder test count increases by at least 6.

- [ ] **Step 6: Commit the Holder authentication boundary**

```bash
git add services/fm-holder/src/main/java/kr/wearless/fmholder/security/HolderHmacFilter.java \
  services/fm-holder/src/test/java/kr/wearless/fmholder/security/HolderHmacFilterTest.java \
  deploy/opendid/config/holder.yml deploy/opendid/env.example
git commit -m "Authenticate every Holder operation across restarts

Constraint: Server 1 and Server 3 communicate over private HTTP without adding a new dependency.
Rejected: Network ACL as the only mutation guard | Captured requests would remain replayable.
Confidence: high
Scope-risk: moderate
Directive: Keep canonical request bytes identical in Python and Java; any format change is a coordinated protocol version change.
Tested: Focused HMAC filter tests and Holder clean test.
Not-tested: Cross-host clock skew is covered by the deployment preflight."
```

---

### Task 3: Signed Python Holder Client and Production Startup Invariants

**Files:**

- Create: `server/app/holder_client.py`
- Create: `server/tests/test_holder_client.py`
- Create: `server/tests/test_facemarket_vc_config.py`
- Modify: `server/app/config.py`
- Modify: `server/app/main.py`
- Modify: `server/.env.example`

**Interfaces:**

- Produces: `holder_client.canonical_request(method, target, timestamp, nonce, body) -> bytes`.
- Produces: `holder_client.signature(secret, method, target, timestamp, nonce, body) -> str`.
- Produces: `await holder_client.post(client, *, base_url, secret, path, payload) -> httpx.Response`.
- Produces settings: `fm_vc_required: bool`, `opendid_holder_hmac_secret: str | None`.
- Startup invariant: production + FaceMarket requires `FACEMARKET_VC_REQUIRED=true`; required mode in any environment requires Holder URL and HMAC secret.

- [ ] **Step 1: Write deterministic signing tests**

Create `server/tests/test_holder_client.py`:

```python
import asyncio
import hashlib
import hmac
import json

import httpx

from app import holder_client


def test_canonical_signature_binds_method_path_timestamp_nonce_and_body():
    body = b'{"vcId":"vc-1"}'
    canonical = b"v1\nPOST\n/holder/vc/verify\n1800000000\nnonce_1234567890123456789012\n" + hashlib.sha256(body).hexdigest().encode()
    expected = hmac.new(b"shared-secret", canonical, hashlib.sha256).hexdigest()
    assert holder_client.signature(
        "shared-secret", "POST", "/holder/vc/verify", "1800000000",
        "nonce_1234567890123456789012", body,
    ) == expected


def test_post_signs_the_exact_json_bytes_sent(monkeypatch):
    observed = {}

    async def handler(request: httpx.Request):
        observed["body"] = request.content
        observed["headers"] = dict(request.headers)
        return httpx.Response(200, json={"status": "valid"})

    async def scenario():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await holder_client.post(
                client,
                base_url="http://holder",
                secret="shared-secret",
                path="/holder/vc/verify",
                payload={"vcId": "vc-1"},
                timestamp="1800000000",
                nonce="nonce_1234567890123456789012",
            )

    response = asyncio.run(scenario())
    assert response.status_code == 200
    assert observed["body"] == json.dumps(
        {"vcId": "vc-1"}, sort_keys=True, separators=(",", ":")
    ).encode()
    assert observed["headers"]["x-fm-timestamp"] == "1800000000"
    assert observed["headers"]["x-fm-nonce"] == "nonce_1234567890123456789012"
    assert observed["headers"]["x-fm-signature"]
```

- [ ] **Step 2: Write production configuration failure tests**

Create `server/tests/test_facemarket_vc_config.py`:

```python
import pytest

from app.main import create_app
from conftest import make_settings


def test_production_facemarket_requires_mandatory_vc():
    with pytest.raises(RuntimeError, match="FACEMARKET_VC_REQUIRED"):
        create_app(make_settings(
            app_env="production", facemarket_enabled=True, fm_vc_required=False
        ))


@pytest.mark.parametrize("missing", ["url", "secret"])
def test_required_vc_rejects_missing_holder_config(missing):
    values = {
        "opendid_holder_url": "http://holder:8100",
        "opendid_holder_hmac_secret": "shared-secret",
    }
    values["opendid_holder_url" if missing == "url" else "opendid_holder_hmac_secret"] = None
    with pytest.raises(RuntimeError, match="OpenDID Holder"):
        create_app(make_settings(fm_vc_required=True, **values))


def test_dev_can_keep_mandatory_vc_disabled():
    create_app(make_settings(app_env="dev", facemarket_enabled=True, fm_vc_required=False))
```

- [ ] **Step 3: Run both new test files and verify failure**

Run:

```bash
cd server
.venv/bin/pytest -q tests/test_holder_client.py tests/test_facemarket_vc_config.py
```

Expected: collection or assertion failure because the module and settings do not exist.

- [ ] **Step 4: Implement the signed client**

Create `server/app/holder_client.py` using only `hashlib`, `hmac`, `json`, `secrets`, `time`, and installed `httpx`:

```python
def canonical_request(
    method: str, target: str, timestamp: str, nonce: str, body: bytes
) -> bytes:
    digest = hashlib.sha256(body).hexdigest()
    return f"v1\n{method.upper()}\n{target}\n{timestamp}\n{nonce}\n{digest}".encode()


def signature(
    secret: str, method: str, target: str,
    timestamp: str, nonce: str, body: bytes,
) -> str:
    return hmac.new(
        secret.encode(), canonical_request(method, target, timestamp, nonce, body), hashlib.sha256
    ).hexdigest()


async def post(
    client: httpx.AsyncClient, *, base_url: str, secret: str,
    path: str, payload: dict, timestamp: str | None = None,
    nonce: str | None = None,
) -> httpx.Response:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    timestamp = timestamp or str(int(time.time()))
    nonce = nonce or secrets.token_urlsafe(24)
    headers = {
        "Content-Type": "application/json",
        "X-FM-Timestamp": timestamp,
        "X-FM-Nonce": nonce,
        "X-FM-Signature": signature(secret, "POST", path, timestamp, nonce, body),
    }
    return await client.post(f"{base_url.rstrip('/')}{path}", content=body, headers=headers)
```

- [ ] **Step 5: Add settings and fail-fast validation**

In `server/app/config.py` add:

```python
fm_vc_required: bool = False
opendid_holder_hmac_secret: str | None = None
```

Load them with:

```python
fm_vc_required=(os.getenv("FACEMARKET_VC_REQUIRED", "false").lower() == "true"),
opendid_holder_hmac_secret=os.getenv("OPENDID_HOLDER_HMAC_SECRET") or None,
```

In `server/app/main.py`, call this before creating the DB pool:

```python
def _validate_facemarket_vc_settings(settings: Settings) -> None:
    if settings.app_env == "production" and settings.facemarket_enabled and not settings.fm_vc_required:
        raise RuntimeError(
            "FACEMARKET_VC_REQUIRED=true is required for production FaceMarket"
        )
    if settings.fm_vc_required and (
        not settings.opendid_holder_url or not settings.opendid_holder_hmac_secret
    ):
        raise RuntimeError(
            "OpenDID Holder URL and HMAC secret are required when FaceMarket VC is mandatory"
        )
```

Document these exact names in `server/.env.example`:

```dotenv
FACEMARKET_VC_REQUIRED=false
OPENDID_HOLDER_URL=
OPENDID_HOLDER_HMAC_SECRET=
```

- [ ] **Step 6: Run signing, config, and existing app-startup tests**

Run:

```bash
cd server
.venv/bin/pytest -q \
  tests/test_holder_client.py \
  tests/test_facemarket_vc_config.py \
  tests/test_facemarket_licenses.py \
  tests/test_payments_toss.py
```

Expected: all tests pass.

- [ ] **Step 7: Commit the shared client protocol and startup gate**

```bash
git add server/app/holder_client.py server/app/config.py server/app/main.py \
  server/.env.example server/tests/test_holder_client.py \
  server/tests/test_facemarket_vc_config.py
git commit -m "Refuse FaceMarket production without an authenticated Holder

Constraint: Production must fail before serving traffic when mandatory VC inputs are incomplete.
Rejected: Logging a warning for missing Holder configuration | It recreates the current silent fail-open path.
Confidence: high
Scope-risk: moderate
Directive: Store OPENDID_HOLDER_HMAC_SECRET only in runtime secret stores, never variables or logs.
Tested: Deterministic signing, exact-body transport, startup invariant, and existing app startup tests.
Not-tested: Cross-language signature compatibility is completed by the deployment smoke task."
```

---

### Task 4: Synchronous `pending -> active` Issuance and Fail-Closed Verification

**Files:**

- Modify: `server/app/facemarket.py`
- Modify: `server/scripts/retry_pending_face_vcs.py`
- Modify: `server/tests/test_facemarket_licenses.py`
- Modify: `server/tests/test_facemarket_seller_loop.py`
- Create: `server/tests/test_facemarket_mandatory_vc.py`

**Interfaces:**

- Consumes: Task 1 `pending` status and Task 3 `holder_client.post`.
- Produces: `_issue_face_vc(...) -> dict`, returning the activated `_LICENSE_CARD_COLS` row or raising without activating.
- Produces: required-mode `verify_license(app, license_row) -> None`; missing/invalid/revoked raises 409, transport/non-200/malformed response raises 503.
- Preserves: non-required dev mode behind `fm_vc_required=False`; production cannot enter that mode because Task 3 rejects startup.

- [ ] **Step 1: Write the focused issuance fake and transition tests**

In `server/tests/test_facemarket_mandatory_vc.py`, import `asyncio`, `contextlib`, `types`, `datetime`, `timedelta`, `timezone`, `pytest`, and `from app import facemarket, holder_client`. Define this focused fake; every helper referenced by the tests lives in this file:

```python
FUTURE = datetime.now(timezone.utc) + timedelta(days=30)


class _Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _IssueCursor:
    def __init__(self, store):
        self.store = store
        self._one = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def execute(self, sql, params=()):
        normalized = " ".join(sql.split()).lower()
        if normalized.startswith("select display_name from fm_models"):
            self._one = {"display_name": "김*늘"}
        elif normalized.startswith("update fm_licenses"):
            vc_id, license_id = params
            row = self.store["licenses"][license_id]
            if row["status"] == "pending" and row["vc_id"] is None:
                row.update(vc_id=vc_id, status="active")
                self._one = dict(row)
            else:
                self._one = None
        elif normalized.startswith("update fm_models"):
            self._one = None
        elif normalized.startswith("insert into fm_vc_revocation_jobs"):
            license_id, model_id, vc_id = params
            self.store["revocations"].setdefault(
                vc_id, {"license_id": license_id, "model_id": model_id, "status": "pending"}
            )
            self._one = None
        else:
            raise AssertionError(f"unexpected SQL: {normalized}")

    async def fetchone(self):
        return self._one


class _IssueConn:
    def __init__(self, store):
        self.store = store

    def cursor(self):
        return _IssueCursor(self.store)

    async def commit(self):
        self.store["commits"] += 1


class _IssuePool:
    def __init__(self, store):
        self.store = store

    def connection(self):
        @contextlib.asynccontextmanager
        async def connection():
            yield _IssueConn(self.store)
        return connection()


def mandatory_app(monkeypatch, *, issue_response=(200, {"vcId": "vc-1"})):
    store = {
        "licenses": {"lic-1": {"id": "lic-1", "status": "pending", "vc_id": None}},
        "revocations": {},
        "commits": 0,
    }
    responses = iter([
        _Response(201, {"modelId": "m-1"}),
        _Response(200, {"flowAComplete": True, "userDid": "did:omn:m-1"}),
        _Response(*issue_response),
    ])

    async def fake_post(_client, **_kwargs):
        return next(responses)

    monkeypatch.setattr(holder_client, "post", fake_post)
    settings = types.SimpleNamespace(
        opendid_holder_url="http://holder:8100",
        opendid_holder_hmac_secret="shared-secret",
    )
    return types.SimpleNamespace(
        state=types.SimpleNamespace(settings=settings, pool=_IssuePool(store))
    ), store
```

Add these tests below the fake:

```python
def test_issue_activates_only_after_nonempty_vc_id(monkeypatch):
    app, store = mandatory_app(
        monkeypatch,
        issue_response=(200, {"vcId": "vc-1", "userDid": "did:omn:m-1"}),
    )
    row = asyncio.run(facemarket._issue_face_vc(
        app, license_id="lic-1", model_id="m-1", allowed=["일반 여성 의류"],
        forbidden=[], unit_price=1000, valid_until=FUTURE, digest="sha256-x",
    ))
    assert row["status"] == "active" and row["vc_id"] == "vc-1"
    assert store["licenses"]["lic-1"]["status"] == "active"


@pytest.mark.parametrize("status,payload", [
    (500, {}),
    (200, {}),
    (200, {"vcId": ""}),
])
def test_issue_failure_leaves_license_pending(monkeypatch, status, payload):
    app, store = mandatory_app(monkeypatch, issue_response=(status, payload))
    with pytest.raises(Exception):
        asyncio.run(facemarket._issue_face_vc(
            app, license_id="lic-1", model_id="m-1", allowed=[], forbidden=[],
            unit_price=1000, valid_until=FUTURE, digest="sha256-x",
        ))
    assert store["licenses"]["lic-1"]["status"] == "pending"
    assert store["licenses"]["lic-1"]["vc_id"] is None
```

- [ ] **Step 2: Add the route-level pending-on-outage test**

In `server/tests/test_facemarket_licenses.py`, make the current `fm` fixture accept `request` and create settings with `fm_vc_required=bool(getattr(request, "param", False))`, Holder URL `http://holder:8100`, and secret `shared-secret` only in required mode. In `FakeCursor`'s insert arm, set `status` to `pending` when the SQL contains an explicit `status` value. Add:

```python
@pytest.mark.parametrize("fm", [True], indirect=True)
def test_required_create_keeps_committed_pending_row_when_holder_is_down(
    fm, make_token, monkeypatch
):
    client, store, _r2 = fm

    async def fail_issue(*_args, **_kwargs):
        raise RuntimeError("holder unavailable")

    monkeypatch.setattr(facemarket, "_issue_face_vc", fail_issue)
    response = client.post(
        "/v1/facemarket/licenses",
        files={"face": _png()},
        data={"unit_price": "1000", "valid_days": "30"},
        headers=_auth(make_token),
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "vc_issue_delayed"
    assert store["licenses"][0]["status"] == "pending"
    assert store["licenses"][0]["vc_id"] is None
```

- [ ] **Step 3: Replace fail-open verification expectations**

In `server/tests/test_facemarket_seller_loop.py` replace these current expectations:

- `test_verify_active_valid_passes_without_holder`
- `test_verify_holder_unreachable_skips_arm`
- `test_verify_holder_set_but_no_vc_skips_arm`

First replace `_app` and `_patch_holder` so the tests use Task 3's signed-client seam:

```python
def _app(opendid_holder_url=None, *, required=False, secret="shared-secret"):
    return types.SimpleNamespace(
        state=types.SimpleNamespace(settings=types.SimpleNamespace(
            fm_vc_required=required,
            opendid_holder_url=opendid_holder_url,
            opendid_holder_hmac_secret=secret,
        ))
    )


def _patch_holder(monkeypatch, resp=None, boom=False):
    async def fake_post(*_args, **_kwargs):
        if boom:
            raise httpx.ConnectError("holder down")
        return resp

    monkeypatch.setattr(facemarket.holder_client, "post", fake_post)
```

Import `httpx`, then add the required-mode assertions:

```python
def test_required_verify_without_vc_is_409():
    row = {"status": "active", "license_valid_until": FUTURE, "vc_id": None}
    with pytest.raises(facemarket.HTTPException) as error:
        asyncio.run(facemarket.verify_license(_app(required=True), row))
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "license_unverified"


def test_required_verify_holder_unreachable_is_503(monkeypatch):
    _patch_holder(monkeypatch, boom=True)
    row = {"status": "active", "license_valid_until": FUTURE, "vc_id": "vc-1"}
    with pytest.raises(facemarket.HTTPException) as error:
        asyncio.run(facemarket.verify_license(
            _app("http://holder:8100", required=True), row
        ))
    assert error.value.status_code == 503
    assert error.value.detail["code"] == "holder_unavailable"


def test_optional_dev_mode_preserves_local_only_behavior():
    row = {"status": "active", "license_valid_until": FUTURE, "vc_id": None}
    assert asyncio.run(facemarket.verify_license(_app(required=False), row)) is None
```

- [ ] **Step 4: Run the focused tests and verify failure**

Run:

```bash
cd server
.venv/bin/pytest -q \
  tests/test_facemarket_mandatory_vc.py \
  tests/test_facemarket_seller_loop.py \
  tests/test_facemarket_licenses.py
```

Expected: FAIL on background issuance and Holder skip behavior.

- [ ] **Step 5: Make license creation synchronous in required mode**

In `create_license`:

1. Insert `status='pending'` explicitly.
2. Commit the pending row before calling Holder so an outage leaves a retryable record.
3. When `fm_vc_required` is true, `await _issue_face_vc(...)` and return its activated row.
4. Translate any issuance failure to `_err("vc_issue_delayed", "VC 발급이 지연되고 있습니다. 잠시 후 다시 확인해 주세요.", status=503)`.
5. Keep `_schedule_face_vc` only for explicit non-required development mode; update its comment so it cannot be mistaken for production behavior.

Change `_issue_face_vc` to:

```python
async def _issue_face_vc(
    app, *, license_id, model_id, allowed, forbidden,
    unit_price, valid_until, digest,
) -> dict:
```

Use one `httpx.AsyncClient(timeout=_HOLDER_TIMEOUT)` and Task 3 `holder_client.post` for all three requests. Accept wallet `201` or `409`; require register `200` with `flowAComplete` or non-empty `userDid`; require issue `200` with non-empty string `vcId`.

Activate with a compare-and-set query:

```sql
update fm_licenses
   set vc_id = %s, status = 'active'
 where id = %s and status = 'pending' and vc_id is null
 returning <_LICENSE_CARD_COLS>
```

If it returns no row, insert the issued VC into `fm_vc_revocation_jobs` with `on conflict (vc_id) do nothing` before raising. This prevents an on-chain VC from escaping a concurrent local revoke.

- [ ] **Step 6: Make verify fail closed only when mandatory mode is active**

In `verify_license` preserve local status/expiry checks, then implement:

```python
required = bool(getattr(app.state.settings, "fm_vc_required", False))
base = getattr(app.state.settings, "opendid_holder_url", None)
secret = getattr(app.state.settings, "opendid_holder_hmac_secret", None)
vc_id = license_row.get("vc_id")

if required and (not base or not secret or not vc_id):
    raise _err("license_unverified", "라이선스 자격 증명(VC)이 준비되지 않았습니다.", status=409)
if not base or not secret or not vc_id:
    return
```

Call signed `/holder/vc/verify`. Map connection/timeout/non-200/malformed JSON to 503 `holder_unavailable`. Map `{verified:false}` or status other than `valid` to 409 `license_unverified`.

- [ ] **Step 7: Point the retry script at pending rows**

In `server/scripts/retry_pending_face_vcs.py`:

- Change query to `where status = 'pending' and vc_id is null`.
- Require both Holder URL and HMAC secret.
- Treat `_issue_face_vc` return as success instead of re-querying a nullable ID.
- Keep dry-run default and aggregate-only output.

- [ ] **Step 8: Run all issuance and gate tests**

Run:

```bash
cd server
.venv/bin/pytest -q \
  tests/test_holder_client.py \
  tests/test_facemarket_mandatory_vc.py \
  tests/test_facemarket_seller_loop.py \
  tests/test_facemarket_licenses.py
```

Expected: all tests pass.

- [ ] **Step 9: Commit mandatory activation and verification**

```bash
git add server/app/facemarket.py server/scripts/retry_pending_face_vcs.py \
  server/tests/test_facemarket_mandatory_vc.py \
  server/tests/test_facemarket_seller_loop.py server/tests/test_facemarket_licenses.py
git commit -m "Activate FaceMarket licenses only after VC proof

Constraint: Holder failure must leave a retryable license without permitting model use.
Rejected: Background best-effort activation | The API could expose active licenses with no VC.
Confidence: high
Scope-risk: moderate
Directive: Keep the pending-to-active update conditional; never overwrite revoke or expiry races.
Tested: Issuance state transitions, retry targeting, and fail-closed verification arms.
Not-tested: Real OpenDID issuance is covered by the deployment smoke task."
```

---

### Task 5: Durable Revoke Reconciler and Worker-Time VC Recheck

**Files:**

- Create: `server/app/workers/fm_vc_revocation_reconciler.py`
- Create: `server/tests/test_facemarket_vc_revocation.py`
- Modify: `server/app/facemarket.py`
- Modify: `server/app/main.py`
- Modify: `server/app/workers/detail_page_job.py`
- Modify: `server/app/workers/editor_image_job.py`
- Modify: `server/tests/test_facemarket_seller_loop.py`
- Modify: `server/tests/test_detail_page_license_face.py`
- Modify: `server/tests/test_cut_input_authority.py`

**Interfaces:**

- Produces: `enqueue_vc_revocation(conn, *, license_id: str, model_id: str, vc_id: str) -> None`.
- Produces: `FaceVcRevocationReconciler(app).start()`, `.stop()`, and `._sweep_once()` following `DraftAssetReclaimer` lifecycle style.
- Produces testable internal transitions: `._claim_one() -> dict | None`, `._holder_status(job) -> str`, `._request_revoke(job) -> None`, `._mark_retry(job, code) -> None`, and `._mark_revoked(job) -> None`.
- Claim contract: one due row through `FOR UPDATE SKIP LOCKED`, 60-second lease, status `processing`.
- Success contract: signed revoke followed by signed verify `status='revoked'`, then queue status `revoked`.
- Failure contract: status `retry`, increment attempts, clear lease, set `next_attempt_at = now() + min(300, 2^attempts) seconds`, store only a bounded reason code.
- Worker contract: API verification is repeated immediately before real-model asset use; any license or Holder failure ends the job and releases reserved credit without result or settlement.

- [ ] **Step 1: Change the existing route fake and prove halt plus enqueue share one commit**

In `server/tests/test_facemarket_seller_loop.py`, initialize `route` with `{"licenses": {}, "settlements": {}, "revocations": {}, "commit_count": 0}`. Increment `commit_count` in `_RouteConn.commit`. Add this arm to `_RouteCur.execute`:

```python
elif normalized.startswith("insert into fm_vc_revocation_jobs"):
    license_id, model_id, vc_id = params
    self.store["revocations"].setdefault(
        vc_id,
        {"license_id": license_id, "model_id": model_id, "status": "pending"},
    )
    self._one = None
```

Rename the local normalized SQL/params variables to `normalized` and `params` throughout that fake, then replace `test_revoke_calls_holder_on_transition` with:

```python
def test_revoke_route_halts_license_and_enqueues_in_one_commit(route, make_token):
    client, store = route
    token, user_id = _uid(make_token)
    store["licenses"]["lic-1"] = {
        "id": "lic-1", "model_id": "m-1", "vc_id": "vc-1",
        "status": "active", "user_id": user_id,
    }
    response = client.post(
        "/v1/facemarket/licenses/lic-1/revoke",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert store["licenses"]["lic-1"]["status"] == "revoked"
    assert store["revocations"]["vc-1"]["status"] == "pending"
    assert store["commit_count"] == 1
```

- [ ] **Step 2: Write reconciler orchestration tests with a complete in-memory harness**

Create `server/tests/test_facemarket_vc_revocation.py` with imports for `asyncio`, `httpx`, `types`, and `FaceVcRevocationReconciler`. Put this harness and test in the file:

```python
class HarnessReconciler(FaceVcRevocationReconciler):
    def __init__(self, holder_results):
        app = types.SimpleNamespace(state=types.SimpleNamespace())
        super().__init__(app)
        self.job = {
            "id": "job-1", "license_id": "lic-1", "model_id": "m-1",
            "vc_id": "vc-1", "attempts": 0, "lease_token": "lease-1",
            "status": "pending",
        }
        self.holder_results = iter(holder_results)
        self.revoke_calls = 0

    async def _claim_one(self):
        if self.job["status"] not in {"pending", "retry"}:
            return None
        self.job["status"] = "processing"
        return dict(self.job)

    async def _holder_status(self, _job):
        result = next(self.holder_results)
        if isinstance(result, Exception):
            raise result
        return result

    async def _request_revoke(self, _job):
        self.revoke_calls += 1

    async def _mark_retry(self, _job, code):
        self.job.update(status="retry", attempts=self.job["attempts"] + 1, last_error_code=code)

    async def _mark_revoked(self, _job):
        self.job["status"] = "revoked"


def test_reconciler_retries_transport_failure_then_confirms_revoked():
    reconciler = HarnessReconciler([
        httpx.ConnectError("holder down"),
        "valid",
        "revoked",
    ])
    asyncio.run(reconciler._sweep_once())
    assert reconciler.job["status"] == "retry"
    assert reconciler.job["last_error_code"] == "transport"

    asyncio.run(reconciler._sweep_once())
    assert reconciler.job["status"] == "revoked"
    assert reconciler.revoke_calls == 1


def test_already_revoked_job_skips_duplicate_revoke():
    reconciler = HarnessReconciler(["revoked"])
    asyncio.run(reconciler._sweep_once())
    assert reconciler.job["status"] == "revoked"
    assert reconciler.revoke_calls == 0
```

- [ ] **Step 3: Replace detail worker degradation tests with fail-closed race tests**

In `server/tests/test_detail_page_license_face.py`, extend `_license_row` with `id`, `model_id`, `vc_id`, and `unit_price`, and make `_Cur.fetchone` return it for the full `_LICENSE_VERIFY_COLS` query. Replace the revoked worker-time test with the existing test helpers, so no new undefined helper is introduced:

```python
def test_revoked_license_at_worker_time_fails_job_and_refunds(monkeypatch):
    captured = {}
    _patch_inputs(
        monkeypatch, captured,
        project={"copywriting": False, "facemarket_license_id": LIC_ID},
    )
    app, _ = _app(_license_row(status="revoked"))
    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=1)))

    assert captured.get("calls") is None
    assert captured["failure"]["reserved"] == 1
    assert app.state.r2_face.gets == []
```

Replace the expired case the same way, asserting `captured.get("calls") is None` and `captured["failure"]["reserved"] == 1`.

- [ ] **Step 4: Add Holder-outage race coverage to both workers**

In the detail test `_app`, accept `vc_required=False`, and pass `fm_vc_required`, Holder URL, and secret through `make_settings`. Add an active-license test that monkeypatches `facemarket.holder_client.post` to raise `httpx.ConnectError`, runs `run_detail_page_job`, then asserts no `captured["calls"]`, full reservation release, and no private-face read.

In `server/tests/test_cut_input_authority.py`, use `_patch_editor_common`, patch `repo.finalize_editor_image_failure` to capture the failure, make `resolve_model_license` return an active row containing `vc_id="vc-1"`, make `identity_source.resolve_real_model_assets` return two face refs, and make `facemarket.holder_client.post` raise `httpx.ConnectError`. Run `run_editor_image_job` with the existing UUID model payload, then assert `captured.get("generations") is None`, the failure received `reserved=1`, and both public and face R2 read lists remain empty.

- [ ] **Step 5: Run focused tests and verify current behavior fails**

Run:

```bash
cd server
.venv/bin/pytest -q \
  tests/test_facemarket_vc_revocation.py \
  tests/test_detail_page_license_face.py \
  tests/test_cut_input_authority.py
```

Expected: FAIL because revoke is best-effort and workers only inspect local active state.

- [ ] **Step 6: Enqueue revoke in the same transaction as local halt**

Implement in `server/app/facemarket.py`:

```python
async def enqueue_vc_revocation(
    conn, *, license_id: str, model_id: str, vc_id: str
) -> None:
    async with conn.cursor() as cur:
        await cur.execute(
            """insert into fm_vc_revocation_jobs
                   (license_id, model_id, vc_id)
               values (%s, %s, %s)
               on conflict (vc_id) do nothing""",
            (license_id, model_id, vc_id),
        )
```

In `revoke_license`, update local status and enqueue before the single commit. Remove direct `_revoke_holder_vc` best-effort invocation. Repeated owner revoke returns the same local row and leaves the unique queue row unchanged.

- [ ] **Step 7: Implement the reconciler by reusing the existing background lifecycle pattern**

Create `FaceVcRevocationReconciler` with the same `start/stop/_run` structure as `DraftAssetReclaimer`, a 3-second idle interval, and `_sweep_once()` that:

1. Requeues `processing` rows whose `lease_expires_at <= now()`.
2. Claims one due row with `FOR UPDATE SKIP LOCKED` and a generated UUID lease token.
3. Calls signed revoke unless a first signed verify already returns `revoked`.
4. Calls signed verify and accepts only `{status:'revoked'}`.
5. Marks success by `id + lease_token`; otherwise records a bounded code from `transport`, `http_status`, `invalid_body`, or `not_revoked` and schedules retry.

Do not store response body, VC body, nonce, signature, URL, or exception text.

In `server/app/main.py`, start it only when pool exists and `settings.fm_vc_required` is true. Stop it before closing the pool.

- [ ] **Step 8: Re-run the common VC gate inside both workers**

In `detail_page_job.py`, load the same columns as `_LICENSE_VERIFY_COLS` and call:

```python
await facemarket.verify_license(app, license_row)
```

before `_load_license_face`, `resolve_real_model_assets`, or any generation provider call. Let the existing job failure path release the reservation. Do not downgrade revoke/expiry/Holder failures to faceless generation.

In `editor_image_job.py`, call the same function immediately after `resolve_model_license` and before loading real refs. Ensure the current success finalizer and settlement hook are unreachable on gate failure.

- [ ] **Step 9: Run reconciler and worker race tests**

Run:

```bash
cd server
.venv/bin/pytest -q \
  tests/test_facemarket_vc_revocation.py \
  tests/test_facemarket_seller_loop.py \
  tests/test_detail_page_license_face.py \
  tests/test_cut_input_authority.py
```

Expected: all tests pass.

- [ ] **Step 10: Commit durable revoke and queue-time enforcement**

```bash
git add server/app/facemarket.py server/app/main.py \
  server/app/workers/fm_vc_revocation_reconciler.py \
  server/app/workers/detail_page_job.py server/app/workers/editor_image_job.py \
  server/tests/test_facemarket_vc_revocation.py \
  server/tests/test_facemarket_seller_loop.py \
  server/tests/test_detail_page_license_face.py \
  server/tests/test_cut_input_authority.py
git commit -m "Keep revoked VC state blocked through outages and races

Constraint: Local model use must stop before an external revoke can succeed.
Rejected: One synchronous revoke attempt | Holder outages would permanently lose the intent.
Confidence: high
Scope-risk: broad
Directive: Worker-time verification must remain before asset reads and provider calls.
Tested: Durable retry, idempotent enqueue, stale lease recovery, detail-page and editor race failures.
Not-tested: Long-running production retry cadence requires staging observation."
```

---

### Task 6: Private Listener Configuration, Authenticated Smoke, and Hermetic Local QA

**Files:**

- Modify: `deploy/opendid/systemd/opendid-tas.service`
- Modify: `deploy/opendid/systemd/opendid-issuer.service`
- Modify: `deploy/opendid/systemd/opendid-cas.service`
- Modify: `deploy/opendid/systemd/fm-holder.service`
- Modify: `deploy/opendid/env.example`
- Modify: `deploy/opendid/README.md`
- Modify: `deploy/opendid/smoke.sh`
- Modify: `deploy/opendid/test-smoke.sh`
- Modify: `deploy/opendid/test-restore-state.sh`

**Interfaces:**

- TAS/Issuer/CAS bind `127.0.0.1` explicitly.
- Holder binds the target private interface from required `FM_HOLDER_BIND_ADDRESS`; local smoke overrides it to `127.0.0.1`.
- Smoke proves unsigned Holder mutation is rejected, then performs signed issue → Holder valid → revoke → Holder revoked → restart → Holder revoked.
- Restore test never observes host `lsof` state.

- [ ] **Step 1: Add static listener and smoke contract assertions**

Extend `deploy/opendid/test-smoke.sh` to assert:

```bash
want_grep 'holder_unsigned=blocked' "$tmp/out" 'smoke rejects unsigned Holder mutation'
want_grep 'holder_valid=valid' "$tmp/out" 'smoke verifies issued VC through Holder'
want_grep 'holder_revoked=revoked' "$tmp/out" 'smoke verifies revoke through Holder'
want_grep 'restart_holder_revoked=revoked' "$tmp/out" 'smoke verifies revoked after restart through Holder'
want_no_grep 'test-only-holder-hmac' "$FAKE_LOG" 'smoke never passes the secret value in argv'
for unit in opendid-tas opendid-issuer opendid-cas; do
  want_grep 'server\.address=127\.0\.0\.1' \
    "$ROOT/deploy/opendid/systemd/$unit.service" "$unit binds loopback"
done
want_grep 'server\.address=\$\{FM_HOLDER_BIND_ADDRESS\}' \
  "$ROOT/deploy/opendid/systemd/fm-holder.service" 'Holder binds configured private address'
```

Set `FM_HOLDER_HMAC_SECRET=test-only-holder-hmac` only in the harness environment; never write that fixture value to `deploy/opendid/env.example`.

- [ ] **Step 2: Run the smoke harness and verify the new assertions fail**

Run:

```bash
bash deploy/opendid/test-smoke.sh
```

Expected: FAIL because current smoke does not authenticate or call Holder verify.

- [ ] **Step 3: Bind each process to the intended interface**

Change entity `ExecStart` lines to include:

```text
--server.address=127.0.0.1
```

Change Holder `ExecStart` to include:

```text
--server.address=${FM_HOLDER_BIND_ADDRESS}
```

Add to `deploy/opendid/env.example`:

```dotenv
FM_HOLDER_BIND_ADDRESS=
```

Update `deploy/opendid/README.md`: only PostgreSQL, Besu, TAS, Issuer, and CAS are loopback-only. Holder listens on the Server 3 private interface and is protected by HMAC plus host/security-group rules allowing only Server 1.

- [ ] **Step 4: Sign smoke requests without placing the secret in argv**

In `deploy/opendid/smoke.sh`, require `FM_HOLDER_HMAC_SECRET` and implement `post_holder` by:

1. Writing the JSON body to a mode-600 file inside the existing smoke temp directory.
2. Calling embedded Python that reads the secret from the environment and body from the file, then prints timestamp, nonce, and signature only.
3. Passing those three non-secret values as curl headers.
4. Sending the exact body file with `--data-binary @file`.

Before the signed lifecycle, make one unsigned wallet POST and require HTTP 401; report only `holder_unsigned=blocked`.

After issue, call signed `/holder/vc/verify` and require `status=valid`. After revoke and again after full restart, require `status=revoked`. Keep VC IDs and bodies out of stdout.

- [ ] **Step 5: Make the restore harness independent of the developer machine**

In `deploy/opendid/test-restore-state.sh`, add a fake `lsof` executable to `$fakebin`:

```bash
cat >"$fakebin/lsof" <<'SH'
#!/usr/bin/env bash
exit 1
SH
chmod +x "$fakebin/lsof"
```

This makes fixture export observe “no writer listener” even if a real local TAS is using `127.0.0.1:8090`. Do not stop or inspect the developer's actual Java process from this test.

- [ ] **Step 6: Run all local deployment QA**

Run:

```bash
bash deploy/opendid/test-export-state.sh
bash deploy/opendid/test-restore-state.sh
bash deploy/opendid/test-opendid-provision.sh
bash deploy/opendid/test-issuer-provision-facelicense.sh
bash deploy/opendid/test-smoke.sh
python3 deploy/opendid/test-verify-vcmeta.py
bash -n deploy/opendid/*.sh scripts/opendid-provision.sh scripts/issuer-provision-facelicense.sh
OPENDID_POSTGRES_PORT=55432 \
OPENDID_POSTGRES_USER=fixture \
OPENDID_POSTGRES_PASSWORD=fixture \
OPENDID_POSTGRES_DB=fixture \
OPENDID_BESU_HTTP_PORT=58545 \
OPENDID_BESU_WS_PORT=58546 \
OPENDID_BESU_P2P_PORT=30303 \
docker compose -f deploy/opendid/infra.compose.yml config >/dev/null
```

Expected: every command exits 0.

- [ ] **Step 7: Run the full local application verification set**

Run:

```bash
cd services/fm-holder && ./gradlew clean test
cd ../../server
.venv/bin/pytest -q \
  tests/test_holder_client.py \
  tests/test_facemarket_vc_config.py \
  tests/test_facemarket_mandatory_vc_migration.py \
  tests/test_facemarket_mandatory_vc.py \
  tests/test_facemarket_vc_revocation.py \
  tests/test_facemarket_biometric_enrollment.py \
  tests/test_facemarket_biometrics.py \
  tests/test_facemarket_licenses.py \
  tests/test_facemarket_seller_loop.py \
  tests/test_detail_page_license_face.py \
  tests/test_cut_input_authority.py
```

Expected: all tests pass with no skipped security test.

- [ ] **Step 8: Commit deployment hardening and local proof**

```bash
git add deploy/opendid/systemd deploy/opendid/env.example deploy/opendid/README.md \
  deploy/opendid/smoke.sh deploy/opendid/test-smoke.sh \
  deploy/opendid/test-restore-state.sh
git commit -m "Prove authenticated VC lifecycle on the private Holder boundary

Constraint: Only Holder is reachable from Server 1; every other OpenDID listener remains loopback-only.
Rejected: Trusting host firewall without explicit binds and application auth | A host rule regression would expose unauthenticated services.
Confidence: high
Scope-risk: moderate
Directive: Keep smoke output aggregate-only and keep host process state out of fixture tests.
Tested: Deployment shell suites, Compose config, Holder clean test, and FaceMarket VC regression set.
Not-tested: Actual Server 3 ports and restart persistence require the authorized cutover window."
```

---

### Task 7: Authorized Server 3 and Server 1 Cutover

**Authority boundary:** This task is an external-production gate, not local implementation. Do not execute it from a developer session. An approved operator with the named Server 3, AWS, firewall, and deployment authorities performs it only after Tasks 1–6 are merged and locally green.

**Files:**

- Modify during the approved deployment window: `copilot/api/manifest.yml`
- Use without redesign: `docs/runbooks/facemarket-opendid-single-server.md`
- Use without redesign: `docs/runbooks/opendid-besu-clock.md`

**Interfaces:**

- Consumes external values: Server 3 private DNS/IP, approved deployment window, source/target access, encrypted transfer path, API SSM access, Server 3 secret-file access, firewall/Security Group authority.
- Produces API environment through SSM paths:
  - `/copilot/wearless/prod/secrets/OPENDID_HOLDER_URL`
  - `/copilot/wearless/prod/secrets/OPENDID_HOLDER_HMAC_SECRET`
- Produces Server 3 `/opt/opendid/opendid.env` containing the same HMAC secret and the private bind address.
- Produces the final release gate: issue → valid → revoke → revoked → restart → revoked, port isolation, production startup, pending → active API behavior.

- [ ] **Step 1: Stop unless all external authority is present**

Before changing any external state, verify these shell variables are set by the approved operator session:

```bash
: "${SERVER3_PRIVATE_DNS:?approved Server 3 private DNS is required}"
: "${SOURCE_ENV:?source OpenDID environment file is required}"
: "${CUTOVER_PARENT:?encrypted archive parent is required}"
: "${AWS_PROFILE:?production AWS profile is required}"
set -euo pipefail
umask 077
set -a
. "$SOURCE_ENV"
set +a
CUTOVER_DIR=$(mktemp -d "$CUTOVER_PARENT/opendid-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")
EXPORT_DIR="$CUTOVER_DIR/export"
SOURCE_INVENTORY="$CUTOVER_DIR/source-inventory.txt"
SOURCE_ISSUER_STATE="$CUTOVER_DIR/source-issuer-state.txt"
```

Stop the cutover if the Holder wallet backup gap has not been explicitly accepted, source and target snapshots cannot be synchronized, or product/security/privacy/operations approval is absent.

- [ ] **Step 2: Install one shared HMAC secret through approved secret stores**

On the approved operator host, create a mode-600 secret file and an AWS CLI request file without placing the value in command arguments:

```bash
HMAC_FILE=$(mktemp "$CUTOVER_PARENT/fm-holder-hmac.XXXXXX")
HMAC_REQUEST=$(mktemp "$CUTOVER_PARENT/fm-holder-hmac-request.XXXXXX.json")
chmod 600 "$HMAC_FILE" "$HMAC_REQUEST"
openssl rand -hex 32 >"$HMAC_FILE"
python3 - "$HMAC_FILE" "$HMAC_REQUEST" <<'PY'
import json, pathlib, sys
secret = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8").strip()
if len(bytes.fromhex(secret)) < 32:
    raise SystemExit("HMAC secret must be at least 32 bytes")
request = {
    "Name": "/copilot/wearless/prod/secrets/OPENDID_HOLDER_HMAC_SECRET",
    "Type": "SecureString",
    "Value": secret,
    "Overwrite": True,
}
pathlib.Path(sys.argv[2]).write_text(json.dumps(request), encoding="utf-8")
PY
aws --profile "$AWS_PROFILE" ssm put-parameter \
  --cli-input-json "file://$HMAC_REQUEST" >/dev/null
aws --profile "$AWS_PROFILE" ssm put-parameter \
  --name /copilot/wearless/prod/secrets/OPENDID_HOLDER_URL \
  --type SecureString --overwrite \
  --value "http://${SERVER3_PRIVATE_DNS}:8100" >/dev/null
```

Transfer `HMAC_FILE` to Server 3 through the approved encrypted credential channel, set `SERVER3_HMAC_FILE` to that mode-600 target path, then update the root-owned environment file without printing the secret:

```bash
: "${SERVER3_HMAC_FILE:?transferred Server 3 HMAC file is required}"
sudo python3 - "$SERVER3_HMAC_FILE" /opt/opendid/opendid.env <<'PY'
import pathlib, sys
secret = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8").strip()
env_path = pathlib.Path(sys.argv[2])
lines = [
    line for line in env_path.read_text(encoding="utf-8").splitlines()
    if not line.startswith("FM_HOLDER_HMAC_SECRET=")
]
lines.append(f"FM_HOLDER_HMAC_SECRET={secret}")
env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
sudo chown root:opendid /opt/opendid/opendid.env
sudo chmod 0640 /opt/opendid/opendid.env
sudo rm -f "$SERVER3_HMAC_FILE"
```

After Server 3 installation succeeds, remove the operator-host request artifacts with `rm -f "$HMAC_FILE" "$HMAC_REQUEST"`. Do not print either SecureString value during verification.

- [ ] **Step 3: Install Server 3 artifacts and synchronized state**

Follow `docs/runbooks/facemarket-opendid-single-server.md` exactly:

```bash
deploy/opendid/inventory-state.sh >"$SOURCE_INVENTORY"
deploy/opendid/export-state.sh "$EXPORT_DIR"
(cd "$EXPORT_DIR" && sha256sum -c SHA256SUMS)
```

Transfer the complete cutover directory through the approved encrypted channel, then on the fresh target run dry-run before apply:

```bash
: "${REPO:?repository root on target is required}"
: "${CUTOVER_DIR:?transferred cutover directory is required}"
cd "$REPO"
EXPORT_DIR="$CUTOVER_DIR/export"
SOURCE_INVENTORY="$CUTOVER_DIR/source-inventory.txt"
SOURCE_ISSUER_STATE="$CUTOVER_DIR/source-issuer-state.txt"
set -a
. /opt/opendid/opendid.env
set +a
(cd "$CUTOVER_DIR" && sha256sum -c SOURCE-STATE.sha256)
sudo --preserve-env=OPENDID_POSTGRES_USER,OPENDID_POSTGRES_PASSWORD,OPENDID_POSTGRES_DB,OPENDID_POSTGRES_VOLUME,OPENDID_BESU_VOLUME \
  deploy/opendid/restore-state.sh "$EXPORT_DIR"
sudo --preserve-env=OPENDID_POSTGRES_USER,OPENDID_POSTGRES_PASSWORD,OPENDID_POSTGRES_DB,OPENDID_POSTGRES_VOLUME,OPENDID_BESU_VOLUME \
  deploy/opendid/restore-state.sh "$EXPORT_DIR" --apply
```

Do not proceed if checksum, DB/table counts, VC/revoke digest, chain ID, contract address, or full VC metadata comparison differs.

- [ ] **Step 4: Start Server 3, prove restored state, then run the authenticated lifecycle smoke**

After UTC/NTP preflight:

```bash
: "${SERVER3_PRIVATE_BIND_ADDRESS:?approved Server 3 private IP is required}"
set -a
. /opt/opendid/opendid.env
set +a
: "${FM_HOLDER_HMAC_SECRET:?set Holder HMAC secret in /opt/opendid/opendid.env}"
: "${FM_HOLDER_BIND_ADDRESS:?set Holder private bind in /opt/opendid/opendid.env}"
test "$FM_HOLDER_BIND_ADDRESS" = "$SERVER3_PRIVATE_BIND_ADDRESS"

sudo systemctl daemon-reload
sudo systemctl enable --now opendid-infra opendid-tas opendid-issuer opendid-cas fm-holder
```

Run runbook Sections 5.1-5.4 in order. Stop on any source/target entity, VC/revoke count or digest mismatch, chain/contract mismatch, full VC metadata mismatch, or open Orchestrator surface. Only after those exact and read-only proofs pass, run the mutating smoke last:

```bash
sudo --preserve-env=FM_HOLDER_HMAC_SECRET,FM_HOLDER_BIND_ADDRESS \
  env OPENDID_SMOKE_MODE=managed deploy/opendid/smoke.sh
```

Expected aggregate output includes:

```text
holder_unsigned=blocked
holder_valid=valid
holder_revoked=revoked
restart_holder_revoked=revoked
smoke_result=ok
```

Stop and leave Server 1 unchanged if any line is absent.

- [ ] **Step 5: Prove network isolation from both relevant vantage points**

From Server 1:

```bash
nc -zvw3 "$SERVER3_PRIVATE_DNS" 8100
for port in 5432 8545 8090 8091 8094 9001; do
  ! nc -zvw2 "$SERVER3_PRIVATE_DNS" "$port"
done
```

From a host outside the private Server 1 security boundary:

```bash
for port in 5432 8545 8090 8091 8094 8100 9001; do
  ! nc -zvw2 "$SERVER3_PRIVATE_DNS" "$port"
done
```

Expected: only Server 1 → `8100` succeeds.

- [ ] **Step 6: Wire the production manifest only after Server 3 passes**

Modify `copilot/api/manifest.yml` variables:

```yaml
FACEMARKET_VC_REQUIRED: "true"
```

Add these fixed SSM mappings under `secrets`:

```yaml
OPENDID_HOLDER_URL: /copilot/wearless/prod/secrets/OPENDID_HOLDER_URL
OPENDID_HOLDER_HMAC_SECRET: /copilot/wearless/prod/secrets/OPENDID_HOLDER_HMAC_SECRET
```

Remove the existing comment that says VC issuance is non-fatal and Holder may remain unset.

- [ ] **Step 7: Deploy Server 1 and prove startup plus API state transition**

Run:

```bash
copilot svc deploy --name api --env prod
curl -fsS https://api.wearless.kr/healthz >/dev/null
```

Use the approved staging/model-owner account to create one FaceMarket license. Verify through service-private DB inspection that it is first inserted as `pending`, then becomes `active` only with a non-empty `vc_id`. Verify one real-model generation request reaches job creation only while Holder returns `valid`.

- [ ] **Step 8: Verify failure gates before ending the window**

Temporarily block Server 1 → Holder in the approved test rule, then verify a real-model request returns 503 before job/credit reservation. Restore the rule and verify service recovery. Revoke the test license and verify local status changes immediately, the durable job reaches `revoked`, and no later generation succeeds.

- [ ] **Step 9: Commit the production wiring decision**

```bash
git add copilot/api/manifest.yml
git commit -m "Require the private OpenDID Holder in production

Constraint: Server 3 lifecycle, restart persistence, and port isolation passed before Server 1 wiring.
Rejected: Leaving mandatory VC disabled after infrastructure cutover | It would preserve the fail-open product path.
Confidence: high
Scope-risk: broad
Directive: Never remove either Holder secret mapping while FACEMARKET_ENABLED is true in production.
Tested: Real lifecycle smoke, port probes, API startup, pending-to-active issuance, outage block, and revoke reconciliation.
Not-tested: Actual monetary payout remains out of scope."
```

## Final Verification

Before declaring the cutover complete, run or collect fresh evidence for every item:

```bash
git status --short
cd services/fm-holder && ./gradlew clean test
cd ../../server && .venv/bin/pytest -q \
  tests/test_holder_client.py \
  tests/test_facemarket_vc_config.py \
  tests/test_facemarket_mandatory_vc_migration.py \
  tests/test_facemarket_mandatory_vc.py \
  tests/test_facemarket_vc_revocation.py \
  tests/test_facemarket_licenses.py \
  tests/test_facemarket_seller_loop.py \
  tests/test_detail_page_license_face.py \
  tests/test_cut_input_authority.py
cd ..
bash deploy/opendid/test-export-state.sh
bash deploy/opendid/test-restore-state.sh
bash deploy/opendid/test-opendid-provision.sh
bash deploy/opendid/test-issuer-provision-facelicense.sh
bash deploy/opendid/test-smoke.sh
python3 deploy/opendid/test-verify-vcmeta.py
```

Completion additionally requires external evidence from Task 7: synchronized restore proof, authenticated real lifecycle including restart persistence, Server 1-only `8100` reachability, production startup with all mandatory settings, pending-to-active issuance, Holder outage pre-reservation block, and durable revoke completion.

## Stop Conditions

- Stop before Server 1 wiring if Holder build, authenticated smoke, restart persistence, DB/Besu/VC metadata comparison, or port isolation fails.
- Stop before mandatory VC activation if API startup can succeed without Holder URL or HMAC secret.
- Stop before completion if any active license has a null `vc_id`, any due revoke job is stuck without a recoverable lease, or either worker can call a generation provider after VC failure.
- Stop the operational cutover rather than weakening HMAC, timeout, status, or startup checks.
