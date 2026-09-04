// SPDX-License-Identifier: MIT
pragma solidity ^0.8.27;

/// @title FaceMarketProvenance
/// @notice Record-only anchor for FaceMarket published deliverables. Stores the
///         sha256 of the file a seller downloaded, keyed by an off-chain
///         publicationId, so a later DB edit cannot go unnoticed.
/// @dev Same constraints as FaceMarketSettlement: self-contained single file for
///      OmniOne Chain console upload, owner-only recorder, duplicate key reverts,
///      confirmation via the public getter (eth_call) because the gateway does not
///      expose transaction receipts.
contract FaceMarketProvenance {
    struct Publication {
        bytes32 imageHash;    // sha256 of the pre-signature bytes
        bytes32 licenseRef;   // keccak256 of the license uuid
        uint256 blockNumber;
        bool exists;
    }

    address public owner;
    uint256 public count;

    mapping(bytes32 => Publication) public publications;

    event PublicationRecorded(
        bytes32 indexed publicationId,
        bytes32 indexed licenseRef,
        bytes32 imageHash
    );
    event OwnerTransferred(address indexed from, address indexed to);

    error NotOwner();
    error DuplicatePublicationId(bytes32 publicationId);
    error ZeroPublicationId();
    error ZeroImageHash();
    error ZeroAddress();

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    constructor() {
        owner = msg.sender;
    }

    /// @notice Record one immutable publication anchor.
    function recordPublication(bytes32 publicationId, bytes32 imageHash, bytes32 licenseRef)
        external
        onlyOwner
    {
        if (publicationId == bytes32(0)) revert ZeroPublicationId();
        if (imageHash == bytes32(0)) revert ZeroImageHash();
        if (publications[publicationId].exists) revert DuplicatePublicationId(publicationId);

        publications[publicationId] = Publication({
            imageHash: imageHash,
            licenseRef: licenseRef,
            blockNumber: block.number,
            exists: true
        });
        count += 1;

        emit PublicationRecorded(publicationId, licenseRef, imageHash);
    }

    /// @notice eth_call confirmation path (no receipt RPC on this gateway).
    function getPublication(bytes32 publicationId)
        external
        view
        returns (bytes32 imageHash, bytes32 licenseRef, uint256 blockNumber, bool exists)
    {
        Publication storage p = publications[publicationId];
        return (p.imageHash, p.licenseRef, p.blockNumber, p.exists);
    }

    function transferOwner(address next) external onlyOwner {
        if (next == address(0)) revert ZeroAddress();
        emit OwnerTransferred(owner, next);
        owner = next;
    }
}
