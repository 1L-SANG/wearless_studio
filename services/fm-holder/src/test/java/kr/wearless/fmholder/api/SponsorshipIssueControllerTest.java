package kr.wearless.fmholder.api;

import com.fasterxml.jackson.databind.MapperFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.json.JsonMapper;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.HexFormat;
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
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class SponsorshipIssueControllerTest {
    private static final String MODEL = "model-1";
    private static final String ID = "123e4567-e89b-12d3-a456-426614174000";
    private static final String SP_KEY = "fm-sponsorship:" + ID;
    private static final String FL_KEY = "fm-license:" + ID;

    @Test
    void sponsorshipRequiresItsOwnKeyFormatBeforeService(@TempDir Path dataDir) throws Exception {
        IssueVcService service = mock(IssueVcService.class);
        IssueController controller = new IssueController(service, new IssueIdempotencyStore(dataDir));

        for (String key : new String[] {
                null, "", FL_KEY, "fm-sponsorship:not-a-uuid",
                "FM-SPONSORSHIP:" + ID, "fm-sponsorship:" + ID.toUpperCase()}) {
            ResponseStatusException error = assertThrows(ResponseStatusException.class,
                    () -> controller.issueVc(MODEL, IssueVcDtos.IssueRequest.sponsorship(claims("v1"), key)));
            assertEquals(HttpStatus.BAD_REQUEST, error.getStatusCode());
            assertEquals("invalid_idempotency_key", error.getReason());
        }
        // 라이선스 플랜에 협찬 키를 실어도 같은 거절.
        ResponseStatusException error = assertThrows(ResponseStatusException.class,
                () -> controller.issueVc(MODEL, new IssueVcDtos.IssueRequest("facelicense-v2", licenseClaims(), SP_KEY)));
        assertEquals("invalid_idempotency_key", error.getReason());
        verify(service, never()).issue(eq(MODEL), any());
    }

    @Test
    void sameKeySameClaimsReplaysOneIssuanceWithSameShape(@TempDir Path dataDir) throws Exception {
        IssueVcService service = mock(IssueVcService.class);
        IssueVcDtos.IssueRequest request = IssueVcDtos.IssueRequest.sponsorship(claims("v1"), SP_KEY);
        when(service.issue(MODEL, request)).thenReturn(result("vc-sp-1"));

        IssueVcService.IssueResult first = new IssueController(service, new IssueIdempotencyStore(dataDir))
                .issueVc(MODEL, request);
        IssueVcService.IssueResult replay = new IssueController(service, new IssueIdempotencyStore(dataDir))
                .issueVc(MODEL, IssueVcDtos.IssueRequest.sponsorship(claims("v1"), SP_KEY));

        assertEquals("vc-sp-1", first.vcId());
        assertEquals("did:omn:user", first.userDid());
        assertEquals(first, replay);
        verify(service, times(1)).issue(eq(MODEL), any());
    }

    @Test
    void sameKeyDifferentClaimsConflicts(@TempDir Path dataDir) throws Exception {
        IssueVcService service = mock(IssueVcService.class);
        IssueController controller = new IssueController(service, new IssueIdempotencyStore(dataDir));
        IssueVcDtos.IssueRequest first = IssueVcDtos.IssueRequest.sponsorship(claims("v1"), SP_KEY);
        IssueVcDtos.IssueRequest changed = IssueVcDtos.IssueRequest.sponsorship(claims("v2"), SP_KEY);
        when(service.issue(MODEL, first)).thenReturn(result("vc-sp-1"));

        controller.issueVc(MODEL, first);
        ResponseStatusException error = assertThrows(ResponseStatusException.class,
                () -> controller.issueVc(MODEL, changed));

        assertEquals(HttpStatus.CONFLICT, error.getStatusCode());
        assertEquals("issue_idempotency_unavailable", error.getReason());
        verify(service, never()).issue(MODEL, changed);
    }

    @Test
    void sameKeyAcrossPlansConflictsInStore(@TempDir Path dataDir) throws Exception {
        // 키 형식이 플랜별이라 컨트롤러에선 못 섞이지만, 저장소 자체도 plan 을 바인딩에 넣는다.
        IssueIdempotencyStore store = new IssueIdempotencyStore(dataDir);
        store.execute(MODEL, IssueVcDtos.IssueRequest.sponsorship(claims("v1"), SP_KEY), () -> result("vc-sp-1"));

        IssueVcDtos.IssueRequest otherPlan = new IssueVcDtos.IssueRequest("facelicense-v2", licenseClaims(), SP_KEY);
        assertThrows(IssueIdempotencyStore.UnavailableException.class,
                () -> store.execute(MODEL, otherPlan, () -> result("vc-fl-1")));
    }

    @Test
    void licenseDigestBytesAreUnchangedSoExistingResultsStillReplay(@TempDir Path dataDir) throws Exception {
        IssueVcDtos.IssueRequest license = new IssueVcDtos.IssueRequest("facelicense-v2", licenseClaims(), FL_KEY);
        new IssueIdempotencyStore(dataDir).execute(MODEL, license, () -> result("vc-fl-1"));

        // 변경 전 SemanticRequest(String plan, Claims claims) 와 같은 직렬화 → 같은 digest.
        ObjectMapper mapper = JsonMapper.builder().enable(MapperFeature.SORT_PROPERTIES_ALPHABETICALLY).build();
        String expected = sha256(mapper.writeValueAsBytes(new LegacySemantic("facelicense-v2", licenseClaims())));
        Path resultFile;
        try (var entries = Files.list(dataDir.resolve("issue-idempotency"))) {
            resultFile = entries.filter(p -> p.toString().endsWith(".result")).findFirst().orElseThrow();
        }
        String stored = mapper.readTree(Files.readAllBytes(resultFile))
                .get("binding").get("requestDigest").asText();
        assertEquals(expected, stored);
    }

    private record LegacySemantic(String plan, IssueVcDtos.Claims claims) {}

    private static String sha256(byte[] value) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value));
    }

    private static IssueVcDtos.SponsorshipClaims claims(String docVersion) {
        return new IssueVcDtos.SponsorshipClaims("did:omn:user", ID, docVersion,
                "a".repeat(64), "b".repeat(64), "2026-09-25T00:00:00Z");
    }

    private static IssueVcDtos.Claims licenseClaims() {
        return new IssueVcDtos.Claims("did:omn:user", ID, "2026-09-11T00:00:00Z", "sha256:opaque", "v1", "v1.1");
    }

    private static IssueVcService.IssueResult result(String vcId) throws Exception {
        return new IssueVcService.IssueResult(vcId, "did:omn:issuer", "tx-1",
                new ObjectMapper().readTree("{\"id\":\"" + vcId + "\"}"), "issued", "issued", "did:omn:user");
    }
}
