package kr.wearless.fmholder.protocol;

import com.fasterxml.jackson.annotation.JsonAlias;
import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonIgnore;
import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.json.JsonMapper;
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
    // 본문 없음/plan 생략 → MDL(기존 동작). plan="facelicense-v2" → FaceLicense VC + claims 를
    // Issuer user(userInfo)로 upsert 해 credentialSubject 에 실린다(값은 서버측 user.data 에서 유래).
    // plan="fmsponsorship-v1" → 협찬 동의 VC. 같은 "claims" 키를 {@link SponsorshipClaims} 로 엄격 파싱
    // (모르는 키 = 400). 라이선스 claims 는 기존대로 관대하게({@link Claims}, 모르는 키 무시).
    @JsonInclude(JsonInclude.Include.NON_NULL)
    public record IssueRequest(String plan, Claims claims, String idempotencyKey,
                               @JsonIgnore SponsorshipClaims sponsorshipClaims) {
        private static final ObjectMapper LENIENT = JsonMapper.builder()
                .disable(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES)   // Spring 기본값과 동일
                .build();
        private static final ObjectMapper STRICT = JsonMapper.builder()
                .enable(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES)
                .build();

        public IssueRequest(String plan, Claims claims, String idempotencyKey) {
            this(plan, claims, idempotencyKey, null);
        }

        public IssueRequest(String plan, Claims claims) {
            this(plan, claims, null);
        }

        public static IssueRequest sponsorship(SponsorshipClaims claims, String idempotencyKey) {
            return new IssueRequest(IssuePlan.FMSPONSORSHIP_V1.requestPlan(), null, idempotencyKey, claims);
        }

        /** plan 을 보고 "claims" 를 플랜별 타입으로 바인딩한다. */
        @JsonCreator
        static IssueRequest fromJson(@JsonProperty("plan") String plan,
                                     @JsonProperty("claims") JsonNode claims,
                                     @JsonProperty("idempotencyKey") String idempotencyKey) throws Exception {
            boolean absent = claims == null || claims.isNull() || claims.isMissingNode();
            if (IssuePlan.fromRequest(plan).orElse(null) == IssuePlan.FMSPONSORSHIP_V1) {
                SponsorshipClaims sc = absent ? null : STRICT.treeToValue(claims, SponsorshipClaims.class);
                return new IssueRequest(plan, null, idempotencyKey, sc);
            }
            Claims c = absent ? null : LENIENT.treeToValue(claims, Claims.class);
            return new IssueRequest(plan, c, idempotencyKey, null);
        }
    }

    /** Immutable evidence for the versioned kr.wearless.facelicense.v2 schema. */
    @JsonInclude(JsonInclude.Include.NON_NULL)
    public record Claims(
            @JsonAlias("model_did") String modelDid,
            @JsonAlias("license_id") String licenseId,
            @JsonAlias("issued_at") String issuedAt,
            @JsonAlias("face_image_digest") String faceImageDigest,
            @JsonAlias("agreement_version") String agreementVersion,
            @JsonAlias("consent_doc_version") String consentDocVersion) {}

    /** 협찬 동의 사실만 — kr.wearless.fmsponsorship.v1. 개인정보(인스타·팔로워·사이즈)는 넣지 않는다. */
    @JsonInclude(JsonInclude.Include.NON_NULL)
    public record SponsorshipClaims(
            @JsonAlias("model_did") String modelDid,
            @JsonAlias("credential_id") String credentialId,
            @JsonAlias("consent_doc_version") String consentDocVersion,
            @JsonAlias("participation_doc_sha256") String participationDocSha256,
            @JsonAlias("profile_doc_sha256") String profileDocSha256,
            @JsonAlias("consented_at") String consentedAt) {}
}
