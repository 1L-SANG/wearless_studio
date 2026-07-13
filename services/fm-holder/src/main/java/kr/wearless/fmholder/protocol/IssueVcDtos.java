package kr.wearless.fmholder.protocol;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.databind.JsonNode;
import org.omnione.did.base.datamodel.data.AccE2e;
import org.omnione.did.base.datamodel.data.DidAuth;
import org.omnione.did.base.datamodel.data.E2e;

/**
 * TAS issue-vc (Flow B) 요청/응답 DTO — 서버 {@code tas.v1.agent.dto.vc} 와이어 포맷 미러.
 * 중첩 타입(DidAuth·AccE2e·E2e)은 TAS 정본 datamodel 재사용(직렬화 호환).
 * request-ecdh / request-create-token 은 Flow A 와 동일 엔드포인트라 {@link RegisterUserDtos} 재사용.
 */
public final class IssueVcDtos {
    private IssueVcDtos() {}

    // ── B1 offer-issue-vc/qr ─────────────────────────────────────
    @JsonInclude(JsonInclude.Include.NON_NULL)
    public record OfferReq(String id, String vcPlanId, String issuer) {}

    /** issueOfferPayload 는 offerId 만 쓰므로 느슨하게 파싱(JsonNode). */
    public record OfferRes(String offerId, String validUntil, JsonNode issueOfferPayload) {}

    // ── B2 propose-issue-vc ──────────────────────────────────────
    @JsonInclude(JsonInclude.Include.NON_NULL)
    public record ProposeReq(String id, String vcPlanId, String issuer, String offerId) {}

    public record ProposeRes(String txId, String refId) {}

    // ── B5 request-issue-profile (응답 = 원문 JSON 문자열, 서비스에서 JsonNode 파싱) ──
    @JsonInclude(JsonInclude.Include.NON_NULL)
    public record ProfileReq(String id, String txId, String serverToken) {}

    // ── B6 request-issue-vc ──────────────────────────────────────
    @JsonInclude(JsonInclude.Include.NON_NULL)
    public record IssueVcReq(String id, String txId, String serverToken,
                             DidAuth didAuth, AccE2e accE2e, String encReqVc) {}

    public record IssueVcRes(String txId, E2e e2e) {}

    // ── B7 confirm-issue-vc ──────────────────────────────────────
    @JsonInclude(JsonInclude.Include.NON_NULL)
    public record ConfirmReq(String id, String txId, String serverToken, String vcId) {}

    public record ConfirmRes(String txId) {}

    // ── 홀더측 encReqVc 페이로드 (발급자 ReqVc/ProfileInfo 미러) ──────────
    // 발급자 IssueServiceBase.validateRequestVc 가 검증: refId(B2), profile.id(B5),
    // profile.issuerNonce(=process.issuerNonce 문자열). credentialRequest 는 MDL(non-ZKP)이라 생략.
    @JsonInclude(JsonInclude.Include.NON_NULL)
    public record ReqVc(String refId, ProfileInfo profile) {}

    @JsonInclude(JsonInclude.Include.NON_NULL)
    public record ProfileInfo(String id, String issuerNonce) {}

    // ── POST /holder/models/{id}/issue-vc 요청 본문 (선택) ────────────────
    // 본문 없음/plan 생략 → MDL(기존 동작). plan="facelicense" → FaceLicense VC + claims 를
    // Issuer user(userInfo)로 upsert 해 credentialSubject 에 실린다(값은 서버측 user.data 에서 유래).
    @JsonInclude(JsonInclude.Include.NON_NULL)
    public record IssueRequest(String plan, Claims claims) {}

    /** FaceLicense claim 입력. namespace kr.wearless.facelicense 의 6개 claim 에 매핑된다. */
    @JsonInclude(JsonInclude.Include.NON_NULL)
    public record Claims(String allowedUse, String forbiddenUse, Integer unitPrice,
                         String licenseValidUntil, String faceImageDigest, String modelName) {}
}
