package kr.wearless.fmholder.api;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.concurrent.atomic.AtomicInteger;
import kr.wearless.fmholder.protocol.IssueVcDtos;
import kr.wearless.fmholder.protocol.IssueVcService;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
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

    @Test
    void nullNonIssuedOrBlankVcResultFailsClosedAndRestartDoesNotCallService(
            @TempDir Path dataDir) throws Exception {
        IssueVcService.IssueResult[] invalidResults = {
                null,
                result("vc-incomplete", "flow_a_incomplete"),
                result("   ", "issued")
        };

        for (int index = 0; index < invalidResults.length; index++) {
            Path caseDir = dataDir.resolve("invalid-" + index);
            String key = "fm-license:123e4567-e89b-12d3-a456-42661417400" + index;
            IssueVcDtos.IssueRequest request = new IssueVcDtos.IssueRequest(
                    "facelicense", claims(), key);
            IssueVcService firstService = mock(IssueVcService.class);
            when(firstService.issue(MODEL, request)).thenReturn(invalidResults[index]);
            IssueController first = new IssueController(
                    firstService, new IssueIdempotencyStore(caseDir));

            assertUnavailable(() -> first.issueVc(MODEL, request));
            verify(firstService).issue(MODEL, request);

            IssueVcService retryService = mock(IssueVcService.class);
            IssueController restarted = new IssueController(
                    retryService, new IssueIdempotencyStore(caseDir));
            assertUnavailable(() -> restarted.issueVc(MODEL, request));
            verify(retryService, never()).issue(eq(MODEL), any());
        }
    }

    @Test
    void directoryForceFailureUsesStableNonSensitiveConflict(@TempDir Path dataDir)
            throws Exception {
        IssueVcService service = mock(IssueVcService.class);
        IssueVcDtos.IssueRequest request = new IssueVcDtos.IssueRequest(
                "facelicense",
                claims(),
                "fm-license:123e4567-e89b-12d3-a456-426614174000");
        when(service.issue(MODEL, request)).thenReturn(result("vc-1"));
        AtomicInteger directoryForces = new AtomicInteger();
        IssueController controller = new IssueController(
                service,
                new IssueIdempotencyStore(dataDir, () -> {
                    if (directoryForces.incrementAndGet() == 2) {
                        throw new IllegalStateException("simulated directory force failure");
                    }
                }));

        assertUnavailable(() -> controller.issueVc(MODEL, request));
    }

    @Test
    void nullEmptyOrTruncatedIntentAndResultFailClosedWithoutRewriteOrFlowB(
            @TempDir Path dataDir) throws Exception {
        String[] corruptJson = {"null", "", "{"};
        for (String suffix : new String[] {".intent", ".result"}) {
            for (int index = 0; index < corruptJson.length; index++) {
                Path caseDir = dataDir.resolve(suffix.substring(1) + "-" + index);
                String key = "fm-license:123e4567-e89b-12d3-a456-4266141740"
                        + (suffix.equals(".intent") ? "1" : "2") + index;
                IssueVcDtos.IssueRequest request = new IssueVcDtos.IssueRequest(
                        "facelicense", claims(), key);
                IssueIdempotencyStore setup = new IssueIdempotencyStore(caseDir);
                if (suffix.equals(".intent")) {
                    // An Error, unlike a caught Exception, is never routed
                    // through the first-attempt cleanup path, so it genuinely
                    // leaves the intent file behind on disk — matching a real
                    // crash mid-issuance rather than an in-process thrown
                    // exception (which is now cleaned up so it can be
                    // retried).
                    assertThrows(Error.class,
                            () -> setup.execute(MODEL, request, () -> {
                                throw new Error("simulated crash");
                            }));
                } else {
                    setup.execute(MODEL, request, () -> result("vc-1"));
                }
                Path artifact;
                try (var entries = Files.list(caseDir.resolve("issue-idempotency"))) {
                    artifact = entries
                            .filter(path -> path.getFileName().toString().endsWith(suffix))
                            .findFirst()
                            .orElseThrow();
                }
                byte[] corruption = corruptJson[index].getBytes(StandardCharsets.UTF_8);
                Files.write(artifact, corruption);
                IssueVcService service = mock(IssueVcService.class);
                IssueController controller = new IssueController(
                        service, new IssueIdempotencyStore(caseDir));

                assertUnavailable(() -> controller.issueVc(MODEL, request));

                verify(service, never()).issue(eq(MODEL), any());
                assertArrayEquals(corruption, Files.readAllBytes(artifact));
            }
        }
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
        return result(vcId, "issued");
    }

    private static IssueVcService.IssueResult result(String vcId, String status) throws Exception {
        return new IssueVcService.IssueResult(
                vcId,
                "did:omn:issuer",
                "tx-1",
                new ObjectMapper().readTree("{\"id\":\"" + vcId + "\"}"),
                status,
                "issued",
                "did:omn:user");
    }

    private static void assertUnavailable(ThrowingCall call) {
        ResponseStatusException error = assertThrows(ResponseStatusException.class, call::run);
        assertEquals(HttpStatus.CONFLICT, error.getStatusCode());
        assertEquals("issue_idempotency_unavailable", error.getReason());
    }

    @FunctionalInterface
    private interface ThrowingCall {
        void run() throws Exception;
    }
}
