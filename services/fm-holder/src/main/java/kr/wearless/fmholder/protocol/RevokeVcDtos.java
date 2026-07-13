package kr.wearless.fmholder.protocol;

import org.omnione.did.base.datamodel.data.ReqRevokeVc;

/**
 * Issuer VC 폐기(revoke-vc) 3스텝 프로토콜 DTO + 홀더 verify/revoke 응답 모양.
 * 서버 {@code org.omnione.did.issuer.v1.agent.dto.vc.*ReqDto/*ResDto} 미러(무프로파일 의존 회피용 로컬 record).
 */
public final class RevokeVcDtos {

    private RevokeVcDtos() {}

    // ── 홀더 API 요청 본문(Python 백엔드가 보냄) ───────────────────
    /** POST /holder/vc/verify, /holder/models/{id}/revoke-vc 공통 본문 {vcId}. */
    public record VcIdReq(String vcId) {}

    // ── Issuer /issuer/api/v1 요청/응답 ─────────────────────────────
    /** inspect-propose-revoke 요청: id(28자 메시지 id) + vcId. */
    public record InspectReq(String id, String vcId) {}

    /** revoke-vc 요청: {id, txId, request=ReqRevokeVc(USER DID #assert 서명)}. */
    public record RevokeReq(String id, String txId, ReqRevokeVc request) {}

    /** complete-revoke 요청: {id, txId}. */
    public record CompleteReq(String id, String txId) {}

    // ── 홀더 API 응답(Python 백엔드 계약) ───────────────────────────
    /** POST /holder/vc/verify → {verified, status:"valid"|"revoked"|"unknown", onChain}. */
    public record VerifyResult(boolean verified, String status, boolean onChain, String note) {}

    /** POST /holder/models/{modelId}/revoke-vc → {revoked, txId}. */
    public record RevokeResult(boolean revoked, String txId, String status, String note) {}
}
