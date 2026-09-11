"""파기 — 등록자 LoRA 가중치도 생체 파생물이라 같이 지워져야 한다.

fm_models 행은 지우지 않고 스크럽만 하므로 fk on delete cascade 가 돌지 않는다.
가중치 파일(face_keys)과 행(delete) 둘 다 명시적으로 처리하지 않으면
"파기 완료" 영수증이 나가고도 얼굴 LoRA 는 r2_face 에 남는다.

FakeDB·시나리오는 test_biometric_purge 의 것을 그대로 쓴다(파기 계약은 한 곳에만 둔다).
"""

from test_biometric_purge import _delete_where_in, _fake_case, _run

# 모델 프리픽스(facemarket/models/<id>/) 밖에 둔다 — 프리픽스 고아 스캔에 딸려 지워지면
# "명시 수집 한 줄" 이 실제로 도는지 증명하지 못한다.
_LORA_KEY_TMPL = "facemarket/loras/{model}/v1_000001500.safetensors"


def _with_loras(ctx, *, extra_model=None):
    """FakeDB 에 fm_model_loras 를 붙인다(기본 FakeDB 에는 없는 테이블 = 마이그 미적용 환경)."""
    db = ctx.db
    db.tables["fm_model_loras"] = []
    key = _LORA_KEY_TMPL.format(model=ctx.model)
    db.add("fm_model_loras", id="lora-a", model_id=ctx.model, version=1, enabled=True,
           status="ready", lora_r2_key=key, bucket="face", trigger_token="ohwx man",
           hair_length="short", face_shape="oval", trained_steps=1500)
    ctx.r2_face.keys.add(key)
    keys = {"target": key}
    if extra_model is not None:
        other = _LORA_KEY_TMPL.format(model=extra_model)
        db.add("fm_model_loras", id="lora-b", model_id=extra_model, version=1, enabled=True,
               status="ready", lora_r2_key=other, bucket="face", trigger_token="ohwx man")
        ctx.r2_face.keys.add(other)
        keys["other"] = other

    orig_select, orig_mutate = db.select, db.mutate

    def select(q, params):
        # delete 문에도 "from fm_model_loras" 가 들어간다 — select 로 가로채면 삭제가 안 돈다.
        if q.startswith("select lora_r2_key as k from fm_model_loras"):
            ids = set(params[0])
            return [{"k": r.get("lora_r2_key")} for r in db.tables["fm_model_loras"]
                    if r.get("model_id") in ids]
        return orig_select(q, params)

    def mutate(q, params):
        if q.startswith("delete from fm_model_loras"):
            return _delete_where_in(db.tables["fm_model_loras"], "model_id", params[0])
        return orig_mutate(q, params)

    db.select, db.mutate = select, mutate
    return keys


def test_purge_deletes_lora_weight_and_row():
    ctx = _fake_case()
    keys = _with_loras(ctx)

    result = _run(ctx, user_id=ctx.user, reason="withdrawal")

    assert result.complete is True
    # 영수증은 "없어졌음을 확인한 대상 수" 다 — 가중치가 그 대상에 들어가야 진짜 영수증이다.
    assert result.target_count == result.confirmed_absent_count
    assert keys["target"] in ctx.r2_face.deleted
    assert keys["target"] not in ctx.r2_face.keys
    assert ctx.db.tables["fm_model_loras"] == []


def test_other_models_lora_is_untouched():
    ctx = _fake_case()
    keys = _with_loras(ctx, extra_model=ctx.other_model)

    _run(ctx, user_id=ctx.user, reason="withdrawal")

    assert keys["other"] in ctx.r2_face.keys
    assert keys["other"] not in ctx.r2_face.deleted
    assert [r["id"] for r in ctx.db.tables["fm_model_loras"]] == ["lora-b"]


def test_purge_completes_when_table_absent():
    """마이그 미적용 환경(테이블 없음)에서도 파기는 완주한다 — _has 가드."""
    ctx = _fake_case()
    assert "fm_model_loras" not in ctx.db.tables

    result = _run(ctx, user_id=ctx.user, reason="withdrawal")

    assert result.complete is True
    assert not any("fm_model_loras" in q for q in ctx.db.queries)
