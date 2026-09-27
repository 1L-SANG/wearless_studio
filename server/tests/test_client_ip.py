"""Cloudflare 프록시 뒤에서의 클라이언트 IP — IP 기반 한도가 엉뚱한 사람들을 묶지 않게.

api.wearless.kr 를 Cloudflare 프록시(오렌지 클라우드) 뒤로 옮기면 ALB 가 보는 접속자는
방문자가 아니라 Cloudflare 엣지다. XFF 마지막 값만 쓰면 같은 엣지를 타는 방문자 전원이
공개 분석 한도(시간당 10회)를 나눠 쓴다. 그렇다고 CF-Connecting-IP 를 무조건 믿으면
ALB 로 직접 붙은 요청이 헤더를 꾸며 한도를 우회한다 — 그래서 **ALB 가 관측한 접속자가
Cloudflare 대역일 때만** 그 헤더를 쓴다.
"""
import pathlib

import pytest
import yaml
from starlette.requests import Request

from app.client_ip import CLOUDFLARE_IPV4_RANGES, client_ip

ENV_MANIFEST = (
    pathlib.Path(__file__).resolve().parents[2] / "copilot/environments/use1/manifest.yml"
)


def _request(headers: dict[str, str], peer: str = "10.0.1.9") -> Request:
    return Request({
        "type": "http",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": (peer, 43210),
    })


@pytest.mark.parametrize(("headers", "want"), [
    # Cloudflare 엣지(172.64.0.0/13)가 붙인 방문자 IP 를 쓴다.
    ({"x-forwarded-for": "198.51.100.10, 172.64.1.2", "cf-connecting-ip": "203.0.113.7"},
     "203.0.113.7"),
    # 대역 마지막 주소도 엣지다.
    ({"x-forwarded-for": "172.71.255.255", "cf-connecting-ip": "203.0.113.8"}, "203.0.113.8"),
    # 대역 바로 밖은 엣지가 아니다 — 헤더를 무시하고 ALB 관측값을 쓴다.
    ({"x-forwarded-for": "172.72.0.0", "cf-connecting-ip": "203.0.113.9"}, "172.72.0.0"),
    # ALB 에 직접 붙어 CF-Connecting-IP 를 꾸민 요청.
    ({"x-forwarded-for": "198.51.100.10, 203.0.113.25", "cf-connecting-ip": "192.0.2.99"},
     "203.0.113.25"),
    # 엣지인데 헤더가 없거나 깨졌으면 엣지 IP 로 좁혀 묶는다(한도가 느슨해지는 쪽이 아니다).
    ({"x-forwarded-for": "172.64.1.2"}, "172.64.1.2"),
    ({"x-forwarded-for": "172.64.1.2", "cf-connecting-ip": "not-an-ip"}, "172.64.1.2"),
    # IPv6 방문자.
    ({"x-forwarded-for": "104.16.0.1", "cf-connecting-ip": "2001:db8::1"}, "2001:db8::1"),
])
def test_client_ip_trusts_cf_connecting_ip_only_from_cloudflare_edge(headers, want):
    assert client_ip(_request(headers)) == want


def test_client_ip_falls_back_to_socket_peer_without_forwarded_header():
    assert client_ip(_request({}, peer="10.0.1.9")) == "10.0.1.9"


def test_alb_security_group_admits_exactly_the_ranges_trusted_as_cloudflare():
    """SG 허용 대역과 코드의 엣지 판정 대역이 어긋나면 두 방향으로 깨진다.

    SG 에만 있는 대역 → 그 엣지를 타는 방문자 전원이 엣지 IP 하나로 묶여 429.
    코드에만 있는 대역 → SG 가 막아 도달하지 않으니 무해하지만, 목록이 낡았다는 신호다.
    ALB 는 IPv4 전용(ipAddressType=ipv4)이라 SG 에는 IPv4 대역만 둔다.

    SG 잠금(런북 4단계)은 프록시를 켜고 한참 뒤에 한다 — 그 전엔 매니페스트에
    `ingress` 가 없고(=0.0.0.0/0) 이 검사는 건너뛴다. 잠그는 순간부터 두 목록이 묶인다.
    """
    manifest = yaml.safe_load(ENV_MANIFEST.read_text())
    ingress = manifest["http"]["public"].get("ingress")
    if ingress is None:
        pytest.skip("ALB SG 아직 안 잠금 — 런북 docs/runbooks/cloudflare-origin-lock.md 4단계")
    source_ips = ingress["source_ips"]

    assert "0.0.0.0/0" not in source_ips
    assert sorted(source_ips) == sorted(CLOUDFLARE_IPV4_RANGES)
