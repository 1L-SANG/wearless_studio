"""마네킹 실작동(real-wire) 스모크 — 프론트(httpAdapter)가 때리는 실제 HTTP 경로를 그대로 재연.

기획: documents/mannequin_realwire_plan.md §2 (P0 무과금 게이트 → P1 유료).
전용 스모크 계정(qa-smoke@wearless.kr)을 admin API로 확보(비밀번호는 매 실행 랜덤 재설정,
저장 안 함 — 실계정 비변경 원칙). 유료 구간(P1)은 --paid 플래그에서만.

실행:  cd server && .venv/bin/python -m scripts.smoke_realwire            # P0 무과금 게이트만
       cd server && .venv/bin/python -m scripts.smoke_realwire --paid    # P0 + P1(마네킹 생성·재생성, 4크레딧)
옵션:  --api http://127.0.0.1:8000  --front <정면 사진 경로>
"""

import argparse
import os
import secrets
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"
SMOKE_EMAIL = "qa-smoke@wearless.kr"
DEFAULT_FRONT = ROOT / "spike" / "input" / "tee-fb" / "1_front.jpeg"


def _load_env(path: Path):
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env(SERVER / ".env")

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
ANON = os.environ["SUPABASE_ANON_KEY"]
SERVICE = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

_gates: list[tuple[str, bool, str]] = []


def gate(name: str, ok: bool, note: str = ""):
    _gates.append((name, ok, note))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {note}" if note else ""))
    if not ok:
        _finish(1)


def _finish(code: int):
    print("\n===== 게이트 요약 =====")
    for name, ok, note in _gates:
        print(f"  {'✅' if ok else '❌'} {name}" + (f" — {note}" if note else ""))
    sys.exit(code)


# ---------- 세션 (S0·S1) ----------

def ensure_smoke_session() -> str:
    """스모크 유저 확보 + 비밀번호 랜덤 재설정 + password grant → access_token."""
    admin = {"apikey": SERVICE, "Authorization": f"Bearer {SERVICE}"}
    password = secrets.token_urlsafe(18)
    with httpx.Client(timeout=20) as c:
        # 존재 확인 (admin 목록 필터)
        r = c.get(f"{SUPABASE_URL}/auth/v1/admin/users", params={"email": SMOKE_EMAIL}, headers=admin)
        r.raise_for_status()
        users = [u for u in r.json().get("users", []) if u.get("email") == SMOKE_EMAIL]
        if users:
            uid = users[0]["id"]
            r = c.put(f"{SUPABASE_URL}/auth/v1/admin/users/{uid}", headers=admin,
                      json={"password": password})
            r.raise_for_status()
        else:
            r = c.post(f"{SUPABASE_URL}/auth/v1/admin/users", headers=admin,
                       json={"email": SMOKE_EMAIL, "password": password, "email_confirm": True})
            r.raise_for_status()
        # password grant (프론트 supabase-js 와 동일한 GoTrue 토큰 엔드포인트)
        r = c.post(f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
                   headers={"apikey": ANON, "Content-Type": "application/json"},
                   json={"email": SMOKE_EMAIL, "password": password})
        r.raise_for_status()
        return r.json()["access_token"]


# ---------- API 헬퍼 (httpAdapter.http 미러) ----------

class Api:
    def __init__(self, base: str, token: str):
        self.c = httpx.Client(base_url=base, timeout=30,
                              headers={"Authorization": f"Bearer {token}"})

    def call(self, method: str, path: str, **kw):
        r = self.c.request(method, path, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else None

    def poll_job(self, job_id: str, timeout_s: int = 300) -> dict:
        """A6: 타임아웃 시 재클릭(재시도) 금지 — 동일 job 을 끝까지 관찰만 한다."""
        t0 = time.time()
        last = -1
        while True:
            job = self.call("GET", f"/v1/jobs/{job_id}")
            if job.get("progress") != last:
                last = job.get("progress")
                print(f"    · job {job_id[:8]} {job['status']} {last}%")
            if job["status"] in ("done", "error"):
                return job
            if time.time() - t0 > timeout_s:
                raise RuntimeError(f"job {job_id} 관찰 타임아웃({timeout_s}s) — 상태={job['status']}")
            time.sleep(2)


class InlineWorker:
    """마네킹 job 을 **로컬 워커 인프로세스**로 실행하는 러너.

    왜: 공유 Supabase DB 를 폴링하는 다른 dispatcher(로컬 3초 폴러·구버전 prod)가
    job 을 가로채면 검증 대상 코드가 실행되지 않는다(실측: prod 는 QC enabled 라
    top-only 컷을 전부 탈락시킴). pool 을 **미리 열어두고** 202 직후 수십 ms 내에
    id 지정 셀프-클레임해 레이스 창을 3초 폴 대비 ~1% 로 줄인다.
    """

    def __init__(self):
        from types import SimpleNamespace

        from app.agents.gemini_image import GeminiImageClient
        from app.config import load_settings
        from app.db import create_pool
        from app.r2 import R2Client

        self._s = load_settings()
        self.pool = create_pool(self._s.database_url)
        self.app = SimpleNamespace(state=SimpleNamespace(
            settings=self._s, pool=self.pool, r2=R2Client(self._s),
            gemini=GeminiImageClient(self._s)))

    async def open(self):
        await self.pool.open()

    async def close(self):
        await self.pool.close()

    async def claim_and_run(self, job_id: str) -> str:
        """반환: 'claimed'(로컬 인프로세스 실행) | 'stolen'(타 dispatcher 선점 — 결과는 폴링 판정)."""
        import uuid as _uuid

        from app import repo as _repo
        from app.workers.mannequin_job import run_mannequin_job

        lease = f"smoke-inline:{_uuid.uuid4()}"
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    update jobs set status = 'running', locked_by = %s, locked_at = now(),
                      started_at = coalesce(started_at, now()), progress = greatest(progress, 5)
                    where id = %s and status = 'pending'
                    returning {_repo._JOB_COLS}, locked_by as lease_token
                    """,
                    (lease, job_id),
                )
                job = await cur.fetchone()
            await conn.commit()
        if job is None:
            return "stolen"
        await run_mannequin_job(self.app, job)
        return "claimed"


def active_credits(api: Api) -> tuple[int, int]:
    """(active 원천 합계, reserved) — 표시 잔액이 아니라 원천 합으로 (A3)."""
    sources = api.call("GET", "/v1/credits/sources")
    total = sum(s.get("remainingCredits", 0) for s in sources if s.get("status") == "active")
    acct = api.call("GET", "/v1/me/account")
    return total, acct.get("reserved", 0) if isinstance(acct.get("reserved"), int) else 0


def fetch_image_ok(url: str, follow: bool = True) -> tuple[bool, str]:
    """<img> 경로 재연: 무인증 GET → (302 추적) → 이미지 매직바이트 확인."""
    with httpx.Client(timeout=20, follow_redirects=follow) as c:
        r = c.get(url)
        if r.status_code != 200:
            return False, f"status={r.status_code}"
        head = r.content[:4]
        if head[:3] == b"\xff\xd8\xff" or head == b"\x89PNG":
            return True, f"{len(r.content)//1024}KB"
        return False, f"not-image head={head!r}"


# ---------- 메인 ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--paid", action="store_true", help="P1 유료 구간(생성·재생성) 실행")
    ap.add_argument("--front", default=str(DEFAULT_FRONT))
    ap.add_argument("--reuse-project", default=None,
                    help="기존 프로젝트 재사용 — P0 업로드·분석과 S5 최초생성 건너뛰고 재생성부터(2크레딧 절약)")
    args = ap.parse_args()

    print("== S0·S1 스모크 세션 ==")
    token = ensure_smoke_session()
    gate("S1 스모크 계정 password grant", bool(token))
    api = Api(args.api, token)

    print("== S2 계정 ==")
    acct = api.call("GET", "/v1/me/account")
    gate("S2 /me/account 200", isinstance(acct, dict), f"credits={acct.get('credits')}")

    print("== S3 크레딧 원천 ==")
    total, reserved = active_credits(api)
    print(f"    active 합={total}, reserved={reserved}")
    if args.paid and total < 6:
        gate("S3 크레딧 충분(유료 실행 전 ≥6)", False,
             f"active={total} — pricing_plans 시드 후 grant 필요(스크립트는 지급 안 함)")
    gate("S3 reserved=0 (시작 불변식)", reserved == 0, f"reserved={reserved}")

    if args.reuse_project:
        print(f"== S4 스킵 (기존 프로젝트 재사용: {args.reuse_project}) ==")
        if not args.paid:
            _finish(0)
        import asyncio as _aio
        _aio.run(_paid_flow(api, args, args.reuse_project, skip_generate=True))
        _finish(0)

    print("== S4 P0 무과금 게이트 ==")
    proj = api.call("POST", "/v1/projects")
    pid = proj["id"]
    gate("S4a 프로젝트 생성", bool(pid), pid)

    cuts0 = api.call("GET", f"/v1/projects/{pid}/mannequins")
    gate("S4b 신규 프로젝트 cuts=0", cuts0 == [], f"{len(cuts0)}건")

    front = Path(args.front)
    blob = front.read_bytes()
    up = api.call("POST", "/v1/assets/upload-url",
                  json={"filename": front.name, "mime": "image/jpeg", "size": len(blob), "projectId": pid})
    with httpx.Client(timeout=60) as c:
        putr = c.put(up["uploadUrl"], content=blob, headers={"Content-Type": "image/jpeg"})
    gate("S4c R2 presigned PUT", putr.status_code in (200, 204), f"status={putr.status_code}")
    asset = api.call("POST", f"/v1/assets/{up['assetId']}/complete",
                     json={"projectId": pid, "mime": "image/jpeg", "filename": front.name})
    gate("S4d complete → url", bool(asset.get("url")))

    ok, note = fetch_image_ok(asset["url"])
    gate("S4e 공개 URL 픽셀 로드", ok, note)
    ok, note = fetch_image_ok(f"{args.api}/v1/assets/{up['assetId']}/file")
    gate("S4f <img> 경로(/file, 무인증 302) 픽셀 로드", ok, note)

    api.call("PATCH", f"/v1/projects/{pid}/product", json={
        "name": "스모크 반팔 티셔츠", "clothingType": "top",
        "colors": [{"id": "c1", "label": "블루", "isBase": True,
                    "images": [{"slot": "Front", "id": up["assetId"], "url": asset["url"]}]}],
    })
    prod = api.call("GET", f"/v1/projects/{pid}/product")
    has_front = any(c.get("isBase") and any(i.get("slot") == "Front" and i.get("id") for i in c.get("images", []))
                    for c in prod.get("colors", []))
    gate("S4g 상품 저장(정면 게이트 충족)", has_front)

    res = api.c.post(f"/v1/projects/{pid}/analyze")
    if res.status_code == 202:
        job = api.poll_job(res.json()["jobId"], timeout_s=180)
        gate("S4h 분석 job done (dispatcher 생존 증명)", job["status"] == "done",
             job.get("errorMessage") or "")
    else:
        gate("S4h 분석 202", False, f"status={res.status_code} {res.text[:200]}")
    analysis = api.call("GET", f"/v1/projects/{pid}/analysis")
    gate("S4i 분석 저장 확인", isinstance(analysis, dict) and bool(analysis), f"keys={len(analysis)}")

    if not args.paid:
        print("\n(무과금 P0까지 완료 — 유료 P1은 --paid 로 실행)")
        _finish(0)

    import asyncio as _aio
    _aio.run(_paid_flow(api, args, pid, skip_generate=False))
    _finish(0)


def _abs_src(api_base: str, src) -> str:
    s = str(src or "")
    return f"{api_base}{s}" if s.startswith("/v1/") else s


async def _paid_flow(api: Api, args, pid: str, *, skip_generate: bool):
    """P1 유료 구간 — InlineWorker(pool 선오픈)로 클레임 레이스 최소화."""
    worker = InlineWorker()
    await worker.open()
    try:
        after_gen, _ = active_credits(api)

        if not skip_generate:
            print("== S5 P1 유료: 마네킹 생성 ==")
            before, _ = active_credits(api)
            res = api.c.post(f"/v1/projects/{pid}/mannequins:generate")
            gate("S5a generate 202", res.status_code == 202, f"status={res.status_code} {res.text[:200]}")
            who = await worker.claim_and_run(res.json()["jobId"])
            print(f"    · inline worker: {who}")
            job = api.poll_job(res.json()["jobId"], timeout_s=420)
            gate("S5b 생성 job done", job["status"] == "done", job.get("errorMessage") or "")
            env1 = job.get("result") or {}
            gate("S5c 결과 봉투 cuts ≥1", len(env1.get("data") or []) >= 1)
            after_gen, reserved_gen = active_credits(api)
            gate("S5d 생성 차감 -2", before - after_gen == 2, f"{before}→{after_gen}")
            gate("S5e reserved=0 복귀", reserved_gen == 0)
            cut1 = (env1.get("data") or [{}])[0]
            ok, note = fetch_image_ok(_abs_src(args.api, cut1.get("src")))
            gate("S5f 생성 컷 <img> 경로 픽셀 로드", ok, note)

        prev_cuts = api.call("GET", f"/v1/projects/{pid}/mannequins")
        prev_max = max((c["version"] for c in prev_cuts), default=0)
        if prev_max == 0:
            gate("S6 전제: 기존 컷 존재", False, "재사용 프로젝트에 v1 컷이 없음")

        print("== S6 P1 유료: 핏 조정 재생성 ==")
        fit_profile = {"category": "top", "gender": "women", "axes": {"fit": "over"},
                       "source": "seller", "version": 1}
        res = api.c.post(f"/v1/projects/{pid}/mannequins:regenerate", json={"fitProfile": fit_profile})
        gate("S6a regenerate 202", res.status_code == 202, f"status={res.status_code} {res.text[:200]}")
        who = await worker.claim_and_run(res.json()["jobId"])
        print(f"    · inline worker: {who}")
        job2 = api.poll_job(res.json()["jobId"], timeout_s=420)
        gate("S6b 재생성 job done", job2["status"] == "done", job2.get("errorMessage") or "")

        after_regen, reserved_regen = active_credits(api)
        gate("S6c 재생성 차감 -2", after_gen - after_regen == 2, f"{after_gen}→{after_regen}")
        gate("S6d reserved=0 복귀", reserved_regen == 0)

        cuts = api.call("GET", f"/v1/projects/{pid}/mannequins")
        versions = sorted(c["version"] for c in cuts)
        # A/B 두 후보 모델 — 재생성은 후보별 max+1 append (예: [1,1] → [1,1,2,2])
        gate("S6e 새 버전 append(max+1)", max(versions) == prev_max + 1,
             f"{versions} (prev_max={prev_max})")

        saved = api.call("GET", f"/v1/projects/{pid}/analysis")
        gate("S6f fitProfile 영속(axes.fit=over)",
             (saved.get("fitProfile") or {}).get("axes", {}).get("fit") == "over")

        new_cut = next(c for c in cuts if c["version"] == prev_max + 1)
        ok, note = fetch_image_ok(_abs_src(args.api, new_cut["src"]))
        gate("S6g 재생성 컷 <img> 픽셀 로드", ok, note)

        print("== S7 선택 영속 ==")
        v1_id = next(c["id"] for c in cuts if c["version"] == 1)
        api.call("PATCH", f"/v1/projects/{pid}", json={"selectedMannequinId": v1_id})
        proj2 = api.call("GET", f"/v1/projects/{pid}")
        gate("S7 선택 저장·재조회 유지", proj2.get("selectedMannequinId") == v1_id,
             f"{proj2.get('selectedMannequinId')}")
    finally:
        await worker.close()


if __name__ == "__main__":
    main()
