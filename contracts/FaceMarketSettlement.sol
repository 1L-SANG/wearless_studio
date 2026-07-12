// SPDX-License-Identifier: MIT
pragma solidity ^0.8.27;

/// @title FaceMarketSettlement
/// @notice Record-only settlement ledger for FaceMarket face-license revenue splits.
///         No coin moves on-chain. Each license transaction records an immutable
///         70/20/10 split (model / platform / ops) keyed by an off-chain paymentId.
///         Confirmation is read back via the public `settlements` getter (eth_call),
///         so no event-log RPC is required.
/// @dev Self-contained (no imports) for single-file .sol upload to OmniOne Chain.
///      Deployed to a Free-Gas BESU network (gasPrice 0). Backend (single console
///      key) is the sole recorder; modelRef is an opaque id, not a chain address,
///      because the team holds one console account (no per-model addresses).
contract FaceMarketSettlement {
    // Split basis points out of 10_000. Model 70% / platform 20% / ops = remainder.
    uint256 public constant MODEL_BPS = 7_000;
    uint256 public constant PLATFORM_BPS = 2_000;
    // OPS gets `total - model - platform` so the three parts always re-sum to total
    // (dust from integer division accrues to ops). This contract math is canonical;
    // the DB stores these returned values, it does not recompute them.

    struct Settlement {
        bytes32 modelRef;     // opaque off-chain model id (e.g. keccak of model uuid)
        uint256 total;        // license fee in minor units (e.g. KRW)
        uint256 modelAmount;
        uint256 platformAmount;
        uint256 opsAmount;
        uint256 blockNumber;  // recorded block (timestamp source lives off-chain)
        bool exists;
    }

    address public owner;
    uint256 public count;

    // paymentId (deterministic from job id, off-chain) => settlement record.
    // Public getter is the eth_call confirmation path.
    mapping(bytes32 => Settlement) public settlements;

    event SettlementRecorded(
        bytes32 indexed paymentId,
        bytes32 indexed modelRef,
        uint256 total,
        uint256 modelAmount,
        uint256 platformAmount,
        uint256 opsAmount
    );

    event OwnerTransferred(address indexed from, address indexed to);

    error NotOwner();
    error DuplicatePaymentId(bytes32 paymentId);
    error ZeroPaymentId();
    error ZeroTotal();
    error ZeroAddress();

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    constructor() {
        owner = msg.sender;
    }

    /// @notice Record one immutable license settlement. Reverts on a duplicate
    ///         paymentId so a retried/idempotent backend call cannot double-record.
    /// @param paymentId deterministic id derived off-chain from the detail-page job id
    /// @param modelRef  opaque model reference (keccak256 of the model uuid)
    /// @param total     license fee (minor units, > 0)
    function recordSettlement(bytes32 paymentId, bytes32 modelRef, uint256 total)
        external
        onlyOwner
        returns (uint256 modelAmount, uint256 platformAmount, uint256 opsAmount)
    {
        if (paymentId == bytes32(0)) revert ZeroPaymentId();
        if (total == 0) revert ZeroTotal();
        if (settlements[paymentId].exists) revert DuplicatePaymentId(paymentId);

        modelAmount = (total * MODEL_BPS) / 10_000;
        platformAmount = (total * PLATFORM_BPS) / 10_000;
        opsAmount = total - modelAmount - platformAmount; // remainder — dust-safe

        settlements[paymentId] = Settlement({
            modelRef: modelRef,
            total: total,
            modelAmount: modelAmount,
            platformAmount: platformAmount,
            opsAmount: opsAmount,
            blockNumber: block.number,
            exists: true
        });
        count += 1;

        emit SettlementRecorded(
            paymentId, modelRef, total, modelAmount, platformAmount, opsAmount
        );
    }

    /// @notice Explicit read (same data as the auto getter) for backend eth_call.
    function getSettlement(bytes32 paymentId)
        external
        view
        returns (
            bytes32 modelRef,
            uint256 total,
            uint256 modelAmount,
            uint256 platformAmount,
            uint256 opsAmount,
            uint256 blockNumber,
            bool exists
        )
    {
        Settlement storage s = settlements[paymentId];
        return (
            s.modelRef, s.total, s.modelAmount, s.platformAmount,
            s.opsAmount, s.blockNumber, s.exists
        );
    }

    function transferOwner(address newOwner) external onlyOwner {
        if (newOwner == address(0)) revert ZeroAddress();
        emit OwnerTransferred(owner, newOwner);
        owner = newOwner;
    }
}
