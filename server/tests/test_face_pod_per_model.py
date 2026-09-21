"""파드 하나 = LoRA 하나 — **모델별로 다른 파드**로 보낸다(2026-09-14 사용자 결정).

왜 교체를 버렸나: Qwen-Image-Edit-2509 파이프라인이 ≈58GB 라 80GB 카드에 한 벌만 들어간다.
교체는 이전 파이프라인 참조를 쥔 채 새로 적재해 항상 OOM 이고, 두 번째 모델이 영영 안 올라간다.

그래서 라우팅이 판정의 전부다: 이 모델의 파드가 없으면 **남의 파드로 보내지 않는다**.
남의 파드는 다른 LoRA 를 물고 있어 409 만 돌려주고, 그 컷은 #309 규칙대로 실패한다(원본 미출고).
"""

import ast
import asyncio
import pathlib

import pytest

from app.agents import identity_source

MODEL_A = "11111111-1111-4111-8111-111111111111"
MODEL_B = "22222222-2222-4222-8222-222222222222"


class _Cur:
    def __init__(self, rows):
        self._rows = rows
        self.sql = []
        self._row = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, sql, params=None):
        one_line = " ".join(sql.split())
        self.sql.append((one_line, params))
        if "to_regclass" in one_line:
            self._row = {"t": "public.fm_face_render_pod"}
            return
        model_id = (params or (None,))[0]
        # 서버와 같은 규칙: 이 모델 전용 행 우선, 없으면 모델 미지정 행.
        rows = [r for r in self._rows if r["model_id"] in (model_id, None)]
        rows.sort(key=lambda r: (r["model_id"] is None,))
        self._row = rows[0] if rows else None

    async def fetchone(self):
        return self._row


class _Conn:
    def __init__(self, rows):
        self.cur = _Cur(rows)

    def cursor(self):
        return self.cur


class _Pool:
    def __init__(self, rows):
        self.conn = _Conn(rows)

    def connection(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return pool.conn

            async def __aexit__(self, *a):
                return False

        return _Ctx()


def _url(rows, model_id):
    return asyncio.run(identity_source.active_face_backend_url(_Pool(rows), model_id))


def test_each_model_goes_to_its_own_pod():
    rows = [{"pod_id": "pod-a", "model_id": MODEL_A}, {"pod_id": "pod-b", "model_id": MODEL_B}]
    assert "pod-a" in _url(rows, MODEL_A)
    assert "pod-b" in _url(rows, MODEL_B)


def test_a_model_without_a_pod_is_not_sent_to_someone_elses():
    """★ 이게 핵심이다. 남의 파드로 보내면 409 를 받고 그 컷이 죽는다 — 차라리 pod_not_ready 다."""
    rows = [{"pod_id": "pod-a", "model_id": MODEL_A}]
    assert _url(rows, MODEL_B) is None


def test_an_unassigned_pod_still_serves_as_the_fallback():
    """모델 미지정 행(구 단일 파드)은 모델별 파드를 세우기 전까지의 다리다."""
    rows = [{"pod_id": "pod-legacy", "model_id": None}]
    assert "pod-legacy" in _url(rows, MODEL_A)


def test_the_model_pod_wins_over_the_unassigned_one():
    rows = [{"pod_id": "pod-legacy", "model_id": None}, {"pod_id": "pod-a", "model_id": MODEL_A}]
    assert "pod-a" in _url(rows, MODEL_A)
    assert "pod-legacy" in _url(rows, MODEL_B)


def test_the_query_filters_by_model_and_prefers_the_exact_row():
    rows = [{"pod_id": "pod-a", "model_id": MODEL_A}]
    pool = _Pool(rows)
    asyncio.run(identity_source.active_face_backend_url(pool, MODEL_A))
    sql = [s for s, _ in pool.conn.cur.sql if "fm_face_render_pod" in s and "to_regclass" not in s]
    assert sql and "model_id = %s or model_id is null" in sql[0]
    assert "order by (model_id is null)" in sql[0]
    # 인라인 주석은 /* */ 여야 한다 — 대역이 개행을 접으면 -- 뒤가 통째로 주석이 된다.
    assert "--" not in sql[0]


@pytest.mark.parametrize("path,needle", [
    ("app/workers/editor_image_job.py", "active_face_backend_url(pool, _vary_model_id)"),
    ("app/workers/editor_image_job.py", "active_face_backend_url(\n                        pool, str(selected_model_id))"),
    ("app/facemarket.py", "active_face_backend_url(pool, model_id)"),
])
def test_every_caller_asks_for_its_own_model(path, needle):
    """모델을 안 주고 부르는 자리가 남으면 그 컷만 조용히 남의 파드로 간다."""
    root = pathlib.Path(__file__).resolve().parents[1]
    assert needle in (root / path).read_text(encoding="utf-8")


def test_detail_worker_takes_the_model_as_an_argument():
    """★ 상세 워커의 파드 조회는 **인자로 받은** 모델을 써야 한다.

    예전에는 람다가 `selected_model_id` 를 그냥 참조했는데 그 이름은 _gen_cuts 에 없다
    (1000행대 다른 함수의 지역 변수다). 람다는 늦게 평가되므로 import 도 테스트도 통과했고,
    **실제로 불리는 순간** NameError 로 컷이 죽었다 — 2026-09-21 운영에서 이미지를 다 만든
    뒤에 터져 컷 6장이 요금만 쓰고 버려졌다.

    소스 문자열 검사는 이걸 못 잡는다(문자열은 그대로 있었다). 그래서 **시그니처**를 본다.
    """
    import inspect

    from app.workers import detail_page_job

    params = inspect.signature(detail_page_job._gen_cuts).parameters
    assert "selected_model_id" in params, (
        "_gen_cuts 가 모델을 인자로 받아야 한다 — 바깥 이름을 잡으면 호출 순간 NameError 다")
    assert params["selected_model_id"].default is None


def test_the_migration_adds_the_column_and_one_pod_per_model():
    root = pathlib.Path(__file__).resolve().parents[2]
    sql = (root / "supabase/migrations/20260914140000_fm_face_render_pod_model.sql").read_text(
        encoding="utf-8")
    assert "add column if not exists model_id" in sql
    # 예전 인덱스(전체에서 하나)는 내려야 모델별 파드가 나란히 산다.
    assert "drop index if exists public.fm_face_render_pod_one_active" in sql
    # null 은 unique 에서 서로 다르게 취급된다 — coalesce 로 묶어야 구 파드도 하나만 남는다.
    assert "coalesce(model_id, '')" in sql


def test_the_pod_service_takes_its_lora_from_the_env():
    """운영·수동 스크립트가 쓸 env 이름을 못 박는다 — 이름이 갈리면 파드가 빈 채로 뜬다."""
    root = pathlib.Path(__file__).resolve().parents[1]
    text = (root / "face_render_service.py").read_text(encoding="utf-8")
    for name in ("FACE_RENDER_LORA_KEY", "FACE_RENDER_LORA_URL", "FACE_RENDER_LORA_SHA256"):
        assert name in text, name
    # 자동 켜기도 같은 이름을 넣는다(지금은 꺼져 있지만 인터페이스는 맞춰 둔다).
    autoscale = (root / "app/services/face_autoscale.py").read_text(encoding="utf-8")
    for name in ("FACE_RENDER_LORA_KEY", "FACE_RENDER_LORA_URL", "FACE_RENDER_LORA_SHA256"):
        assert name in autoscale, name
