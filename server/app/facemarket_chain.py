"""FaceMarket 온체인 정산 recorder (선택과제2 — OmniOne Chain, Free-Gas BESU).

record-only: 코인 이동 없음. recordSettlement(paymentId, modelRef, total) 로 70/20/10
분배를 온체인에 불변 기록하고, getSettlement 로 eth_call 확인한다. canonical 산식은
컨트랙트(FaceMarketSettlement.sol)에 있고, 백엔드는 반환값을 fm_settlements 에 미러한다.

Free-Gas BESU 제약: EIP-1559 거부 → **legacy tx (type 0), gasPrice 0**. 단일 owner 키가
유일한 recorder → nonce 충돌 방지 위해 **직렬화 큐(Lock)** 로 서명·전송을 한 번에 하나씩.

넷(rpc·address·private_key) 다 있어야 활성. 하나라도 없으면 from_settings 가 None 을
돌려 정산 훅이 no-op(FACEMARKET_ENABLED off 와 동일하게 기존 흐름 무영향).
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger("facemarket.chain")

# 필요한 두 함수만 임베드(런타임 파일 의존 제거). build/*.abi 와 동일 시그니처.
_ABI = [
    {
        "type": "function",
        "name": "recordSettlement",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "paymentId", "type": "bytes32"},
            {"name": "modelRef", "type": "bytes32"},
            {"name": "total", "type": "uint256"},
        ],
        "outputs": [
            {"name": "modelAmount", "type": "uint256"},
            {"name": "platformAmount", "type": "uint256"},
            {"name": "opsAmount", "type": "uint256"},
        ],
    },
    {
        "type": "function",
        "name": "getSettlement",
        "stateMutability": "view",
        "inputs": [{"name": "paymentId", "type": "bytes32"}],
        "outputs": [
            {"name": "modelRef", "type": "bytes32"},
            {"name": "total", "type": "uint256"},
            {"name": "modelAmount", "type": "uint256"},
            {"name": "platformAmount", "type": "uint256"},
            {"name": "opsAmount", "type": "uint256"},
            {"name": "blockNumber", "type": "uint256"},
            {"name": "exists", "type": "bool"},
        ],
    },
]

_GAS_LIMIT = 300_000  # recordSettlement 실측 여유(단일 mapping write + event)


class ChainDisabled(Exception):
    """체인 미설정/미가동 — 호출부가 정산을 조용히 건너뛸 신호."""


class FaceMarketChain:
    """단일 owner 키 온체인 recorder. web3.py 는 동기 → 호출부가 asyncio.to_thread 로 감쌀 것."""

    # OmniOne Chain 게이트웨이는 getTransactionReceipt/estimateGas/gasPrice 를 노출하지 않는다
    # (문서 = blockNumber·chainId·getBlock·getCode·getTransactionCount·call·sendRawTransaction·
    # getProof 만). 그래서 receipt 로 확인하지 않고 **우리 컨트랙트 getSettlement eth_call 폴링**으로
    # 확정하고(README 설계), tx 필드는 전부 명시해 estimate/gasPrice 호출을 피한다.
    _CONFIRM_TIMEOUT = 90.0
    _POLL_INTERVAL = 2.0

    def __init__(self, rpc_url: str, address: str, private_key: str, chain_id: int | None):
        import time as _time

        from web3 import Web3  # 지연 import — 미설정 배포에서 web3 부재해도 앱 기동

        self._Web3 = Web3
        self._time = _time
        self.w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 20}))
        # BESU IBFT extraData(>32B)는 web3 기본 블록 포매터를 깨뜨린다 → POA 미들웨어 주입.
        # 우리 경로(getTransactionCount·call·sendRawTransaction·chainId)는 블록 파싱을 안 하지만
        # 안전판으로 둔다(버전차 대비 best-effort).
        try:
            from web3.middleware import ExtraDataToPOAMiddleware
            self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        except Exception:
            pass
        self.account = self.w3.eth.account.from_key(private_key)
        self.address = Web3.to_checksum_address(address)
        self.contract = self.w3.eth.contract(address=self.address, abi=_ABI)
        # chain_id 미지정이면 1회 조회(Free-Gas BESU는 eth_chainId 지원).
        self.chain_id = int(chain_id) if chain_id else int(self.w3.eth.chain_id)
        self._nonce_lock = threading.Lock()

    @classmethod
    def from_settings(cls, settings) -> "FaceMarketChain | None":
        if not (
            settings.fm_chain_rpc_url
            and settings.fm_settlement_address
            and settings.fm_chain_private_key
        ):
            return None
        try:
            client = cls(
                settings.fm_chain_rpc_url,
                settings.fm_settlement_address,
                settings.fm_chain_private_key,
                settings.fm_chain_id,
            )
            logger.info(
                "facemarket_chain_ready",
                extra={"chain_id": client.chain_id, "address": client.address},
            )
            return client
        except Exception:
            logger.exception("facemarket_chain_init_failed")
            return None

    # ---- bytes32 helpers (paymentId/modelRef 는 keccak256 으로 고정폭) ----
    def keccak32(self, s: str) -> bytes:
        return self._Web3.keccak(text=s)

    def record_settlement(self, *, payment_key: str, model_uuid: str, total: int) -> dict:
        """온체인 기록 후 getSettlement 로 저장값을 되읽어 반환.

        payment_key = job id 기반 결정적 문자열(멱등 — 컨트랙트 중복 revert 와 쌍).
        반환: {tx_hash, block, chain_id, model_ref, model_amount, platform_amount, ops_amount}.
        중복 paymentId 는 컨트랙트가 revert → 이 함수도 예외(호출부가 이미 기록으로 처리).
        """
        if total <= 0:
            raise ValueError("total must be > 0")
        pid = self.keccak32(payment_key)
        mref = self.keccak32(model_uuid)

        with self._nonce_lock:  # 단일 키 nonce 직렬화(동시 정산 충돌 방지)
            # 각 tx 를 확정(_await_recorded)까지 블로킹 → 직전 tx 는 이미 채굴됨 → "latest" 정확.
            # (게이트웨이가 "pending" 태그를 보장 안 해 "latest" 채택.)
            nonce = self.w3.eth.get_transaction_count(self.account.address, "latest")
            tx = self.contract.functions.recordSettlement(pid, mref, int(total)).build_transaction(
                {
                    "from": self.account.address,
                    "nonce": nonce,
                    "gas": _GAS_LIMIT,
                    "gasPrice": 0,  # Free-Gas BESU: legacy type-0, 가스비 0
                    "chainId": self.chain_id,
                }
            )
            signed = self.account.sign_transaction(tx)
            raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction  # web3 v7/v6
            tx_hash = self.w3.eth.send_raw_transaction(raw)

        # receipt 대신 getSettlement eth_call 폴링으로 확정(게이트웨이 receipt 미지원).
        stored = self._await_recorded(payment_key)
        if stored is None:
            raise RuntimeError(f"recordSettlement not confirmed on-chain: {tx_hash.hex()}")
        return {
            "tx_hash": tx_hash.hex(),
            "block": stored["block"],
            "chain_id": self.chain_id,
            "model_ref": "0x" + mref.hex(),
            "model_amount": stored["model_amount"],
            "platform_amount": stored["platform_amount"],
            "ops_amount": stored["ops_amount"],
            "total": stored["total"],
        }

    def _await_recorded(self, payment_key: str) -> dict | None:
        """getSettlement eth_call 을 exists=True 될 때까지 폴링(receipt 대체). 타임아웃이면 None."""
        deadline = self._time.monotonic() + self._CONFIRM_TIMEOUT
        while self._time.monotonic() < deadline:
            try:
                stored = self.get_settlement(payment_key)
            except Exception:
                stored = {"exists": False}
            if stored.get("exists"):
                return stored
            self._time.sleep(self._POLL_INTERVAL)
        return None

    def get_settlement(self, payment_key: str) -> dict:
        """eth_call getter — 백엔드 확인 경로(영수증 UI 프록시). exists=False 면 미기록."""
        pid = self.keccak32(payment_key)
        (mref, total, model_a, plat_a, ops_a, block, exists) = self.contract.functions.getSettlement(
            pid
        ).call()
        return {
            "model_ref": "0x" + mref.hex(),
            "total": int(total),
            "model_amount": int(model_a),
            "platform_amount": int(plat_a),
            "ops_amount": int(ops_a),
            "block": int(block),
            "exists": bool(exists),
        }
