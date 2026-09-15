"""얼굴 LoRA 는 **본인이 테스트컷을 확인한 뒤부터** 착용컷에 쓰인다.

학습은 승인 전에 돌 수 있다(등록 사진만 있으면 된다). 그때 붙인 행을 켜 두면, 본인이 자기
얼굴을 한 번도 못 본 상태에서 판매 컷에 얼굴이 들어간다. 그래서 두 곳을 묶는다.

  · seed_model_lora  — 모델이 verified 가 아니면 **끈 채로** 붙인다(resolve_enable).
  · confirm_test_cut — verified 로 바꾸는 같은 트랜잭션에서 켠다(_enable_ready_lora).

fm_model_loras 에는 partial unique index(model_id) where enabled 가 있다. 켜기 전에 끄지
않으면 두 번째 승인부터 UniqueViolation 으로 죽는다 — 아래 가짜 커서가 그 색인을 흉내 낸다.
"""
import asyncio

from app.facemarket_admin_models import _enable_ready_lora
from scripts.seed_model_lora import resolve_enable


class _UniqueViolation(RuntimeError):
    pass


class _Cur:
    """fm_model_loras 한 테이블만 아는 가짜 커서 + partial unique index 흉내."""

    def __init__(self, rows):
        self.rows = rows
        self.result = None
        self.queries = []

    async def execute(self, q, params=()):
        text = " ".join(q.split())
        self.queries.append(text)
        if text.startswith("select id::text as id from fm_model_loras"):
            ready = [r for r in self.rows
                     if r["model_id"] == params[0] and r["status"] == "ready"]
            ready.sort(key=lambda r: r["version"], reverse=True)
            self.result = {"id": ready[0]["id"]} if ready else None
        elif "set enabled = false" in text:
            for r in self.rows:
                if r["model_id"] == params[0] and r["id"] != params[1] and r["enabled"]:
                    r["enabled"] = False
            self.result = None
        elif "set enabled = true" in text:
            for r in self.rows:
                if r["id"] == params[0] and not r["enabled"]:
                    r["enabled"] = True
            self.result = None
        else:  # pragma: no cover - 이 테스트가 쓰지 않는 문장
            raise AssertionError(f"예상 밖 질의: {text}")
        self._check_index()

    def _check_index(self):
        seen = set()
        for r in self.rows:
            if not r["enabled"]:
                continue
            if r["model_id"] in seen:
                raise _UniqueViolation("fm_model_loras_one_enabled_uidx")
            seen.add(r["model_id"])

    async def fetchone(self):
        return self.result


def _row(row_id, model_id, version, *, enabled=False, status="ready"):
    return {"id": row_id, "model_id": model_id, "version": version,
            "enabled": enabled, "status": status}


def test_seed_defaults_off_before_confirmation():
    #: 승인 전 모델에 붙이는 건 "학습은 끝났다" 일 뿐이다 — 아직 쓰면 안 된다.
    assert resolve_enable(None, "pending") is False
    assert resolve_enable(None, "awaiting_confirm") is False
    assert resolve_enable(None, None) is False


def test_seed_defaults_on_for_verified_model():
    #: 이미 쓰이고 있는 모델의 재학습 교체 — 끄면 얼굴이 조용히 옛 버전으로 남는다.
    assert resolve_enable(None, "verified") is True


def test_seed_explicit_flag_wins():
    assert resolve_enable(True, "pending") is True
    assert resolve_enable(False, "verified") is False


def test_confirm_enables_latest_ready_lora():
    rows = [_row("a", "m1", 1), _row("b", "m1", 2)]
    cur = _Cur(rows)

    enabled = asyncio.run(_enable_ready_lora(cur, "m1"))

    assert enabled == "b"  # 최신 버전
    assert [r["enabled"] for r in rows] == [False, True]


def test_confirm_replaces_previously_enabled_row():
    #: 색인이 모델당 하나만 허용한다 — 켜기 전에 끄지 않으면 여기서 죽는다.
    rows = [_row("a", "m1", 1, enabled=True), _row("b", "m1", 2)]
    cur = _Cur(rows)

    enabled = asyncio.run(_enable_ready_lora(cur, "m1"))

    assert enabled == "b"
    assert [r["enabled"] for r in rows] == [False, True]


def test_confirm_without_lora_row_is_a_noop():
    #: 학습 전에 승인하는 경우 — verified 만 되고 얼굴 패스는 안 걸린다.
    rows = [_row("a", "other", 1, enabled=True)]
    cur = _Cur(rows)

    assert asyncio.run(_enable_ready_lora(cur, "m1")) is None
    assert rows[0]["enabled"] is True  # 남의 모델은 건드리지 않는다
    assert not any("set enabled" in q for q in cur.queries)


def test_confirm_skips_rows_that_are_not_ready():
    #: 학습 중(status='training')·실패 행을 켜면 파드가 받을 가중치가 없다.
    rows = [_row("a", "m1", 1), _row("b", "m1", 2, status="training")]
    cur = _Cur(rows)

    assert asyncio.run(_enable_ready_lora(cur, "m1")) == "a"
    assert [r["enabled"] for r in rows] == [True, False]
