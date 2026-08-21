package kr.wearless.fmholder.api;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.file.Path;
import kr.wearless.fmholder.protocol.IssueVcDtos;
import kr.wearless.fmholder.protocol.IssueVcService;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class IssueControllerTest {
    private static final String MODEL = "model-1";

    @Test
    void faceLicenseRejectsMissingOrInvalidKeyBeforeService(@TempDir Path dataDir) throws Exception {
        IssueVcService service = mock(IssueVcService.class);
        IssueController controller = new IssueController(service, new IssueIdempotencyStore(dataDir));
        IssueVcDtos.Claims claims = claims();

        for (String key : new String[] {
                null,
                "",
                "fm-license:not-a-uuid",
                "FM-LICENSE:123e4567-e89b-12d3-a456-426614174000",
                "fm-license:123E4567-E89B-12D3-A456-426614174000"
        }) {
            ResponseStatusException error = assertThrows(ResponseStatusException.class,
                    () -> controller.issueVc(
                            MODEL,
                            new IssueVcDtos.IssueRequest("facelicense", claims, key)));
            assertEquals(HttpStatus.BAD_REQUEST, error.getStatusCode());
            assertEquals("invalid_idempotency_key", error.getReason());
        }
        verify(service, never()).issue(eq(MODEL), any());
    }

    @Test
    void idempotencyConflictUsesStableNonSensitiveHttpFailure(@TempDir Path dataDir)
            throws Exception {
        IssueVcService service = mock(IssueVcService.class);
        IssueController controller = new IssueController(service, new IssueIdempotencyStore(dataDir));
        String key = "fm-license:123e4567-e89b-12d3-a456-426614174000";
        IssueVcDtos.IssueRequest first = new IssueVcDtos.IssueRequest(
                "facelicense", claims(), key);
        IssueVcDtos.IssueRequest changed = new IssueVcDtos.IssueRequest(
                "facelicense",
                new IssueVcDtos.Claims("changed", "forbidden", 7, "2099-12-31",
                        "sha256:opaque", "model"),
                key);
        when(service.issue(MODEL, first)).thenReturn(result("vc-1"));

        assertEquals("vc-1", controller.issueVc(MODEL, first).vcId());
        ResponseStatusException error = assertThrows(ResponseStatusException.class,
                () -> controller.issueVc(MODEL, changed));

        assertEquals(HttpStatus.CONFLICT, error.getStatusCode());
        assertEquals("issue_idempotency_unavailable", error.getReason());
        verify(service, never()).issue(MODEL, changed);
    }

    @Test
    void legacyMdlWithoutKeyStillCallsExistingFlowB(@TempDir Path dataDir) throws Exception {
        IssueVcService service = mock(IssueVcService.class);
        IssueController controller = new IssueController(service, new IssueIdempotencyStore(dataDir));
        IssueVcDtos.IssueRequest request = new IssueVcDtos.IssueRequest("mdl", null, null);
        when(service.issue(MODEL, request)).thenReturn(result("vc-mdl"));

        IssueVcService.IssueResult actual = controller.issueVc(MODEL, request);

        assertEquals("vc-mdl", actual.vcId());
        verify(service).issue(MODEL, request);
    }

    private static IssueVcDtos.Claims claims() {
        return new IssueVcDtos.Claims(
                "allowed",
                "forbidden",
                7,
                "2099-12-31",
                "sha256:opaque",
                "model");
    }

    private static IssueVcService.IssueResult result(String vcId) throws Exception {
        return new IssueVcService.IssueResult(
                vcId,
                "did:omn:issuer",
                "tx-1",
                new ObjectMapper().readTree("{\"id\":\"" + vcId + "\"}"),
                "issued",
                "issued",
                "did:omn:user");
    }
}
