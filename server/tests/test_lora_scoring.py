"""후보 채점·선정 + LoRA 등록. 순위 규칙의 정본은 v6_kit/score_v7_verify.py 다.

  1순위 컷 간 일관성 중앙 · 2순위 개별 중앙(기준셋 대비). 합격선 = 개별 중앙 0.70.

왜 일관성이 1순위인가: 개별 점수는 기준셋과 같은 원본에서 온 컨트롤로 그린 렌더라 조금 후하다.
일관성은 렌더끼리 비교라 그 편향이 없다.

여기서 지키는 것:
  · 원본 스크립트와 **같은 순위**가 나온다(저장된 result 픽스처)
  · 합격선 미달이면 행을 만들지 않는다
  · 두 번 돌려도 키·행이 안 는다(멱등)
  · 새 학습은 **꺼진 채로** 등록된다(켜는 건 등록자 승인 트랜잭션)
"""
import asyncio
import math

import pytest

from app.services import lora_scoring as ls
from app.services import model_lora


def _unit(*values):
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return [v / norm for v in values]


#: 기준셋 3장 — 거의 같은 방향(같은 사람).
REFS = [_unit(1, 0.02, 0), _unit(1, 0.05, 0), _unit(1, 0.01, 0)]


def _render(rid, vector, adopted=True):
    return {"id": rid, "adopted": adopted, "embedding": _unit(*vector) if vector else None}


def _result(checkpoints, references=None):
    return {"references": references if references is not None else REFS,
            "checkpoints": checkpoints}


# ── 순위 ──────────────────────────────────────────────────────────────────
def test_consistency_wins_over_the_individual_median():
    """★ 1순위가 일관성이다. 개별 점수가 더 높아도 서로 다른 얼굴이면 팔 수 없다."""
    scores = ls.evaluate(_result({
        # 개별은 높지만 렌더끼리 흩어진다
        "000000300": [_render("a", (1, 0.0, 0)), _render("b", (1, 0.9, 0)), _render("c", (1, 0.0, 0.9))],
        # 개별은 조금 낮지만 렌더끼리 똑같다
        "000001800": [_render("a", (1, 0.1, 0)), _render("b", (1, 0.1, 0)), _render("c", (1, 0.1, 0))],
    }))
    assert scores["rank"][0] == "000001800"
    assert scores["selected"] == "000001800"


def test_the_individual_median_breaks_a_consistency_tie():
    scores = ls.evaluate(_result({
        "000000300": [_render("a", (1, 0.3, 0)), _render("b", (1, 0.3, 0))],
        "000001200": [_render("a", (1, 0.02, 0)), _render("b", (1, 0.02, 0))],
    }))
    # 일관성은 둘 다 1.0 — 기준셋과 더 닮은 쪽이 이긴다.
    assert scores["rank"][0] == "000001200"


def test_gate_failures_are_left_out_of_the_score_but_recorded():
    scores = ls.evaluate(_result({
        "000000300": [_render("a", (1, 0.02, 0)), _render("b", None, adopted=False)],
    }))
    step = scores["scores"]["000000300"]
    assert step["n"] == 1 and step["gate_fail"] == ["b"]


# ── 합격선 ────────────────────────────────────────────────────────────────
def test_a_low_median_fails_instead_of_selecting():
    """★ 합격선 미달로 고르면 셀러가 산 적 없는 얼굴이 착용컷에 들어간다."""
    far = _unit(0.3, 1, 0)
    scores = ls.evaluate(_result({
        "000001800": [{"id": "a", "adopted": True, "embedding": far},
                      {"id": "b", "adopted": True, "embedding": far}],
    }))
    assert scores["selected"] is None
    assert "합격선" in scores["reason"]


def test_no_adopted_render_is_a_failure_not_a_pick():
    scores = ls.evaluate(_result({
        "000001800": [_render("a", None, adopted=False), _render("b", None, adopted=False)],
    }))
    assert scores["selected"] is None and "게이트" in scores["reason"]


def test_a_thin_reference_set_refuses_to_score():
    """기준이 3장 미만이면 자를 못 든다 — 대충 재느니 실패로 말한다."""
    scores = ls.evaluate(_result({"000001800": [_render("a", (1, 0, 0))]}, references=REFS[:2]))
    assert scores["selected"] is None and "기준셋" in scores["reason"]


def test_the_pass_line_is_the_documented_number():
    assert ls.PASS_MEDIAN == 0.70


# ── 등록 ──────────────────────────────────────────────────────────────────
class _R2:
    def __init__(self, existing=None):
        self.objects = dict(existing or {})
        self.puts = []

    def head(self, key):
        return {"size": len(self.objects[key])} if key in self.objects else None

    def put_bytes(self, key, data, mime, cache=None):
        self.puts.append(key)
        self.objects[key] = data


class _Cur:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=()):
        text = " ".join(sql.split())
        self.conn.queries.append((text, params))
        if text.startswith("select coalesce(max(version)"):
            self.conn.result = {"next": self.conn.max_version + 1}
        elif text.startswith("insert into fm_model_loras"):
            key = (params[0], params[1])
            self.conn.rows[key] = {"enabled": params[2], "key": params[5], "sha": params[6]}
            self.conn.result = {"id": f"lora-{len(self.conn.rows)}"}
        elif "set enabled = false" in text:
            for row in self.conn.rows.values():
                row["enabled"] = False
            self.conn.result = None
        else:
            self.conn.result = None

    async def fetchone(self):
        return self.conn.result


class _Conn:
    def __init__(self, max_version=0):
        self.queries = []
        self.rows = {}
        self.result = None
        self.max_version = max_version

    def cursor(self):
        return _Cur(self)


def _register(conn, r2, **over):
    kwargs = dict(model_id="m1", weights=b"weights-bytes", filename="ohwx_man_v1_000001800.safetensors",
                  trigger_token="ohwx man", base_model="Qwen/Qwen-Image-Edit-2509",
                  trained_steps=1800, version=1)
    kwargs.update(over)
    return asyncio.run(model_lora.register(conn, r2, **kwargs))


def test_a_new_lora_is_registered_switched_off():
    """★ 학습이 끝났다고 얼굴이 바로 팔리면 본인이 자기 얼굴을 못 본 채로 상품이 나간다."""
    conn, r2 = _Conn(), _R2()
    result = _register(conn, r2)

    assert result["enabled"] is False
    assert conn.rows[("m1", 1)]["enabled"] is False
    assert not any("set enabled = false" in text for text, _ in conn.queries)


def test_running_twice_does_not_add_a_key_or_a_row():
    conn, r2 = _Conn(), _R2()
    first = _register(conn, r2)
    second = _register(conn, r2)

    assert first["key"] == second["key"] and first["sha256"] == second["sha256"]
    assert r2.puts == [first["key"]], "같은 크기의 객체가 있으면 다시 안 올린다"
    assert len(conn.rows) == 1
    assert first["uploaded"] is True and second["uploaded"] is False


def test_a_retrain_takes_the_next_version():
    conn, r2 = _Conn(max_version=2), _R2()
    result = _register(conn, r2, version=None)
    assert result["version"] == 3
    assert result["key"].endswith("/loras/v3_ohwx_man_v1_000001800.safetensors")


def test_enabling_turns_the_other_versions_off_first():
    """partial unique(model_id) where enabled — 먼저 끄지 않으면 insert 가 통과 못 한다."""
    conn, r2 = _Conn(), _R2()
    _register(conn, r2, enable=True)
    order = [text for text, _ in conn.queries]
    assert any("set enabled = false" in text for text in order)
    assert order.index(next(t for t in order if "set enabled = false" in t)) < \
        order.index(next(t for t in order if t.startswith("insert into fm_model_loras")))


def test_the_learned_look_is_stored_not_the_current_one():
    """머리·얼굴형은 **이 LoRA 가 학습한 모습**이다 — 나중에 머리를 잘라도 이 값은 안 바뀐다."""
    conn, r2 = _Conn(), _R2()
    _register(conn, r2, physique={"hair_length": "short", "hair_color": "black",
                                  "hair_texture": "straight", "face_shape": "oval",
                                  "jaw_line": "defined"})
    _text, params = next((t, p) for t, p in conn.queries if t.startswith("insert into fm_model_loras"))
    assert params[7:12] == ("short", "black", "straight", "oval", "defined")


def test_the_result_carries_no_bytes_or_urls():
    conn, r2 = _Conn(), _R2()
    result = _register(conn, r2)
    import json

    text = json.dumps(result)
    assert "http" not in text and "weights-bytes" not in text
    assert set(result) == {"id", "version", "key", "sha256", "bytes", "uploaded", "enabled"}


# ── 파드가 결과를 올리는 자리 ─────────────────────────────────────────────
def test_the_pod_pushes_the_scoring_input():
    from app.services import lora_train_pod as ltp

    assert ltp.result_key("m1", "r1").endswith("/training/r1/result.json")
    assert "VERIFY_PUT_URL" in ltp.POD_BOOT_SCRIPT
    # 학습이 끝난 **그 파드**에서 이어 그린다 — 따로 만들면 54GB 적재를 한 번 더 한다.
    assert ltp.POD_BOOT_SCRIPT.index("verify start") > ltp.POD_BOOT_SCRIPT.index("train start")
