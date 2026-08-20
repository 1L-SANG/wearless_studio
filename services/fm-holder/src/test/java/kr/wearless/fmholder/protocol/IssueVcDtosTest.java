package kr.wearless.fmholder.protocol;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class IssueVcDtosTest {

    @Test
    void bindsFaceLicenseSnakeCaseClaims() throws Exception {
        var json = """
                {
                  "plan": "facelicense",
                  "claims": {
                    "allowed_use": "smoke",
                    "forbidden_use": "resale",
                    "unit_price": 0,
                    "license_valid_until": "2099-12-31",
                    "face_image_digest": "sha256:opaque",
                    "model_name": "smoke"
                  }
                }
                """;

        var req = new ObjectMapper().readValue(json, IssueVcDtos.IssueRequest.class);

        assertThat(req.claims().allowedUse()).isEqualTo("smoke");
        assertThat(req.claims().forbiddenUse()).isEqualTo("resale");
        assertThat(req.claims().unitPrice()).isZero();
        assertThat(req.claims().licenseValidUntil()).isEqualTo("2099-12-31");
        assertThat(req.claims().faceImageDigest()).isEqualTo("sha256:opaque");
        assertThat(req.claims().modelName()).isEqualTo("smoke");
    }
}
