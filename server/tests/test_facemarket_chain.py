import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from app.facemarket_chain import FaceMarketChain


class _TxHash:
    def __init__(self, value):
        self.value = value

    def hex(self):
        return self.value


class _Eth:
    def __init__(self, state):
        self.state = state
        self.account = SimpleNamespace(
            from_key=lambda _key: None,
        )

    def get_transaction_count(self, _address, _tag):
        return 7 + self.state["confirmed"]

    def send_raw_transaction(self, raw):
        self.state["sent_nonces"].append(raw["nonce"])
        return _TxHash(f"0x{raw['nonce']:064x}")


class _RecordCall:
    def __init__(self, pid, model_ref, total):
        self.pid = pid
        self.model_ref = model_ref
        self.total = total

    def build_transaction(self, tx):
        return {**tx, "pid": self.pid, "model_ref": self.model_ref, "total": self.total}


def _chain():
    state = {"confirmed": 0, "sent_nonces": []}
    chain = FaceMarketChain.__new__(FaceMarketChain)
    chain._Web3 = SimpleNamespace(keccak=lambda text: hashlib.sha256(text.encode()).digest())
    chain.w3 = SimpleNamespace(eth=_Eth(state))
    chain.account = SimpleNamespace(
        address="0xowner",
        sign_transaction=lambda tx: SimpleNamespace(raw_transaction=tx),
    )
    chain.contract = SimpleNamespace(functions=SimpleNamespace(
        recordSettlement=lambda pid, model_ref, total: _RecordCall(pid, model_ref, total)
    ))
    chain.chain_id = 1337
    chain._nonce_lock = threading.Lock()

    def wait_for_settlement(_payment_key):
        time.sleep(0.05)
        state["confirmed"] += 1
        return {
            "model_ref": "0x" + "ab" * 32,
            "total": 10000,
            "model_amount": 7000,
            "platform_amount": 2000,
            "ops_amount": 1000,
            "block": state["confirmed"],
            "exists": True,
        }

    chain.wait_for_settlement = wait_for_settlement
    return chain, state


def test_distinct_concurrent_settlements_hold_nonce_lock_until_confirmation():
    chain, state = _chain()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(
            lambda key: chain.record_settlement(
                payment_key=key, model_uuid="model-1", total=10000
            ),
            ("payment-1", "payment-2"),
        ))

    assert state["sent_nonces"] == [7, 8]
    assert [result["block"] for result in results] == [1, 2]
