"""서버리스 핸들러 — 파드 서비스와 **같은 구현**을 부르는가, 실패가 사유로 돌아오는가.

핸들러 파일 자체는 임포트할 수 없다(맨 아래에서 runpod.serverless.start() 가 블로킹으로 돈다).
그래서 소스를 읽어 계약을 고정하고, 갈라질 수 있는 부분만 실제 모듈로 확인한다:

  · `run_render` · `run_upscale` 이 인증 없이 부를 수 있는 자리로 갈라져 있는가
    — 이게 없으면 핸들러가 가짜 토큰을 지어내거나 렌더 구현을 복제하게 된다.
  · 파드 라우트는 여전히 인증을 지나는가 — 가르면서 파드가 열리면 안 된다.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

SERVER_DIR = Path(__file__).resolve().parents[1]
HANDLER = SERVER_DIR / "deploy" / "face_render_serverless" / "handler.py"
DOCKERFILE = SERVER_DIR / "deploy" / "face_render_serverless" / "Dockerfile"


def _handler_source() -> str:
    return HANDLER.read_text(encoding="utf-8")


def test_the_handler_calls_the_pod_implementation_instead_of_copying_it():
    """두 경로가 다른 코드를 돌면 "파드에서는 되는데 서버리스는 다르게 나온다"가 생긴다.

    그건 픽셀 문제라 로그로 못 찾는다 — 핸들러는 렌더를 직접 구현하지 않아야 한다.
    """
    source = _handler_source()
    assert "svc.run_render(" in source and "svc.run_upscale(" in source
    # 렌더 본체를 복제했다면 이 이름들이 핸들러 안에 있을 것이다.
    for leaked in ("QwenImageEditPlusPipeline", "load_lora_weights", "fuse_lora"):
        assert leaked not in source, f"핸들러가 렌더를 복제했다: {leaked}"


def test_the_handler_loads_the_pipeline_before_accepting_jobs():
    """적재가 start() 뒤로 가면 워커가 "준비됨"으로 잡을 받고 나서 적재를 시작한다.

    그 시간이 그대로 셀러 대기로 들어간다. 순서를 소스에서 고정한다.
    """
    # 본문의 **실제 문장**만 본다 — 독스트링이 같은 이름을 언급하므로 문자열 검색은 못 쓴다.
    tree = ast.parse(_handler_source())
    preload = next(
        node.lineno for node in tree.body
        if isinstance(node, ast.If) and "_preload()" in ast.unparse(node)
    )
    # ★ 모듈 독스트링도 ast.Expr 다 — 같은 이름을 설명하고 있어 그게 1번 줄로 잡힌다.
    #   실제 **호출**(Expr.value 가 Call)만 센다.
    start = next(
        node.lineno for node in tree.body
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
        and "runpod.serverless.start" in ast.unparse(node.value.func)
    )
    assert preload < start, "베이스 적재가 runpod.serverless.start() 보다 뒤에 있다"


def test_the_handler_points_hf_home_at_the_volume_before_importing_diffusers():
    """HF_HOME 을 임포트 뒤에 바꾸면 늦는다 — diffusers 가 이미 읽은 뒤다.

    파드에서 이 순서가 뚫려 53.8GiB 가 20GB 볼륨으로 향했고 Errno 122 로 죽었다.
    """
    source = _handler_source()
    assert 'os.environ["HF_HOME"]' in source, "HF_HOME 을 `:-` 가 아니라 덮어써야 한다"
    assert source.index('os.environ["HF_HOME"]') < source.index("import face_render_service")


def test_every_op_the_handler_advertises_has_a_function():
    """_OPS 표와 실제 함수가 갈리면 요청이 unknown op 으로 조용히 죽는다."""
    tree = ast.parse(_handler_source())
    defined = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    ops = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(getattr(t, "id", None) == "_OPS" for t in node.targets)
    )
    keys = {k.value for k in ops.value.keys}
    values = {v.id for v in ops.value.values}
    assert keys == {"render", "upscale", "healthz"}
    assert values <= defined


def test_render_and_upscale_have_an_auth_free_body_for_the_worker():
    """서버리스는 엔드포인트 앞에서 RunPod 이 막는다 — 그 안쪽에 두 번째 비밀을 두지 않는다."""
    import face_render_service as svc

    for name in ("run_render", "run_upscale"):
        fn = getattr(svc, name)
        params = inspect.signature(fn).parameters
        assert "authorization" not in params, f"{name} 은 인증 인자를 받지 않아야 한다"


def test_the_pod_routes_still_authorize():
    """본체를 갈라내면서 파드가 열리면 안 된다."""
    import face_render_service as svc

    for name in ("render", "upscale"):
        source = inspect.getsource(getattr(svc, name))
        assert "_authorize(authorization)" in source, f"{name} 라우트에서 인증이 사라졌다"


def test_the_image_does_not_bake_the_weights():
    """53.8GiB 를 이미지에 넣으면 CI 러너 디스크에 안 들어가고, 모델을 바꿀 때마다 다시 굽는다."""
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    for baked in ("hf download", "huggingface-cli download", "snapshot_download"):
        assert baked not in dockerfile, f"Dockerfile 이 가중치를 굽는다: {baked}"
    assert "runpod==" in dockerfile, "runpod SDK 를 고정해서 설치해야 워커가 뜬다"
    # 파드 bootstrap.sh 가 검증한 조합 그대로여야 한다 — 고정하지 않으면 아무 날에나 바뀐다.
    for pin in ("diffusers==0.40.0", "transformers==5.17.0", "huggingface_hub==1.31.0"):
        assert pin in dockerfile, f"검증된 핀이 빠졌다: {pin}"


@pytest.mark.parametrize("needed", ["face_render_service.py", "face_esrgan.py", "app"])
def test_the_image_carries_the_render_code(needed):
    """파드는 R2 묶음으로 코드를 받았다. 워커는 이미지에 구워 간다 — 빠지면 임포트에서 죽는다."""
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    assert needed in dockerfile
