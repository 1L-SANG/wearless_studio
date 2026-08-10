"""R2 고아 객체 — 업로드 직후 죽으면 아무도 그 객체를 모른다.

무엇이 잘못됐었나
-----------------
`_save_cut` 은 무작위 asset UUID 를 만들고 `put_bytes` 를 부른 **뒤에야** 후보 dict 를
돌려준다. 그 사이에 프로세스가 죽으면 그 객체는 DB 행도 없고 정리 목록에도 없다 —
영구 고아다. 재시도는 새 UUID 로 새 객체를 만들 뿐 옛것을 지우지 않는다.

왜 접두사 청소인가
------------------
업로드 **전에** 키를 적어 두는 방식은 "적기 직전에 죽는" 창을 다시 만든다. 창을 좁히는
대신 없앤다: `ai_key` 가 `.../ai/{job_id}/{asset_id}.{ext}` 라서 한 잡의 모든 객체가 한
접두사 아래 모인다. "접두사에 있는데 남기기로 한 것이 아니면 고아"는 크래시 시점과
무관하게 참이다.
"""

import asyncio
import inspect

from app.r2 import ai_key
from app.workers import mannequin_job


class FakeR2:
    def __init__(self, keys):
        self.keys = list(keys)
        self.deleted = []

    def list_prefix(self, prefix):
        return [k for k in self.keys if k.startswith(prefix)]

    def delete(self, key):
        self.deleted.append(key)


def _key(job, asset, ext="png"):
    return ai_key("u1", "p1", job, asset, ext)


def test_it_removes_objects_nothing_points_at():
    r2 = FakeR2([_key("j1", "orphan-1"), _key("j1", "orphan-2")])
    removed = asyncio.run(mannequin_job._sweep_job_orphans(
        r2, user_id="u1", project_id="p1", job_id="j1"))
    assert removed == 2
    assert sorted(r2.deleted) == sorted([_key("j1", "orphan-1"), _key("j1", "orphan-2")])


def test_it_never_touches_another_job():
    """접두사가 잡 단위라는 것이 이 청소의 안전 근거다 — 어긋나면 남의 컷을 지운다."""
    other = _key("j2", "keep-me")
    r2 = FakeR2([_key("j1", "orphan"), other])
    asyncio.run(mannequin_job._sweep_job_orphans(
        r2, user_id="u1", project_id="p1", job_id="j1"))
    assert other not in r2.deleted
    assert r2.deleted == [_key("j1", "orphan")]


def test_kept_keys_survive():
    keep_key = _key("j1", "adopted")
    r2 = FakeR2([keep_key, _key("j1", "orphan")])
    removed = asyncio.run(mannequin_job._sweep_job_orphans(
        r2, user_id="u1", project_id="p1", job_id="j1", keep={keep_key}))
    assert removed == 1
    assert keep_key not in r2.deleted


def test_a_listing_failure_does_not_break_the_job():
    """청소 실패가 잡 종결을 막으면 본말이 전도된다."""
    class Broken:
        def list_prefix(self, prefix):
            raise RuntimeError("network")

    assert asyncio.run(mannequin_job._sweep_job_orphans(
        Broken(), user_id="u1", project_id="p1", job_id="j1")) == 0


def test_a_delete_failure_does_not_stop_the_rest():
    class PartlyBroken(FakeR2):
        def delete(self, key):
            if "bad" in key:
                raise RuntimeError("nope")
            super().delete(key)

    r2 = PartlyBroken([_key("j1", "bad"), _key("j1", "good")])
    removed = asyncio.run(mannequin_job._sweep_job_orphans(
        r2, user_id="u1", project_id="p1", job_id="j1"))
    assert removed == 1
    assert r2.deleted == [_key("j1", "good")]


def test_kept_keys_collects_cuts_and_overlays():
    keep = mannequin_job._kept_keys([
        {"key": "a", "qc_debug_assets": [{"key": "a-dbg"}, {"no": "key"}]},
        {"key": "b"},
        "not-a-dict",
        None,
    ])
    assert keep == {"a", "a-dbg", "b"}


def test_the_terminal_paths_actually_sweep():
    """배선이 없으면 이 청소는 장식이다."""
    src = inspect.getsource(mannequin_job.run_mannequin_job)
    assert src.count("_sweep_job_orphans(") >= 2, src.count("_sweep_job_orphans(")
