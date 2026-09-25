"""요청의 실제 방문자 IP — ALB 직결, 그리고 Cloudflare 프록시 → ALB 경로 둘 다.

ALB 는 들어온 X-Forwarded-For 뒤에 자기가 관측한 접속자 IP 를 append 한다. 앞쪽 값은
클라이언트가 꾸밀 수 있지만 마지막 값은 ALB 가 쓴 것이라 믿을 수 있다.

api.wearless.kr 가 Cloudflare 프록시 뒤에 있으면 그 마지막 값은 방문자가 아니라
Cloudflare 엣지다. 그때만 Cloudflare 가 덮어쓴 CF-Connecting-IP 를 방문자로 쓴다.
엣지가 아닌 접속자가 보낸 CF-Connecting-IP 는 꾸민 값일 수 있으니 무시한다.

대역 출처는 https://www.cloudflare.com/ips-v4 · ips-v6 (2026-09-15 확인). IPv4 목록은
ALB 보안그룹(copilot/environments/use1/manifest.yml 의 http.public.ingress.source_ips)과
같아야 한다 — tests/test_client_ip.py 가 잠근다.
"""
from ipaddress import IPv4Address, IPv6Address, ip_address, ip_network

from starlette.requests import Request

CLOUDFLARE_IPV4_RANGES = (
    "173.245.48.0/20",
    "103.21.244.0/22",
    "103.22.200.0/22",
    "103.31.4.0/22",
    "141.101.64.0/18",
    "108.162.192.0/18",
    "190.93.240.0/20",
    "188.114.96.0/20",
    "197.234.240.0/22",
    "198.41.128.0/17",
    "162.158.0.0/15",
    "104.16.0.0/13",
    "104.24.0.0/14",
    "172.64.0.0/13",
    "131.0.72.0/22",
)

CLOUDFLARE_IPV6_RANGES = (
    "2400:cb00::/32",
    "2606:4700::/32",
    "2803:f800::/32",
    "2405:b500::/32",
    "2405:8100::/32",
    "2a06:98c0::/29",
    "2c0f:f248::/32",
)

_CLOUDFLARE_NETWORKS = tuple(
    ip_network(cidr) for cidr in CLOUDFLARE_IPV4_RANGES + CLOUDFLARE_IPV6_RANGES
)


def _parse_ip(value: str) -> IPv4Address | IPv6Address | None:
    try:
        return ip_address(value.strip())
    except ValueError:
        return None


def client_ip(request: Request) -> str:
    """방문자 IP. 헤더가 없거나 형식이 틀리면 ASGI peer 로 안전 폴백한다."""
    forwarded = request.headers.get("x-forwarded-for", "")
    peer = _parse_ip(forwarded.rsplit(",", 1)[-1]) if forwarded else None
    if peer is None:
        return request.client.host if request.client else "unknown"
    if any(peer in network for network in _CLOUDFLARE_NETWORKS):
        visitor = _parse_ip(request.headers.get("cf-connecting-ip", ""))
        if visitor is not None:
            return str(visitor)
    return str(peer)
