package kr.wearless.fmholder.protocol;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.json.JsonMapper;
import com.fasterxml.jackson.databind.DeserializationFeature;
import kr.wearless.fmholder.wallet.HolderWalletService;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.util.Map;
import java.util.function.UnaryOperator;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

class SponsorshipIssuePlanTest {
    private static final String ID = "123e4567-e89b-12d3-a456-426614174000";
    private static final String KEY = "fm-sponsorship:" + ID;
    private static final String HEX_A = "a".repeat(64);
    private static final String HEX_B = "0123456789abcdef".repeat(4);
    private static final String BODY = """
        {"plan":"fmsponsorship-v1","idempotencyKey":"%s",
         "claims":{"modelDid":"did:omn:user","credentialId":"%s","consentDocVersion":"2026-09-v1",
         "participationDocSha256":"%s","profileDocSha256":"%s","consentedAt":"2026-09-25T01:02:03.456Z"}}
        """.formatted(KEY, ID, HEX_A, HEX_B);

    /** Spring MVC 기본 ObjectMapper 와 같은 설정(모르는 최상위 키 무시). */
    private static final ObjectMapper SPRING_LIKE = JsonMapper.builder()
            .disable(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES).build();

    @Test
    void registryPlanIdsAreExactly20CharsAndSponsorshipIsDistinct() {
        for (IssuePlan plan : IssuePlan.values()) {
            assertThat(plan.vcPlanId()).hasSize(20);
        }
        assertThat(IssuePlan.FMSPONSORSHIP_V1.vcPlanId()).isEqualTo("vcplanspons000000001");
        assertThat(IssuePlan.FACELICENSE_V2.vcPlanId()).isEqualTo("vcplanface0000000002");
        assertThat(IssuePlan.fromRequest(" FMSponsorship-V1 ")).contains(IssuePlan.FMSPONSORSHIP_V1);
        assertThat(IssuePlan.fromRequest(null)).contains(IssuePlan.MDL);
        assertThat(IssuePlan.fromRequest("fmsponsorship-v2")).isEmpty();
        assertThat(IssuePlan.FMSPONSORSHIP_V1.isValidIdempotencyKey(KEY)).isTrue();
        assertThat(IssuePlan.FMSPONSORSHIP_V1.isValidIdempotencyKey("fm-license:" + ID)).isFalse();
        assertThat(IssuePlan.FACELICENSE_V2.isValidIdempotencyKey(KEY)).isFalse();
        assertThat(IssuePlan.MDL.requiresIdempotencyKey()).isFalse();
    }

    @Test
    void validSponsorshipClaimsReachIssuerUnderSponsorshipSchema() throws Exception {
        var issuer = mock(IssuerAdminClient.class);
        var service = serviceStoppingAtUpsert(issuer, "did:omn:user");

        assertThatThrownBy(() -> service.issue("model", parse(BODY)))
                .hasMessage("stop-before-protocol");

        var captured = ArgumentCaptor.forClass(IssuerAdminClient.DemoUserReq.class);
        verify(issuer).upsertDemoUser(captured.capture());
        assertThat(captured.getValue().vcSchemaId()).isEqualTo("fmsponsorship-v1");
        assertThat(new ObjectMapper().readValue(captured.getValue().userInfo(),
                new TypeReference<Map<String, String>>() {}))
                .isEqualTo(Map.of(
                    "kr.wearless.fmsponsorship.v1.model_did", "did:omn:user",
                    "kr.wearless.fmsponsorship.v1.credential_id", ID,
                    "kr.wearless.fmsponsorship.v1.consent_doc_version", "2026-09-v1",
                    "kr.wearless.fmsponsorship.v1.participation_doc_sha256", HEX_A,
                    "kr.wearless.fmsponsorship.v1.profile_doc_sha256", HEX_B,
                    "kr.wearless.fmsponsorship.v1.consented_at", "2026-09-25T01:02:03.456Z"));
    }

    @Test
    void snakeCaseAliasesBindToo() throws Exception {
        var req = parse(BODY.replace("\"modelDid\"", "\"model_did\"")
                .replace("\"credentialId\"", "\"credential_id\"")
                .replace("\"consentedAt\"", "\"consented_at\""));
        assertThat(req.sponsorshipClaims().modelDid()).isEqualTo("did:omn:user");
        assertThat(req.sponsorshipClaims().credentialId()).isEqualTo(ID);
        assertThat(req.sponsorshipClaims().consentedAt()).isEqualTo("2026-09-25T01:02:03.456Z");
        assertThat(req.claims()).isNull();
    }

    @Test
    void unknownOrLicenseOnlyClaimKeysAreRejectedAtBinding() {
        for (String extra : new String[] {"\"instagram\":\"@x\"", "\"licenseId\":\"" + ID + "\"",
                "\"face_image_digest\":\"sha256:x\""}) {
            String body = BODY.replace("\"modelDid\"", extra + ",\"modelDid\"");
            assertThatThrownBy(() -> parse(body)).hasMessageContaining("Unrecognized field");
        }
    }

    @Test
    void missingBlankOrMalformedClaimsAreRejectedBeforeIssuerMutation() throws Exception {
        Map<String, UnaryOperator<String>> cases = Map.ofEntries(
            Map.entry("no claims", b -> b.substring(0, b.indexOf(",\n \"claims\"")) + "}"),
            Map.entry("missing modelDid", b -> b.replace("\"modelDid\":\"did:omn:user\",", "")),
            Map.entry("blank consentDocVersion", b -> b.replace("2026-09-v1", "  ")),
            Map.entry("missing profileDocSha256", b -> b.replace(",\"profileDocSha256\":\"" + HEX_B + "\"", "")),
            Map.entry("uppercase hex", b -> b.replace(HEX_A, HEX_A.toUpperCase())),
            Map.entry("short hex", b -> b.replace(HEX_A, "a".repeat(63))),
            Map.entry("prefixed hex", b -> b.replace(HEX_B, "sha256:" + HEX_B.substring(7))),
            Map.entry("offset time", b -> b.replace("01:02:03.456Z", "10:02:03.456+09:00")),
            Map.entry("not a time", b -> b.replace("2026-09-25T01:02:03.456Z", "yesterdayZ")),
            Map.entry("uuid not canonical", b -> b.replace("\"credentialId\":\"" + ID,
                    "\"credentialId\":\"" + ID.toUpperCase())),
            Map.entry("key/credential mismatch", b -> b.replace(KEY,
                    "fm-sponsorship:223e4567-e89b-12d3-a456-426614174000")));
        for (var entry : cases.entrySet()) {
            var issuer = mock(IssuerAdminClient.class);
            var service = serviceStoppingAtUpsert(issuer, "did:omn:user");
            var request = parse(entry.getValue().apply(BODY));
            assertThatThrownBy(() -> service.issue("model", request))
                    .as(entry.getKey())
                    .isInstanceOf(IllegalArgumentException.class);
            verifyNoInteractions(issuer);
        }
    }

    @Test
    void claimedDidMustMatchActualWalletBeforeIssuerMutation() throws Exception {
        var issuer = mock(IssuerAdminClient.class);
        var service = serviceStoppingAtUpsert(issuer, "did:omn:other");
        assertThatThrownBy(() -> service.issue("model", parse(BODY)))
                .isInstanceOf(IllegalArgumentException.class).hasMessageContaining("modelDid");
        verifyNoInteractions(issuer);
    }

    @Test
    void licenseBodyStillBindsLeniently() throws Exception {
        // 라이선스 claims 는 기존처럼 모르는 키를 무시한다(동작 불변).
        var req = parse("""
            {"plan":"facelicense-v2","idempotencyKey":"fm-license:%s",
             "claims":{"modelDid":"did:omn:user","licenseId":"%s","issuedAt":"2026-09-11T00:00:00Z",
             "faceImageDigest":"sha256:x","agreementVersion":"v1","consentDocVersion":"v1.1",
             "somethingElse":"ignored"}}
            """.formatted(ID, ID));
        assertThat(req.claims()).isEqualTo(new IssueVcDtos.Claims("did:omn:user", ID,
                "2026-09-11T00:00:00Z", "sha256:x", "v1", "v1.1"));
        assertThat(req.sponsorshipClaims()).isNull();
    }

    @Test
    void unknownPlanIsStillRejected() {
        var service = new IssueVcService(null, mock(HolderWalletService.class), null, null, null, null);
        assertThatThrownBy(() -> service.issue("model",
                new IssueVcDtos.IssueRequest("fmsponsorship-v9", null, null)))
                .isInstanceOf(IllegalArgumentException.class).hasMessageContaining("unknown plan");
    }

    private static IssueVcDtos.IssueRequest parse(String json) throws Exception {
        return SPRING_LIKE.readValue(json, IssueVcDtos.IssueRequest.class);
    }

    private static IssueVcService serviceStoppingAtUpsert(IssuerAdminClient issuer, String userDid)
            throws Exception {
        var wallets = mock(HolderWalletService.class);
        var kyc = mock(KycSetupService.class);
        when(wallets.exists("model")).thenReturn(true);
        when(wallets.isFlowAComplete("model")).thenReturn(true);
        when(wallets.readUserDid("model")).thenReturn(userDid);
        when(kyc.modelPii("model")).thenReturn("hashed-pii");
        doThrow(new IllegalStateException("stop-before-protocol")).when(issuer).upsertDemoUser(any());
        return new IssueVcService(null, wallets, null, null, issuer, kyc);
    }
}
