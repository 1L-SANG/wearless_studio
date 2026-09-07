"""가입 출처 판정 — 이 요청이 셀러에서 왔는가, FaceMarket 에서 왔는가.

DB 도 요청 객체도 모르는 순수 함수만 둔다. 판정 규칙이 라우트 안에 숨어 있으면
"왜 이 계정이 seller 로 찍혔나" 를 되짚을 때 읽을 곳이 없다.

**신뢰 순서: Origin 헤더 > 본문.**
본문의 app 은 클라이언트가 쓰는 값이라 위조된다 — FaceMarket 탭에서 {"app":"seller"} 를
보낼 수 있다. Origin 은 브라우저가 붙이고 스크립트가 못 고친다. 그래서 헤더가 쓸 만하면
본문은 아예 안 본다. 본문은 헤더로 판정이 안 될 때(로컬 개발)만 쓰는 폴백이다.

이건 **권한이 아니라 라벨**이다. 출처를 속여도 얻는 것은 콘솔 배지 한 칸이 전부다 —
여기에 접근 제어를 걸지 마라(그건 require_user·admin_guard 의 일이다).
"""

SELLER = "seller"
FACEMARKET = "facemarket"
BOTH = "both"

# 사용자가 "가입한" 앱으로 기록될 수 있는 값. 'both' 는 서버가 승격시켜 만드는 값이라
# 입력으로는 받지 않는다.
APPS = (SELLER, FACEMARKET)

# 콘솔·미상까지 포함한, 저장될 수 있는 전체 값(마이그레이션 CHECK 와 같은 집합).
STORED = (SELLER, FACEMARKET, BOTH)


def _host(origin: str | None) -> str:
    """Origin 헤더에서 호스트만 소문자로. 포트와 IPv6 대괄호는 버린다."""
    raw = (origin or "").strip().lower()
    if not raw:
        return ""
    # scheme://host[:port] — scheme 이 없어도(테스트가 호스트만 넘겨도) 동작하게.
    host = raw.split("://", 1)[-1].split("/", 1)[0]
    if host.startswith("["):  # [::1]:5173 — IPv6 리터럴
        return host[1:].split("]", 1)[0]
    return host.split(":", 1)[0]


def app_from_origin(origin: str | None) -> str | None:
    """Origin 헤더 → 'seller' | 'facemarket' | None(판정 불가).

    판정 불가가 셋이다 — 셋 다 '아니오' 가 아니라 '모르겠다' 라서 None 이다:
      - 헤더 없음: 브라우저가 아닌 호출(curl·서버 간).
      - 관리자 콘솔: admin.wearless.kr 은 **출처가 아니다**. 관리자가 콘솔을 연 것을
        가입 출처로 찍으면 그 계정의 진짜 출처가 덮인다.
      - localhost: 로컬 dev 서버는 세 문서를 한 오리진에서 배급한다(vite.config.js).
        호스트로는 구분이 안 되니 본문 폴백에 넘긴다.
    """
    host = _host(origin)
    if not host:
        return None
    if host in ("localhost", "127.0.0.1", "::1") or host.endswith(".localhost"):
        return None
    if host.startswith("admin."):
        return None
    if "facemarket" in host:
        return FACEMARKET
    return SELLER


def resolve_app(origin: str | None, body_app: str | None) -> str | None:
    """이번 요청의 출처. 헤더가 말해 주면 본문은 무시한다."""
    from_header = app_from_origin(origin)
    if from_header is not None:
        return from_header
    candidate = (body_app or "").strip().lower()
    return candidate if candidate in APPS else None


def merge(current: str | None, incoming: str) -> str:
    """저장된 출처와 이번 출처를 합친다 — 첫 값은 보존, 다른 앱이 오면 'both'.

    첫 값을 보존하는 이유: 'app_origin' 이 답해야 하는 질문은 "이 사람은 어디서
    들어왔나" 이지 "마지막으로 어디를 썼나" 가 아니다. 후자는 세션 로그가 답할 일이다.
    """
    if current is None:
        return incoming
    if current == incoming:
        return current
    return BOTH
