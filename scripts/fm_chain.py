#!/usr/bin/env python3
"""FaceMarketSettlement 배포/스모크 — OmniOne Chain (Free-Gas BESU, 선택과제2).

컨트랙트 실배포 + 실 TX + eth_call 확인을 한 파일로 증명한다(장면④ 증거). web3 만 의존
(server/.venv 에 설치됨) → `cd server && uv run python ../scripts/fm_chain.py <cmd>`.

env (server/.env.local 자동 로드 또는 셸 export):
  FM_CHAIN_RPC_URL      필수 — 콘솔 RPC 엔드포인트
  FM_CHAIN_PRIVATE_KEY  필수(deploy·smoke) — 배포/owner 개인키(0x…). 절대 커밋 금지
  FM_CHAIN_ID           선택 — 없으면 eth_chainId 조회
  FM_SETTLEMENT_ADDRESS smoke·confirm 필수 — 배포된 컨트랙트 주소

명령:
  deploy   build/*.bin+*.abi 로 배포 → 주소·chainId 출력(→ FM_SETTLEMENT_ADDRESS 에 넣기)
  smoke    임의 paymentId 로 recordSettlement 실 TX → getSettlement 로 되읽어 분배 검증
  confirm PAYMENT_KEY   getSettlement eth_call 만(읽기 전용)
"""

import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
ABI_PATH = BUILD / "contracts_FaceMarketSettlement_sol_FaceMarketSettlement.abi"
BIN_PATH = BUILD / "contracts_FaceMarketSettlement_sol_FaceMarketSettlement.bin"


def _load_dotenv():
    """server/.env.local → os.environ (기존값 우선). dotenv 의존 없이 최소 파싱."""
    for env_file in (ROOT / "server" / ".env.local", ROOT / "server" / ".env"):
        if not env_file.exists():
            continue
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            os.environ.setdefault(k, v)


def _w3_account():
    from web3 import Web3

    rpc = os.environ.get("FM_CHAIN_RPC_URL")
    pk = os.environ.get("FM_CHAIN_PRIVATE_KEY")
    if not rpc:
        sys.exit("FM_CHAIN_RPC_URL 필요")
    if not pk:
        sys.exit("FM_CHAIN_PRIVATE_KEY 필요")
    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 30}))
    try:  # BESU IBFT extraData 대응(블록 파싱 안전판)
        from web3.middleware import ExtraDataToPOAMiddleware
        w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    except Exception:
        pass
    acct = w3.eth.account.from_key(pk)
    chain_id = int(os.environ.get("FM_CHAIN_ID") or w3.eth.chain_id)
    return w3, acct, chain_id


def _await_code(w3, address, timeout=120):
    """getCode 가 비지 않을 때까지 폴링(receipt 미지원 게이트웨이 — 배포 확인)."""
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        code = w3.eth.get_code(w3.to_checksum_address(address))
        if code and code != b"" and code.hex() not in ("", "0x"):
            return True
        time.sleep(2)
    return False


def _create_address(w3, sender, nonce):
    """CREATE 컨트랙트 주소 로컬 계산 = keccak(rlp([sender, nonce]))[12:] (receipt 불필요)."""
    import rlp
    from eth_utils import keccak, to_checksum_address
    raw = keccak(rlp.encode([bytes.fromhex(sender[2:]), nonce]))[12:]
    return to_checksum_address(raw)


def cmd_deploy():
    from web3 import Web3

    if not (ABI_PATH.exists() and BIN_PATH.exists()):
        sys.exit(f"build 아티팩트 없음 — 먼저 컴파일:\n  npx --yes solc@0.8.27 --bin --abi "
                 f"contracts/FaceMarketSettlement.sol -o build/")
    w3, acct, chain_id = _w3_account()
    abi = __import__("json").loads(ABI_PATH.read_text())
    bytecode = "0x" + BIN_PATH.read_text().strip()
    Contract = w3.eth.contract(abi=abi, bytecode=bytecode)
    nonce = w3.eth.get_transaction_count(acct.address, "latest")
    tx = Contract.constructor().build_transaction(
        {"from": acct.address, "nonce": nonce, "gas": 1_500_000, "gasPrice": 0, "chainId": chain_id}
    )
    signed = acct.sign_transaction(tx)
    raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
    h = w3.eth.send_raw_transaction(raw)
    print(f"deploy tx: {h.hex()}  (기다리는 중…)")
    # 게이트웨이가 receipt 를 안 주므로: CREATE 주소 로컬 계산 + getCode 폴링으로 확정.
    addr = _create_address(w3, acct.address, nonce)
    if not _await_code(w3, addr, timeout=150):
        sys.exit(f"배포 확인 실패(getCode 빈값 — 타임아웃). tx={h.hex()} addr(예상)={addr}")
    print("\n✅ 배포 완료")
    print(f"  chainId          : {chain_id}")
    print(f"  deployer(owner)  : {acct.address}")
    print(f"  contract address : {addr}")
    print(f"  deploy tx        : {h.hex()}")
    print(f"\n→ server/.env.local 에 추가:\n  FM_SETTLEMENT_ADDRESS={addr}\n  FM_CHAIN_ID={chain_id}")


def _contract(w3, addr):
    abi = __import__("json").loads(ABI_PATH.read_text())
    return w3.eth.contract(address=w3.to_checksum_address(addr), abi=abi)


def cmd_smoke():
    w3, acct, chain_id = _w3_account()
    addr = os.environ.get("FM_SETTLEMENT_ADDRESS")
    if not addr:
        sys.exit("FM_SETTLEMENT_ADDRESS 필요(먼저 deploy)")
    c = _contract(w3, addr)
    payment_key = f"smoke:{uuid.uuid4().hex}"
    model_uuid = str(uuid.uuid4())
    total = 10000
    pid = w3.keccak(text=payment_key)
    mref = w3.keccak(text=model_uuid)
    nonce = w3.eth.get_transaction_count(acct.address, "latest")
    tx = c.functions.recordSettlement(pid, mref, total).build_transaction(
        {"from": acct.address, "nonce": nonce, "gas": 300_000, "gasPrice": 0, "chainId": chain_id}
    )
    signed = acct.sign_transaction(tx)
    raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
    h = w3.eth.send_raw_transaction(raw)
    print(f"recordSettlement tx: {h.hex()}  total={total}  (기다리는 중…)")
    # receipt 미지원 → getSettlement eth_call 폴링으로 확정.
    import time
    deadline = time.monotonic() + 150
    t = ma = pa = oa = blk = None
    exists = False
    while time.monotonic() < deadline:
        (_mref, t, ma, pa, oa, blk, exists) = c.functions.getSettlement(pid).call()
        if exists:
            break
        time.sleep(2)
    if not exists:
        sys.exit(f"확인 타임아웃(getSettlement exists=False). tx={h.hex()}")
    ok = (ma + pa + oa == t) and ma == 7000 and pa == 2000 and oa == 1000
    print("\n✅ 실 TX + eth_call 확인")
    print(f"  chainId       : {chain_id}")
    print(f"  tx_hash       : {h.hex()}")
    print(f"  block         : {blk}")
    print(f"  paymentKey    : {payment_key}")
    print(f"  총액          : {t}")
    print(f"  모델(70%)     : {ma}")
    print(f"  플랫폼(20%)   : {pa}")
    print(f"  운영(10%)     : {oa}")
    print(f"  분배합==총액  : {ma + pa + oa == t}")
    print(f"\n{'✅ 스모크 PASS' if ok else '❌ 값 불일치'}")
    sys.exit(0 if ok else 1)


def cmd_confirm():
    if len(sys.argv) < 3:
        sys.exit("usage: fm_chain.py confirm <PAYMENT_KEY>")
    w3, _acct, _cid = _w3_account()
    addr = os.environ.get("FM_SETTLEMENT_ADDRESS")
    if not addr:
        sys.exit("FM_SETTLEMENT_ADDRESS 필요")
    c = _contract(w3, addr)
    pid = w3.keccak(text=sys.argv[2])
    (mref, t, ma, pa, oa, blk, exists) = c.functions.getSettlement(pid).call()
    print(f"exists={exists} total={t} model={ma} platform={pa} ops={oa} block={blk}")


def cmd_keygen():
    """새 EOA 생성(Free-Gas = 펀딩 불필요). 개인키는 이 터미널에만 — .env.local 로 복사."""
    from eth_account import Account

    acct = Account.create()
    print("새 계정 생성(Free-Gas 체인 — 펀딩 불필요):")
    print(f"  address     : {acct.address}")
    print(f"  private key : {acct.key.hex()}")
    print("\n→ server/.env.local 에 추가(이 값 채팅에 붙이지 말 것):")
    print(f"  FM_CHAIN_PRIVATE_KEY={acct.key.hex()}")


def main():
    _load_dotenv()
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "keygen":
        cmd_keygen()
    elif cmd == "deploy":
        cmd_deploy()
    elif cmd == "smoke":
        cmd_smoke()
    elif cmd == "confirm":
        cmd_confirm()
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
