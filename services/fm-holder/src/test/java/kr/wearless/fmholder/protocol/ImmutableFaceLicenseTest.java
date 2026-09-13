package kr.wearless.fmholder.protocol;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.core.type.TypeReference;
import kr.wearless.fmholder.wallet.HolderWalletService;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

class ImmutableFaceLicenseTest {
    private static final String BODY = """
        {"plan":"facelicense-v2","idempotencyKey":"fm-license:123e4567-e89b-12d3-a456-426614174000",
         "claims":{"modelDid":"did:omn:user","licenseId":"123e4567-e89b-12d3-a456-426614174000",
         "issuedAt":"2026-09-11T01:02:03.456789Z","faceImageDigest":"sha256:evidence",
         "agreementVersion":"v1","consentDocVersion":"v1.1"}}
        """;

    @Test
    void issuerReceivesOnlyImmutableClaimsInSeparateSchema() throws Exception {
        var request = new ObjectMapper().readValue(BODY, IssueVcDtos.IssueRequest.class);
        var wallets = mock(HolderWalletService.class);
        var issuer = mock(IssuerAdminClient.class);
        var kyc = mock(KycSetupService.class);
        when(wallets.exists("model")).thenReturn(true);
        when(wallets.isFlowAComplete("model")).thenReturn(true);
        when(wallets.readUserDid("model")).thenReturn("did:omn:user");
        when(kyc.modelPii("model")).thenReturn("hashed-pii");
        doThrow(new IllegalStateException("stop-before-protocol"))
                .when(issuer).upsertDemoUser(any());
        var service = new IssueVcService(null, wallets, null, null, issuer, kyc);
        assertThatThrownBy(() -> service.issue("model", request))
                .hasMessage("stop-before-protocol");
        var captured = ArgumentCaptor.forClass(IssuerAdminClient.DemoUserReq.class);
        verify(issuer).upsertDemoUser(captured.capture());
        assertThat(captured.getValue().vcSchemaId()).isEqualTo("facelicense-v2");
        assertThat(new ObjectMapper().readValue(captured.getValue().userInfo(),
                new TypeReference<Map<String, String>>() {}))
                .isEqualTo(Map.of(
                    "kr.wearless.facelicense.v2.model_did", "did:omn:user",
                    "kr.wearless.facelicense.v2.license_id", "123e4567-e89b-12d3-a456-426614174000",
                    "kr.wearless.facelicense.v2.issued_at", "2026-09-11T01:02:03.456789Z",
                    "kr.wearless.facelicense.v2.face_image_digest", "sha256:evidence",
                    "kr.wearless.facelicense.v2.agreement_version", "v1",
                    "kr.wearless.facelicense.v2.consent_doc_version", "v1.1"));
    }

    @Test
    void claimedDidMustMatchActualWalletBeforeIssuerMutation() throws Exception {
        var request = new ObjectMapper().readValue(BODY, IssueVcDtos.IssueRequest.class);
        var wallets = mock(HolderWalletService.class);
        var issuer = mock(IssuerAdminClient.class);
        when(wallets.exists("model")).thenReturn(true);
        when(wallets.isFlowAComplete("model")).thenReturn(true);
        when(wallets.readUserDid("model")).thenReturn("did:omn:other");
        var service = new IssueVcService(null, wallets, null, null, issuer, null);
        assertThatThrownBy(() -> service.issue("model", request))
                .isInstanceOf(IllegalArgumentException.class).hasMessageContaining("modelDid");
        verifyNoInteractions(issuer);
    }
}
