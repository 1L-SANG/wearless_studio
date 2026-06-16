"""라우트 HTTP 레벨 회귀 테스트.

검증 실패는 get_conn(DB) 이전에 발생하므로 풀 없이 TestClient로 검증 가능.
explicit-null PATCH가 500이 아니라 422 봉투로 떨어지는지 — 에러 핸들러 직렬화 버그 가드.
"""


def _auth(make_token):
    return {"Authorization": f"Bearer {make_token()}"}


def test_patch_explicit_null_compose_mode_is_422_not_500(client, make_token):
    res = client.patch(
        "/v1/projects/any-id", headers=_auth(make_token), json={"composeMode": None}
    )
    assert res.status_code == 422
    body = res.json()
    assert body["error"]["code"] == "validation_error"


def test_patch_explicit_null_copywriting_is_422(client, make_token):
    res = client.patch(
        "/v1/projects/any-id", headers=_auth(make_token), json={"copywriting": None}
    )
    assert res.status_code == 422


def test_patch_unknown_status_field_ignored_not_500(client, make_token):
    # status·adjustCount는 모델에 없어 무시 → 검증 통과 후 get_conn(풀 없음) → 503, 500 아님
    res = client.patch(
        "/v1/projects/any-id",
        headers=_auth(make_token),
        json={"status": "done", "adjustCount": 9},
    )
    assert res.status_code == 503  # db_unavailable (검증은 통과)
