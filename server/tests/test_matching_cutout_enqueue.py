"""소스 구조 검증: 커스텀 매칭 등록 커밋 후 matching_cutout 잡이 enqueue 되는지.

전체 라우트 통합 테스트는 무거우므로(스토리지·DB·AI 목킹 다수) 소스 레벨로 다음을 검증한다:
- 헬퍼가 무과금(credits_reserved=0)인지
- "matching_cutout" kind 를 쓰는지
- 큐잉 실패를 삼키는지(except Exception)
- 호출부가 insert_custom_matching_item · 커밋 뒤에 있는지(등록을 절대 막지 않는다)
"""
import pathlib

SERVER = pathlib.Path(__file__).resolve().parents[1]


def test_enqueue_is_uncharged_and_swallows_and_after_commit():
    src = (SERVER / "app" / "routes.py").read_text(encoding="utf-8")

    fn_start = src.index("async def _enqueue_matching_cutout")
    fn = src[fn_start:fn_start + 1500]
    assert "credits_reserved=0" in fn, "무과금"
    assert "matching_cutout" in fn
    assert "except Exception" in fn, "큐잉 실패를 삼켜야 등록이 안 죽는다"

    # 호출부가 insert_custom_matching_item 커밋 뒤인지
    call = src.index("await _enqueue_matching_cutout(")
    commit_before = src.rfind("await conn.commit()", 0, call)
    insert = src.rfind("insert_custom_matching_item", 0, call)
    assert insert < commit_before < call, "enqueue 는 insert·커밋 뒤"
