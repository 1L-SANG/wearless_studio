"""층③ 앵커 — 같은 owner 키가 두 컨트랙트를 쓴다. nonce lock 공유가 계약이다.

FaceMarketChain 을 두 인스턴스로 쪼개면 각자 get_transaction_count("latest") 를 읽어
같은 nonce 로 서명한다. 그래서 provenance 는 별도 클래스가 아니라 같은 인스턴스의
메서드여야 한다. 이 테스트가 그 구조를 못박는다.
"""
import threading

from app.facemarket_chain import FaceMarketChain


def test_provenance_methods_live_on_the_same_class():
    for name in ("record_publication", "get_publication", "wait_for_publication"):
        assert hasattr(FaceMarketChain, name), f"{name} must be a FaceMarketChain method"


def test_nonce_lock_is_shared_between_settlement_and_publication():
    """두 경로가 같은 lock 객체를 쓴다 = 서명이 직렬화된다."""
    import inspect

    settle_src = inspect.getsource(FaceMarketChain.record_settlement)
    pub_src = inspect.getsource(FaceMarketChain.record_publication)
    assert "self._nonce_lock" in settle_src
    assert "self._nonce_lock" in pub_src


def test_from_settings_leaves_provenance_disabled_without_address():
    class S:
        fm_chain_rpc_url = None
        fm_settlement_address = None
        fm_chain_private_key = None
        fm_chain_id = None
        fm_provenance_address = None

    assert FaceMarketChain.from_settings(S()) is None
