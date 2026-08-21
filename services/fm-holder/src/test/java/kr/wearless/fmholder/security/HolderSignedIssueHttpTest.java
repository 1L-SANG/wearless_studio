package kr.wearless.fmholder.security;

import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.time.Instant;
import kr.wearless.fmholder.api.IssueController;
import kr.wearless.fmholder.api.IssueIdempotencyStore;
import kr.wearless.fmholder.protocol.IssueVcDtos;
import kr.wearless.fmholder.protocol.IssueVcService;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.mockito.ArgumentCaptor;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;
import static org.springframework.test.web.servlet.setup.MockMvcBuilders.standaloneSetup;

class HolderSignedIssueHttpTest {
    private static final String SECRET = "signed-http-secret";
    private static final String MODEL = "model-http";
    private static final String PATH = "/holder/models/" + MODEL + "/issue-vc";
    private static final String KEY = "fm-license:123e4567-e89b-12d3-a456-426614174000";
    private static final String BODY = """
            {"plan":"facelicense","idempotencyKey":"%s","claims":{
              "allowed_use":"allowed","forbidden_use":"forbidden","unit_price":7,
              "license_valid_until":"2099-12-31","face_image_digest":"sha256:opaque",
              "model_name":"model"}}
            """.formatted(KEY);

    @Test
    void signedBodyReachesControllerAndSameKeyReplaysOneServiceCall(@TempDir Path dataDir)
            throws Exception {
        IssueVcService service = mock(IssueVcService.class);
        when(service.issue(eq(MODEL), any(IssueVcDtos.IssueRequest.class)))
                .thenReturn(new IssueVcService.IssueResult(
                        "vc-1", "did:omn:issuer", "tx-1", null,
                        "issued", "issued", "did:omn:user"));
        MockMvc mvc = mvc(dataDir, service);

        mvc.perform(signed(BODY, "nonce_signed_http_00001"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.vcId").value("vc-1"));
        mvc.perform(signed(BODY, "nonce_signed_http_00002"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.vcId").value("vc-1"));

        ArgumentCaptor<IssueVcDtos.IssueRequest> request =
                ArgumentCaptor.forClass(IssueVcDtos.IssueRequest.class);
        verify(service, times(1)).issue(eq(MODEL), request.capture());
        assertEquals(KEY, request.getValue().idempotencyKey());
    }

    @Test
    void unsignedOrSignedMissingKeyNeverCallsService(@TempDir Path dataDir) throws Exception {
        IssueVcService service = mock(IssueVcService.class);
        MockMvc mvc = mvc(dataDir, service);

        mvc.perform(post(PATH)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(BODY))
                .andExpect(status().isUnauthorized());

        String missingKey = BODY.replace(
                "\"idempotencyKey\":\"" + KEY + "\",", "");
        mvc.perform(signed(missingKey, "nonce_signed_http_00003"))
                .andExpect(status().isBadRequest());

        verify(service, never()).issue(eq(MODEL), any());
    }

    private static MockMvc mvc(Path dataDir, IssueVcService service) {
        return standaloneSetup(new IssueController(
                        service, new IssueIdempotencyStore(dataDir.toString())))
                .addFilters(new HolderHmacFilter(SECRET, dataDir.toString()))
                .build();
    }

    private static org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder signed(
            String json, String nonce) {
        byte[] body = json.getBytes(StandardCharsets.UTF_8);
        String timestamp = String.valueOf(Instant.now().getEpochSecond());
        return post(PATH)
                .contentType(MediaType.APPLICATION_JSON)
                .content(body)
                .header("X-FM-Timestamp", timestamp)
                .header("X-FM-Nonce", nonce)
                .header("X-FM-Signature", HolderHmacFilter.signature(
                        SECRET, "POST", PATH, timestamp, nonce, body));
    }
}
