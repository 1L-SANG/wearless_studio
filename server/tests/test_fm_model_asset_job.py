"""실존 모델 자산 빌드 워커 — 합성·등록·PII 경계 검증(QC off로 격리).

SQL 라우팅 stub 으로 워커의 여러 쿼리에 canned row 를 돌려주고, r2_face put/DB insert 를
기록해 (1) grid_sedcard+face_front 두 자산 등록, (2) assets_status='ready', (3) done 이벤트·
job result 에 얼굴 키 미포함(§1.4)을 단언한다.
"""

import asyncio
import contextlib
import io
import types

import numpy as np
from PIL import Image

from app.workers.fm_model_asset_job import run_fm_model_asset_job
from conftest import make_settings


def _png_bytes(color: int) -> bytes:
    out = io.BytesIO()
    Image.fromarray(np.full((128, 128, 3), color, np.uint8)).save(out, format="PNG")
    return out.getvalue()


class _Cur:
    def __init__(self, log):
        self._log = log
        self._last = None

    async def execute(self, sql, params=None):
        self._log.append((sql, params))
        s = " ".join(sql.split())
        if "from fm_models m join personalization_profiles" in s:
            self._last = {"status": "verified", "profile_id": "prof-1"}
        elif "from personalization_face_photos" in s:
            self._last = [
                {"angle": "front", "r2_key": "face/f.png", "mime_type": "image/png"},
                {"angle": "side", "r2_key": "face/s.png", "mime_type": "image/png"},
                {"angle": "angle45", "r2_key": "face/a.png", "mime_type": "image/png"},
            ]
        elif "from jobs where id=" in s and "for update" in s:
            self._last = {"id": "job-1"}  # lease 유지
        else:
            self._last = None

    async def fetchone(self):
        return self._last if not isinstance(self._last, list) else None

    async def fetchall(self):
        return self._last if isinstance(self._last, list) else []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Conn:
    def __init__(self, log):
        self._log = log

    async def commit(self):
        return None

    def cursor(self):
        return _Cur(self._log)


class _Pool:
    def __init__(self, log):
        self._log = log

    def connection(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield _Conn(self._log)

        return _cm()


class _FaceR2:
    def __init__(self):
        self.puts: list[tuple[str, str]] = []
        self.deletes: list[str] = []

    def get_bytes(self, key):
        return _png_bytes(hash(key) % 200)

    def put_bytes(self, key, data, mime):
        self.puts.append((key, mime))

    def delete(self, key):
        self.deletes.append(key)


def _job():
    return {"id": "job-1", "user_id": "u1", "lease_token": "u1:tok",
            "payload": {"modelId": "model-1"}}


def test_asset_build_registers_two_views_and_ready():
    log: list = []
    face_r2 = _FaceR2()
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        pool=_Pool(log), r2_face=face_r2,
        settings=make_settings(fm_face_qc_enabled=False)))

    asyncio.run(run_fm_model_asset_job(app, _job()))

    joined = " | ".join(" ".join(sql.split()) for sql, _ in log)
    # 두 자산 put (grid_sedcard, face_front)
    put_keys = [k for k, _ in face_r2.puts]
    assert any("grid_sedcard" in k for k in put_keys)
    assert any("face_front" in k for k in put_keys)
    # fm_model_assets 등록 + ready
    assert "insert into fm_model_assets" in joined
    assert "set assets_status='ready'" in joined
    # 성공 경로면 orphan 정리(delete) 없음
    assert face_r2.deletes == []


def test_asset_build_event_has_no_face_key():
    log: list = []
    face_r2 = _FaceR2()
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        pool=_Pool(log), r2_face=face_r2,
        settings=make_settings(fm_face_qc_enabled=False)))

    asyncio.run(run_fm_model_asset_job(app, _job()))

    # job_events / result 에 실린 payload(params)에 얼굴 R2 키가 없어야 한다(§1.4)
    for sql, params in log:
        if "job_events" in sql or "update jobs set status='done'" in sql:
            blob = str(params)
            assert "facemarket/models/model-1/grid_sedcard" not in blob
            assert "face/f.png" not in blob
