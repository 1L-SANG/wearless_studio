"""R2 순수 로직 단위테스트 — MIME 화이트리스트·키 규칙 (계약 §3).

네트워크(presigned/head/put)는 자격증명 필요 → 로컬 통합 검증으로 따로 확인.
"""

from app.r2 import R2Client, ai_key, ext_for_mime, upload_key


def test_ext_for_mime_whitelist():
    assert ext_for_mime("image/png") == "png"
    assert ext_for_mime("image/jpeg") == "jpg"
    assert ext_for_mime("IMAGE/WEBP") == "webp"  # 대소문자 무관
    # 허용 외 타입은 None → 업로드 거부 근거
    assert ext_for_mime("application/pdf") is None
    assert ext_for_mime("") is None
    assert ext_for_mime(None) is None


def test_upload_key_rule():
    key = upload_key("user-1", "proj-9", "asset-7", "png")
    assert key == "users/user-1/projects/proj-9/uploads/asset-7.png"


def test_ai_key_rule():
    key = ai_key("user-1", "proj-9", "job-3", "asset-7", "png")
    assert key == "users/user-1/projects/proj-9/ai/job-3/asset-7.png"


# ── list_prefix — 파기 고아 스캔의 기반(api-spec §3.5) ────────
class _StubPaginator:
    def __init__(self, pages):
        self._pages = pages
        self.kwargs = None

    def paginate(self, **kwargs):
        self.kwargs = kwargs
        return iter(self._pages)


class _StubS3:
    """boto3 클라이언트 대역 — list_objects_v2 페이지네이터만."""

    def __init__(self, pages):
        self.paginator = _StubPaginator(pages)

    def get_paginator(self, op):
        assert op == "list_objects_v2"
        return self.paginator


def _client_with_pages(pages) -> R2Client:
    # __init__ 은 boto3 실클라이언트를 만든다(자격증명 필요) → 순수 집계 로직만 떼어 검증.
    client = object.__new__(R2Client)
    client._s3 = _StubS3(pages)
    client._bucket = "test-bucket"
    return client


def test_list_prefix_aggregates_every_page():
    """1000건 초과 시 응답이 쪼개진다 — 첫 페이지만 읽으면 고아가 조용히 살아남는다."""
    client = _client_with_pages([
        {"Contents": [{"Key": "p/a.png"}, {"Key": "p/b.png"}]},
        {"Contents": [{"Key": "p/c.png"}]},
    ])
    assert client.list_prefix("p/") == ["p/a.png", "p/b.png", "p/c.png"]


def test_list_prefix_passes_bucket_and_prefix():
    client = _client_with_pages([{"Contents": [{"Key": "p/a.png"}]}])
    client.list_prefix("p/")
    assert client._s3.paginator.kwargs == {"Bucket": "test-bucket", "Prefix": "p/"}


def test_list_prefix_empty_when_no_contents_key():
    """빈 prefix 응답엔 Contents 키가 아예 없다 — KeyError 금지(정상 '고아 0건' 경로)."""
    client = _client_with_pages([{"KeyCount": 0}])
    assert client.list_prefix("p/") == []
