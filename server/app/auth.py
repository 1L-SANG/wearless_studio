"""Supabase JWT 검증 (backend_integration_plan §9).

모든 /v1 요청: Authorization: Bearer <jwt> → JWKS 서명 검증 → user_id(sub) 주입.
키 해석은 app.state.jwt_key_resolver 에 두어 테스트에서 교체 가능하게 한다.
"""

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jwt import PyJWKClient

ALLOWED_ALGORITHMS = ["ES256", "RS256"]

security_scheme = HTTPBearer(auto_error=False)


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail={"code": "unauthorized", "message": "로그인이 필요합니다."},
    )


def jwks_key_resolver(jwks_url: str):
    """JWKS 엔드포인트에서 토큰 kid에 맞는 공개키를 찾는 기본 resolver."""
    client = PyJWKClient(jwks_url, cache_keys=True)

    def resolve(token: str):
        return client.get_signing_key_from_jwt(token).key

    return resolve


def require_user(
    request: Request,
    token: HTTPAuthorizationCredentials | None = Depends(security_scheme),
) -> str:
    """인증된 user_id를 반환한다. 실패 시 401 봉투."""
    settings = request.app.state.settings

    if token is None or not token.credentials:
        raise _unauthorized()
    token_str = token.credentials.strip()

    resolver = request.app.state.jwt_key_resolver
    if resolver is None:
        raise HTTPException(
            status_code=500,
            detail={"code": "auth_not_configured", "message": "서버 인증 설정이 없습니다."},
        )

    try:
        key = resolver(token_str)
        claims = jwt.decode(
            token_str,
            key,
            algorithms=ALLOWED_ALGORITHMS,
            audience=settings.jwt_audience,
            options={"require": ["exp", "sub"]},
        )
    except HTTPException:
        raise
    except Exception:
        raise _unauthorized() from None

    return claims["sub"]
