// SPDX-License-Identifier: MIT
pragma solidity ^0.8.27;

// Foundry test. Run: `forge test` (from contracts/, with foundry installed).
// Covers the FM-01 DoD: duplicate paymentId revert + dust-safe split invariant.

import "forge-std/Test.sol";
import "../FaceMarketSettlement.sol";

contract FaceMarketSettlementTest is Test {
    FaceMarketSettlement s;
    bytes32 constant PID = keccak256("job-1");
    bytes32 constant MREF = keccak256("model-uuid");

    function setUp() public {
        s = new FaceMarketSettlement();
    }

    function test_recordsSplit_70_20_10() public {
        (uint256 m, uint256 p, uint256 o) = s.recordSettlement(PID, MREF, 10_000);
        assertEq(m, 7_000);
        assertEq(p, 2_000);
        assertEq(o, 1_000);
        assertEq(s.count(), 1);
    }

    function test_duplicatePaymentId_reverts() public {
        s.recordSettlement(PID, MREF, 10_000);
        vm.expectRevert(
            abi.encodeWithSelector(FaceMarketSettlement.DuplicatePaymentId.selector, PID)
        );
        s.recordSettlement(PID, MREF, 10_000); // retry same id must revert
    }

    function test_zeroTotal_reverts() public {
        vm.expectRevert(FaceMarketSettlement.ZeroTotal.selector);
        s.recordSettlement(PID, MREF, 0);
    }

    function test_zeroPaymentId_reverts() public {
        vm.expectRevert(FaceMarketSettlement.ZeroPaymentId.selector);
        s.recordSettlement(bytes32(0), MREF, 10_000);
    }

    function test_onlyOwner_records() public {
        vm.prank(address(0xBEEF));
        vm.expectRevert(FaceMarketSettlement.NotOwner.selector);
        s.recordSettlement(PID, MREF, 10_000);
    }

    /// forge-config: default.fuzz.runs = 512
    function testFuzz_splitSumsToTotal(uint256 total) public {
        total = bound(total, 1, type(uint128).max);
        bytes32 pid = keccak256(abi.encode(total));
        (uint256 m, uint256 p, uint256 o) = s.recordSettlement(pid, MREF, total);
        assertEq(m + p + o, total); // dust-safe: parts always re-sum to total
    }
}
