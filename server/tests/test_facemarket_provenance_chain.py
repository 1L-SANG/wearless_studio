"""층③ 앵커 — 같은 owner 키가 두 컨트랙트를 쓴다. nonce lock 공유가 계약이다.

FaceMarketChain 을 두 인스턴스로 쪼개면 각자 get_transaction_count("latest") 를 읽어
같은 nonce 로 서명한다. 그래서 provenance 는 별도 클래스가 아니라 같은 인스턴스의
메서드여야 한다. 이 테스트가 그 구조를 못박는다.

리뷰 라운드 1 로 추가된 두 가지:
  - from_settings 는 provenance 주소가 망가져도 settlement client 자체는 살려야 한다
    (선택 기능의 설정 오류가 이미 실서비스 중인 정산 recorder 를 죽이면 안 된다).
  - "self._nonce_lock" 문자열 존재만으로는 lock 이 confirmation 까지 실제로 잡혀
    있는지 못 잡는다(누가 wait_for_publication 호출을 with 밖으로 빼도 통과함). 그래서
    record_settlement 와 record_publication 을 같은 인스턴스에서 동시에 돌려
    nonce 가 겹치지 않는지 실제로 검증하는 테스트를 추가한다.
"""
import hashlib
import inspect
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from app.facemarket_chain import FaceMarketChain


def test_provenance_methods_live_on_the_same_class():
    for name in ("record_publication", "get_publication", "wait_for_publication"):
        assert hasattr(FaceMarketChain, name), f"{name} must be a FaceMarketChain method"


def test_nonce_lock_is_shared_between_settlement_and_publication():
    """두 경로가 같은 lock 객체를 쓴다 = 서명이 직렬화된다.

    이 테스트는 소스에 문자열이 있는지만 본다 — 값싸고 다른(더 조야한) 회귀를 잡지만,
    lock 이 confirmation 까지 실제로 유지되는지는 증명 못한다. 그건 아래
    test_interleaved_settlement_and_publication_hold_nonce_lock_until_confirmation 가 한다.
    """
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


def test_from_settings_survives_malformed_provenance_address():
    """provenance 주소 오타(hex 한 자리 짧음)가 이미 살아있는 settlement client 를 죽이면 안 된다.

    Critical 리뷰: attach_provenance 의 to_checksum_address 가 raise 하면 그게 client
    생성 전체를 감싸는 try 안에서 터져 from_settings 가 None 을 돌려줬다 — 정산 recorder
    까지 함께 무음으로 꺼진다. 고친 뒤에는: client 는 살아있고, provenance 만 꺼진 채여야 한다.
    """
    import app.facemarket_chain as chain_mod

    def _fake_to_checksum_address(addr):
        # 정상 주소(정산)는 통과, provenance 주소(한 자리 짧은 hex)는 raise —
        # 실제 web3.Web3.to_checksum_address 가 잘못된 길이/checksum 에 raise 하는 것과 동일.
        if len(addr) != 42:
            raise ValueError(f"invalid address length: {addr!r}")
        return addr

    class _FakeAccount:
        address = "0xowner"

    class _FakeEth:
        chain_id = 1337

        def __init__(self):
            self.account = SimpleNamespace(from_key=lambda _k: _FakeAccount())

        def contract(self, address, abi):
            return SimpleNamespace(address=address, abi=abi)

    class _FakeW3:
        def __init__(self, *_a, **_k):
            self.eth = _FakeEth()
            self.middleware_onion = SimpleNamespace(inject=lambda *a, **k: None)

    class PatchedChain(FaceMarketChain):
        def __init__(self, rpc_url, address, private_key, chain_id):
            self._Web3 = SimpleNamespace(
                to_checksum_address=_fake_to_checksum_address,
                keccak=lambda text: hashlib.sha256(text.encode()).digest(),
            )
            self._time = time
            self.w3 = _FakeW3()
            self.account = self.w3.eth.account.from_key(private_key)
            self.address = self._Web3.to_checksum_address(address)
            self.contract = self.w3.eth.contract(address=self.address, abi=chain_mod._ABI)
            self.chain_id = int(chain_id) if chain_id else int(self.w3.eth.chain_id)
            self._nonce_lock = threading.Lock()
            self.provenance = None
            self.provenance_enabled = False

    class S:
        fm_chain_rpc_url = "http://fake-rpc"
        fm_settlement_address = "0x" + "11" * 20  # 42자, 정상
        fm_chain_private_key = "0x" + "22" * 32
        fm_chain_id = 1337
        fm_provenance_address = "0x" + "33" * 19  # 40자 — 한 자리 짧음(오타 시나리오)

    client = PatchedChain.from_settings(S())

    assert client is not None, (
        "provenance 주소 오타가 settlement client 전체를 죽였다 — "
        "이미 실서비스 정산을 기록 중인 recorder 가 무음으로 꺼진다"
    )
    assert client.provenance_enabled is False
    assert client.provenance is None
    # settlement 경로는 멀쩡히 살아있어야 한다(오탐 방지 — 진짜 회귀는 client 가 None).
    assert client.address == "0x" + "11" * 20


class _TxHash:
    def __init__(self, value):
        self.value = value

    def hex(self):
        return self.value


class _Eth:
    """정산(recordSettlement)·배포본(recordPublication) 양쪽을 다 흉내내는 fake eth.

    get_transaction_count 는 실제 체인처럼 confirmed 카운트에 의존한다 — lock 이
    confirmation 전에 풀리면 두 스레드가 같은 nonce 를 읽게 되어 테스트가 잡아낸다.
    """

    def __init__(self, state):
        self.state = state
        self.account = SimpleNamespace(from_key=lambda _key: None)

    def get_transaction_count(self, _address, _tag):
        return 7 + self.state["confirmed"]

    def send_raw_transaction(self, raw):
        self.state["sent_nonces"].append(raw["nonce"])
        return _TxHash(f"0x{raw['nonce']:064x}")


class _Call:
    def __init__(self, **fields):
        self.fields = fields

    def build_transaction(self, tx):
        return {**tx, **self.fields}


def _chain_with_settlement_and_publication():
    state = {"confirmed": 0, "sent_nonces": []}
    chain = FaceMarketChain.__new__(FaceMarketChain)
    chain._Web3 = SimpleNamespace(keccak=lambda text: hashlib.sha256(text.encode()).digest())
    chain.w3 = SimpleNamespace(eth=_Eth(state))
    chain.account = SimpleNamespace(
        address="0xowner",
        sign_transaction=lambda tx: SimpleNamespace(raw_transaction=tx),
    )
    chain.contract = SimpleNamespace(functions=SimpleNamespace(
        recordSettlement=lambda pid, model_ref, total: _Call(
            pid=pid, model_ref=model_ref, total=total
        )
    ))
    chain.provenance = SimpleNamespace(functions=SimpleNamespace(
        recordPublication=lambda pid, img, lref: _Call(pid=pid, img=img, lref=lref)
    ))
    chain.chain_id = 1337
    chain._nonce_lock = threading.Lock()
    chain._time = time
    chain._CONFIRM_TIMEOUT = 5.0
    chain._POLL_INTERVAL = 0.01

    def wait_for_settlement(_payment_key, timeout=None):
        time.sleep(0.03)
        state["confirmed"] += 1
        return {
            "model_ref": "0x" + "ab" * 32, "total": 10000, "model_amount": 7000,
            "platform_amount": 2000, "ops_amount": 1000,
            "block": state["confirmed"], "exists": True,
        }

    def wait_for_publication(_publication_id, timeout=None):
        time.sleep(0.03)
        state["confirmed"] += 1
        return {
            "image_hash": "cd" * 32, "license_ref": "0x" + "ef" * 32,
            "block": state["confirmed"], "exists": True,
        }

    chain.wait_for_settlement = wait_for_settlement
    chain.wait_for_publication = wait_for_publication
    return chain, state


def test_interleaved_settlement_and_publication_hold_nonce_lock_until_confirmation():
    """진짜 프로덕션 모양: 단일 owner 키, 두 recorder(정산·앵커)가 같은 인스턴스에서 경합한다.

    record_settlement 하나만 동시에 도는 test_facemarket_chain.py 의 테스트로는
    settlement 과 publication 이 서로의 nonce 를 밟는 회귀를 못 잡는다 — 이 테스트가 그 자리다.
    """
    chain, state = _chain_with_settlement_and_publication()

    jobs = [
        ("settle", "payment-1"),
        ("publish", "pub-1"),
        ("settle", "payment-2"),
        ("publish", "pub-2"),
    ]

    def run(job):
        kind, key = job
        if kind == "settle":
            return chain.record_settlement(payment_key=key, model_uuid="model-1", total=10000)
        return chain.record_publication(
            publication_id=key, image_sha256="ab" * 32, license_id="license-1"
        )

    with ThreadPoolExecutor(max_workers=len(jobs)) as executor:
        list(executor.map(run, jobs))

    nonces = state["sent_nonces"]
    assert nonces == list(range(7, 7 + len(jobs))), (
        f"nonce collision/gap between settlement and publication paths: {nonces}"
    )
    assert len(set(nonces)) == len(nonces), "duplicate nonce across settle/publish — lock not shared"
