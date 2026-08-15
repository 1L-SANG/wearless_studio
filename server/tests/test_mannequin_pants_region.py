"""매칭 하의 보존 — 편집 전후 로컬 픽셀 비교(compare_pants_region).

AI 아님·API 비용 0. 합성 마네킹 컷으로 세 지표(색·폭·구조)가 각각 발화하는지, 그리고
주름·미세광택·허리경계 변화 같은 정상 변동엔 발화 안 하는지 증명한다.
"""

from io import BytesIO

from PIL import Image, ImageDraw

from app.services import qc


def _figure(*, pants_color=(28, 28, 92), pants_x=(70, 130), size=(200, 300),
            noise=0, waist_only=False, extras=None) -> bytes:
    """흰 배경 위 상의(상단)+바지(하단 밴드) 합성 컷. band_top=0.60 → y≈180 부터가 바지."""
    w, h = size
    img = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle([65, 60, 135, 170], fill=(150, 60, 60))       # 상의 — 밴드 위(y<180)
    if waist_only:
        # 허리 경계(밴드 상단 바로 위)만 바꾼다 — 바지 판정으로 새면 안 된다.
        d.rectangle([65, 150, 135, 178], fill=(20, 120, 20))
    d.rectangle([pants_x[0], 182, pants_x[1], 288], fill=pants_color)  # 바지 — 밴드 안
    if noise:
        # 주름·미세광택 흉내 — 바지 위에 옅은 하이라이트 점들. 색 계열·폭·구조는 그대로.
        for i in range(40):
            x = pants_x[0] + 3 + (i * 7) % max(1, pants_x[1] - pants_x[0] - 6)
            y = 185 + (i * 11) % 100
            d.point((x, y), fill=(min(255, pants_color[0] + noise),
                                  min(255, pants_color[1] + noise),
                                  min(255, pants_color[2] + noise)))
    if extras:
        extras(d)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _m(result):
    return f"{result.verdict} {result.reasons} {result.metrics}"


def test_identical_is_stable():
    b = _figure()
    r = qc.compare_pants_region(b, b)
    assert r.verdict == "pants_stable", _m(r)
    assert r.reasons == []


def test_colour_family_shift_regresses():
    before = _figure(pants_color=(28, 28, 92))            # 네이비
    after = _figure(pants_color=(28, 110, 28))            # 그린 — 색 계열 변화
    r = qc.compare_pants_region(before, after)
    assert r.verdict == "pants_regressed", _m(r)
    assert "matching_bottom_colour_shift" in r.reasons, _m(r)


def test_leg_width_shift_regresses():
    before = _figure(pants_x=(65, 135))                   # 와이드
    after = _figure(pants_x=(92, 108))                    # 슬림 — 같은 색
    r = qc.compare_pants_region(before, after)
    assert r.verdict == "pants_regressed", _m(r)
    assert "matching_bottom_width_shift" in r.reasons, _m(r)


def _dense_structure(d):
    """포켓·허리단·플라이 대량 = gross 구조 변화. 세로/가로 라인 격자로 엣지 신호를 키운다."""
    for x in range(70, 131, 6):
        d.line([x, 184, x, 286], fill=(0, 0, 0), width=2)
    for y in range(190, 286, 10):
        d.line([70, y, 130, y], fill=(0, 0, 0), width=2)


def test_gross_structure_change_regresses_at_default_threshold():
    """굵은 구조 변화는 **출고 기본 임계(PANTS_EDGE_DELTA_MAX)** 에서 엣지 지표가 발화한다.

    엣지 게이트가 죽은 임계가 아님을 보장한다 — 기본값이 잘못 커지면 이 테스트가 잡는다.
    절대 스케일은 실사진 캘리브 대상이지만, 기본값은 살아 있는 게이트여야 한다.
    """
    r = qc.compare_pants_region(_figure(), _figure(extras=_dense_structure))
    assert r.verdict == "pants_regressed", _m(r)
    assert "matching_bottom_structure_shift" in r.reasons, _m(r)
    assert r.metrics["edgeDelta"] > qc.PANTS_EDGE_DELTA_MAX, _m(r)


def test_structure_signal_separates_from_wrinkle_noise():
    """주름 노이즈는 엣지 지표를 기본 임계 밑에 둔다 — 구조 변화와 뚜렷이 분리."""
    before = _figure()
    struct = qc.compare_pants_region(before, _figure(extras=_dense_structure))
    wrinkle = qc.compare_pants_region(before, _figure(noise=45))
    assert struct.metrics["edgeDelta"] > wrinkle.metrics["edgeDelta"] * 3, \
        f"structure={struct.metrics} wrinkle={wrinkle.metrics}"
    assert "matching_bottom_structure_shift" not in wrinkle.reasons, _m(wrinkle)


def test_width_metric_ignores_stray_edge_pixels():
    """재렌더 아티팩트 한두 점(밴드 프레임 가장자리)이 폭을 부풀려 오탐하지 않는다.

    getbbox 는 최외곽 단일 픽셀에 좌우된다 — MinFilter 침식이 없으면 stray 픽셀 하나가
    width_delta 를 폭발시켜 멀쩡한 편집을 되돌린다(리뷰 지적). 침식 후엔 무시돼야 한다.
    """
    def stray(d):
        d.point((2, 182), fill=(0, 0, 0))
        d.point((3, 182), fill=(0, 0, 0))
        d.point((197, 184), fill=(0, 0, 0))
    r = qc.compare_pants_region(_figure(), _figure(extras=stray))
    assert r.metrics["widthDelta"] == 0.0, _m(r)
    assert r.verdict == "pants_stable", _m(r)


def test_wrinkle_and_sheen_noise_is_stable():
    """주름·미세광택은 세 지표 어디에도 크게 안 걸린다(요구 3 — 오탐 방지)."""
    before = _figure()
    after = _figure(noise=45)
    r = qc.compare_pants_region(before, after)
    assert r.verdict == "pants_stable", _m(r)


def test_waist_boundary_change_is_tolerated():
    """허리 경계(밴드 상단 위) 변화는 바지 판정으로 새지 않는다 — untuck 정상 동작 보호."""
    before = _figure()
    after = _figure(waist_only=True)
    r = qc.compare_pants_region(before, after)
    assert r.verdict == "pants_stable", _m(r)


def test_size_mismatch_is_resized_not_crashed():
    before = _figure(size=(200, 300))
    big = Image.open(BytesIO(before)).resize((400, 600), Image.LANCZOS)  # 같은 내용 2x
    buf = BytesIO(); big.save(buf, format="PNG")
    r = qc.compare_pants_region(before, buf.getvalue())
    assert r.verdict == "pants_stable", _m(r)


def test_decode_failure_is_unknown_fail_open():
    r = qc.compare_pants_region(b"not-an-image", _figure())
    assert r.verdict == "pants_unknown"
    assert "decode_failed" in r.reasons
