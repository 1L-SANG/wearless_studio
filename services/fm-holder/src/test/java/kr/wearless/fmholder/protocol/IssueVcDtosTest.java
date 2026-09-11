package kr.wearless.fmholder.protocol;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class IssueVcDtosTest {

    @Test
    void bindsFaceLicenseSnakeCaseClaims() throws Exception {
        var json = """
                {
                  "plan": "facelicense-v2",
                  "idempotencyKey": "fm-license:123e4567-e89b-12d3-a456-426614174000",
                  "claims": {
                    "model_did": "did:omn:user",
                    "license_id": "123e4567-e89b-12d3-a456-426614174000",
                    "issued_at": "2026-09-11T00:00:00Z",
                    "face_image_digest": "sha256:opaque",
                    "agreement_version": "v1",
                    "consent_doc_version": "v1.1"
                  }
                }
                """;

        var req = new ObjectMapper().readValue(json, IssueVcDtos.IssueRequest.class);

        assertThat(req.idempotencyKey())
                .isEqualTo("fm-license:123e4567-e89b-12d3-a456-426614174000");
        assertThat(req.claims().modelDid()).isEqualTo("did:omn:user");
        assertThat(req.claims().licenseId()).isEqualTo("123e4567-e89b-12d3-a456-426614174000");
        assertThat(req.claims().issuedAt()).isEqualTo("2026-09-11T00:00:00Z");
        assertThat(req.claims().faceImageDigest()).isEqualTo("sha256:opaque");
        assertThat(req.claims().agreementVersion()).isEqualTo("v1");
        assertThat(req.claims().consentDocVersion()).isEqualTo("v1.1");
    }
}
