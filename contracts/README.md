# FaceMarket Contracts

`FaceMarketSettlement.sol` — record-only revenue-split ledger for face-license
transactions. No coin moves; each transaction records an immutable 70/20/10 split
(model / platform / ops) keyed by an off-chain `paymentId`. Deployed to OmniOne
Chain (self BESU, **Free-Gas Network**, `gasPrice 0`).

## Design (why record-only)

- Free-Gas BESU: no native coin to move, so settlement = **audit record**, not transfer.
- Confirmation without event-log RPC: read back via the public `settlements(paymentId)`
  getter over `eth_call` (some event-log RPCs are disabled on the graded chain).
- One console account per team → no per-model chain addresses → model identified by an
  opaque `bytes32 modelRef` (= `keccak256(model uuid)`), not an address.
- Idempotent: duplicate `paymentId` **reverts**, so a retried backend call can't double-record.
- Canonical math lives here (dust from integer division accrues to ops so parts always
  re-sum to `total`). The DB **stores the returned values**, it does not recompute them.

## Compile

No local toolchain needed:

```bash
npx --yes solc@0.8.27 --bin --abi contracts/FaceMarketSettlement.sol -o build/
```

Verified: compiles clean on solc 0.8.27 (0 warnings). Self-contained, no imports —
uploadable as a single `.sol` to the OmniOne Chain console, or as `abi + bin`.

## Test

Foundry (`contracts/test/FaceMarketSettlement.t.sol`): duplicate-`paymentId` revert,
zero-guards, owner-only, and a fuzz proof that `model + platform + ops == total`.

```bash
# from contracts/  (needs: curl -L https://foundry.paradigm.xyz | bash && foundryup)
forge test
```

Split invariant is also checked framework-free in
`.omx/.../scratchpad/split_check.mjs` (BigInt mirror of the uint256 math).

## Deploy (OmniOne Chain console) — pending team account (FM-05 C)

1. Console → upload `FaceMarketSettlement.sol` (single file) **or** `abi + bin`.
2. Deploy → record deployed **address** + **chainId** (`eth_chainId`).
3. Save the console **private key / JSON keystore** (key management is our responsibility —
   prod would use HSM/KMS; demo keeps the key in backend env, never committed).

## Backend integration (S3, FM-32/33)

- Sign with **ethers.js**, **legacy tx `type: 0`, `gasPrice: 0`** (Free-Gas BESU rejects
  EIP-1559). Single signer key → **serialize nonce** (one queue) to avoid nonce clashes.
- `paymentId` = deterministic from the detail-page **job id** (idempotent re-record safe).
- Hook point = worker **final `succeeded`** (generation is async; partial success rules
  in FM-32). Confirm via `getSettlement(paymentId).exists` over `eth_call` (backend proxy —
  never expose the console API key to the browser).
