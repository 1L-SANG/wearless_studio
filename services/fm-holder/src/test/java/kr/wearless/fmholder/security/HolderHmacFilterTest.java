package kr.wearless.fmholder.security;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletInputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.attribute.FileTime;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.mock.web.MockFilterChain;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class HolderHmacFilterTest {
    private static final String SECRET = "shared-secret";
    private static final long NOW = 1_800_000_000L;
    private static final Clock CLOCK = Clock.fixed(Instant.ofEpochSecond(NOW), ZoneOffset.UTC);
    private static final String NONCE = "nonce_value_123456789012";
    private static final byte[] BODY = "{\"vcId\":\"vc-1\"}".getBytes(StandardCharsets.UTF_8);
    private static final int MAX_BODY_BYTES = 256 * 1024;

    @Test
    void signatureMatchesTheIndependentProtocolVector() {
        assertEquals(
                "c58f4b35c96bafee167cbbb9edccbb55d23d1b47822d5d6f245199d2dee12e6b",
                HolderHmacFilter.signature(
                        SECRET,
                        "POST",
                        "/holder/vc/verify",
                        "1800000000",
                        "nonce_1234567890123456789012",
                        BODY));
    }

    @Test
    void validSignatureUsesRawQueryAndPreservesExactBody(@TempDir Path dir) throws Exception {
        HolderHmacFilter filter = new HolderHmacFilter(SECRET, dir, CLOCK);
        String query = "redirect=%2Fholder%2Fhealth&label=%ED%95%9C%EA%B8%80+test&tag=a&tag=b";
        MockHttpServletRequest request = signedRequest(
                SECRET, "POST", "/holder/vc/verify", query, Long.toString(NOW), NONCE, BODY);
        MockHttpServletResponse response = new MockHttpServletResponse();
        AtomicReference<byte[]> observed = new AtomicReference<>();

        filter.doFilter(request, response, (req, res) ->
                observed.set(req.getInputStream().readAllBytes()));

        assertEquals(200, response.getStatus());
        assertArrayEquals(BODY, observed.get());
    }

    @Test
    void invalidHeadersRejectChunkedBodyBeforeOpeningItsStream(@TempDir Path dir) throws Exception {
        HolderHmacFilter filter = new HolderHmacFilter(SECRET, dir, CLOCK);
        byte[] hugeBody = new byte[MAX_BODY_BYTES + 1];
        List<ChunkedRequest> requests = new ArrayList<>();
        requests.add(new ChunkedRequest("POST", "/holder/vc/verify", hugeBody));

        ChunkedRequest malformedTimestamp = new ChunkedRequest(
                "POST", "/holder/vc/verify", hugeBody);
        malformedTimestamp.addHeader("X-FM-Timestamp", "not-a-number");
        malformedTimestamp.addHeader("X-FM-Nonce", NONCE);
        malformedTimestamp.addHeader("X-FM-Signature", "0".repeat(64));
        requests.add(malformedTimestamp);

        ChunkedRequest malformedNonce = new ChunkedRequest(
                "POST", "/holder/vc/verify", hugeBody);
        malformedNonce.addHeader("X-FM-Timestamp", Long.toString(NOW));
        malformedNonce.addHeader("X-FM-Nonce", "short");
        malformedNonce.addHeader("X-FM-Signature", "0".repeat(64));
        requests.add(malformedNonce);

        ChunkedRequest malformedSignature = new ChunkedRequest(
                "POST", "/holder/vc/verify", hugeBody);
        malformedSignature.addHeader("X-FM-Timestamp", Long.toString(NOW));
        malformedSignature.addHeader("X-FM-Nonce", NONCE);
        malformedSignature.addHeader("X-FM-Signature", "not-hex");
        requests.add(malformedSignature);

        for (ChunkedRequest request : requests) {
            MockHttpServletResponse response = new MockHttpServletResponse();
            filter.doFilter(request, response, new MockFilterChain());
            assertUnauthorized(response);
            assertFalse(request.inputStreamOpened);
        }
    }

    @Test
    void signedChunkedBodyOverLimitIsUniformlyRejected(@TempDir Path dir) throws Exception {
        HolderHmacFilter filter = new HolderHmacFilter(SECRET, dir, CLOCK);
        byte[] body = new byte[MAX_BODY_BYTES + 1];
        ChunkedRequest request = new ChunkedRequest("POST", "/holder/vc/verify", body);
        addSignature(request, SECRET, "POST", "/holder/vc/verify", null,
                Long.toString(NOW), "oversize_nonce_123456789", body);
        MockHttpServletResponse response = new MockHttpServletResponse();
        AtomicInteger invoked = new AtomicInteger();

        filter.doFilter(request, response, (req, res) -> invoked.incrementAndGet());

        assertUnauthorized(response);
        assertTrue(request.inputStreamOpened);
        assertEquals(0, invoked.get());
    }

    @Test
    void signedBodyAtExactLimitPassesAndPreservesEveryByte(@TempDir Path dir) throws Exception {
        HolderHmacFilter filter = new HolderHmacFilter(SECRET, dir, CLOCK);
        byte[] body = new byte[MAX_BODY_BYTES];
        java.util.Arrays.fill(body, (byte) 0x5a);
        ChunkedRequest request = new ChunkedRequest("POST", "/holder/vc/verify", body);
        addSignature(request, SECRET, "POST", "/holder/vc/verify", null,
                Long.toString(NOW), "exact_limit_nonce_12345678", body);
        MockHttpServletResponse response = new MockHttpServletResponse();
        AtomicReference<byte[]> observed = new AtomicReference<>();

        filter.doFilter(request, response,
                (req, res) -> observed.set(req.getInputStream().readAllBytes()));

        assertEquals(200, response.getStatus());
        assertArrayEquals(body, observed.get());
    }

    @Test
    void duplicateNonceIsRejectedAfterFilterRecreation(@TempDir Path dir) throws Exception {
        MockHttpServletRequest first = signedRequest(
                SECRET, "POST", "/holder/models/m-1/wallet", null,
                Long.toString(NOW), NONCE, BODY);
        MockHttpServletResponse firstResponse = new MockHttpServletResponse();
        new HolderHmacFilter(SECRET, dir, CLOCK)
                .doFilter(first, firstResponse, new MockFilterChain());
        assertEquals(200, firstResponse.getStatus());

        MockHttpServletRequest replay = signedRequest(
                SECRET, "POST", "/holder/models/m-1/wallet", null,
                Long.toString(NOW), NONCE, BODY);
        MockHttpServletResponse replayResponse = new MockHttpServletResponse();
        new HolderHmacFilter(SECRET, dir, CLOCK)
                .doFilter(replay, replayResponse, new MockFilterChain());

        assertUnauthorized(replayResponse);
    }

    @Test
    void concurrentRequestsWithOneNonceHaveExactlyOneWinner(@TempDir Path dir) throws Exception {
        HolderHmacFilter filter = new HolderHmacFilter(SECRET, dir, CLOCK);
        AtomicInteger passed = new AtomicInteger();
        CountDownLatch start = new CountDownLatch(1);
        ExecutorService executor = Executors.newFixedThreadPool(16);
        List<Future<Integer>> results = new ArrayList<>();
        try {
            for (int i = 0; i < 16; i++) {
                results.add(executor.submit(() -> {
                    start.await();
                    MockHttpServletRequest request = signedRequest(
                            SECRET, "POST", "/holder/vc/issue", null,
                            Long.toString(NOW), NONCE, BODY);
                    MockHttpServletResponse response = new MockHttpServletResponse();
                    FilterChain chain = (req, res) -> passed.incrementAndGet();
                    filter.doFilter(request, response, chain);
                    return response.getStatus();
                }));
            }
            start.countDown();
            int unauthorized = 0;
            for (Future<Integer> result : results) {
                if (result.get() == 401) {
                    unauthorized++;
                }
            }
            assertEquals(1, passed.get());
            assertEquals(15, unauthorized);
        } finally {
            executor.shutdownNow();
        }
    }

    @Test
    void invalidRequestsReturnOneUniformResponse(@TempDir Path dir) throws Exception {
        HolderHmacFilter filter = new HolderHmacFilter(SECRET, dir, CLOCK);
        List<MockHttpServletRequest> requests = new ArrayList<>();

        MockHttpServletRequest missing = new MockHttpServletRequest("POST", "/holder/vc/verify");
        missing.setContent(BODY);
        requests.add(missing);
        requests.add(signedRequest(
                SECRET, "POST", "/holder/vc/verify", null,
                Long.toString(NOW - 61), "stale_nonce_123456789012", BODY));
        requests.add(signedRequest(
                SECRET, "POST", "/holder/vc/verify", null,
                "999999999999999999999999", "overflow_nonce_123456789", BODY));
        requests.add(signedRequest(
                SECRET, "POST", "/holder/vc/verify", null,
                "not-a-number", "malformed_time_123456789", BODY));
        requests.add(signedRequest(
                SECRET, "POST", "/holder/vc/verify", null,
                Long.toString(NOW), "short", BODY));
        requests.add(signedRequest(
                SECRET, "POST", "/holder/vc/verify", null,
                Long.toString(NOW), "invalid!nonce_value_12345", BODY));

        MockHttpServletRequest tampered = signedRequest(
                SECRET, "POST", "/holder/vc/verify", null,
                Long.toString(NOW), "tampered_nonce_123456789", BODY);
        tampered.setContent("{\"vcId\":\"vc-2\"}".getBytes(StandardCharsets.UTF_8));
        requests.add(tampered);

        MockHttpServletRequest uppercase = signedRequest(
                SECRET, "POST", "/holder/vc/verify", null,
                Long.toString(NOW), "uppercase_nonce_12345678", BODY);
        String lowerSignature = uppercase.getHeader("X-FM-Signature");
        uppercase.removeHeader("X-FM-Signature");
        uppercase.addHeader("X-FM-Signature",
                lowerSignature.toUpperCase(java.util.Locale.ROOT));
        requests.add(uppercase);

        MockHttpServletRequest malformedSignature = signedRequest(
                SECRET, "POST", "/holder/vc/verify", null,
                Long.toString(NOW), "malformed_sig_nonce_12345", BODY);
        malformedSignature.removeHeader("X-FM-Signature");
        malformedSignature.addHeader("X-FM-Signature", "g".repeat(64));
        requests.add(malformedSignature);

        for (MockHttpServletRequest request : requests) {
            MockHttpServletResponse response = new MockHttpServletResponse();
            AtomicInteger invoked = new AtomicInteger();
            filter.doFilter(request, response, (req, res) -> invoked.incrementAndGet());
            assertEquals(0, invoked.get());
            assertUnauthorized(response);
        }
    }

    @Test
    void onlyExactGetHealthWithoutQueryBypassesAuthentication(@TempDir Path dir) throws Exception {
        HolderHmacFilter filter = new HolderHmacFilter(SECRET, dir, CLOCK);
        AtomicInteger invoked = new AtomicInteger();
        MockHttpServletResponse exactResponse = new MockHttpServletResponse();
        filter.doFilter(new MockHttpServletRequest("GET", "/holder/health"), exactResponse,
                (req, res) -> invoked.incrementAndGet());

        MockHttpServletRequest queryVariant = new MockHttpServletRequest("GET", "/holder/health");
        queryVariant.setQueryString("probe=1");
        MockHttpServletResponse queryResponse = new MockHttpServletResponse();
        filter.doFilter(queryVariant, queryResponse, (req, res) -> invoked.incrementAndGet());

        MockHttpServletResponse postResponse = new MockHttpServletResponse();
        filter.doFilter(new MockHttpServletRequest("POST", "/holder/health"), postResponse,
                (req, res) -> invoked.incrementAndGet());

        assertEquals(1, invoked.get());
        assertEquals(200, exactResponse.getStatus());
        assertUnauthorized(queryResponse);
        assertUnauthorized(postResponse);
    }

    @Test
    void timestampBoundaryOfSixtySecondsIsAccepted(@TempDir Path dir) throws Exception {
        HolderHmacFilter filter = new HolderHmacFilter(SECRET, dir, CLOCK);
        MockHttpServletRequest request = signedRequest(
                SECRET, "POST", "/holder/vc/verify", null,
                Long.toString(NOW + 60), NONCE, BODY);
        MockHttpServletResponse response = new MockHttpServletResponse();

        filter.doFilter(request, response, new MockFilterChain());

        assertEquals(200, response.getStatus());
    }

    @Test
    void cleanupDeletesOnlyBoundedStaleDigestFilesAndNeverFollowsSymlinks(@TempDir Path dir)
            throws Exception {
        Path nonceDir = dir.resolve("auth-nonces");
        Files.createDirectories(nonceDir);
        FileTime stale = FileTime.from(Instant.ofEpochSecond(NOW - 121));
        FileTime recent = FileTime.from(Instant.ofEpochSecond(NOW - 120));
        for (int i = 0; i < 300; i++) {
            Path entry = nonceDir.resolve(String.format("%064x", i));
            Files.writeString(entry, Long.toString(NOW - 121));
            Files.setLastModifiedTime(entry, stale);
        }
        Path recentDigest = nonceDir.resolve("f".repeat(64));
        Files.writeString(recentDigest, Long.toString(NOW));
        Files.setLastModifiedTime(recentDigest, recent);
        Path unrelated = nonceDir.resolve("do-not-delete.txt");
        Files.writeString(unrelated, "owned by another process");
        Files.setLastModifiedTime(unrelated, stale);
        Path target = dir.resolve("outside-target");
        Files.writeString(target, "keep");
        Path symlink = nonceDir.resolve("e".repeat(64));
        Files.createSymbolicLink(symlink, target);

        HolderHmacFilter filter = new HolderHmacFilter(SECRET, dir, CLOCK);
        MockHttpServletResponse response = new MockHttpServletResponse();
        filter.doFilter(signedRequest(
                        SECRET, "POST", "/holder/vc/verify", null,
                        Long.toString(NOW), NONCE, BODY),
                response, new MockFilterChain());

        long staleRemaining;
        try (var entries = Files.list(nonceDir)) {
            staleRemaining = entries
                    .filter(path -> path.getFileName().toString().matches("[0-9a-f]{64}"))
                    .filter(path -> Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS))
                    .filter(path -> {
                        try {
                            return Files.getLastModifiedTime(path, LinkOption.NOFOLLOW_LINKS)
                                    .equals(stale);
                        } catch (IOException error) {
                            throw new RuntimeException(error);
                        }
                    })
                    .count();
        }
        assertEquals(200, response.getStatus());
        assertTrue(staleRemaining > 0, "cleanup must cap work per request");
        assertTrue(staleRemaining < 300, "cleanup must remove stale nonce files");
        assertTrue(Files.exists(recentDigest, LinkOption.NOFOLLOW_LINKS));
        assertTrue(Files.exists(unrelated, LinkOption.NOFOLLOW_LINKS));
        assertTrue(Files.isSymbolicLink(symlink));
        assertTrue(Files.exists(target, LinkOption.NOFOLLOW_LINKS));
        assertEquals("keep", Files.readString(target));
    }

    private static MockHttpServletRequest signedRequest(
            String secret,
            String method,
            String path,
            String query,
            String timestamp,
            String nonce,
            byte[] body) {
        MockHttpServletRequest request = new MockHttpServletRequest(method, path);
        request.setQueryString(query);
        request.setContent(body);
        addSignature(request, secret, method, path, query, timestamp, nonce, body);
        return request;
    }

    private static void addSignature(
            MockHttpServletRequest request,
            String secret,
            String method,
            String path,
            String query,
            String timestamp,
            String nonce,
            byte[] body) {
        String target = query == null ? path : path + "?" + query;
        request.addHeader("X-FM-Timestamp", timestamp);
        request.addHeader("X-FM-Nonce", nonce);
        request.addHeader("X-FM-Signature",
                HolderHmacFilter.signature(secret, method, target, timestamp, nonce, body));
    }

    private static void assertUnauthorized(MockHttpServletResponse response) throws Exception {
        assertEquals(401, response.getStatus());
        assertEquals("application/json;charset=UTF-8", response.getContentType());
        assertEquals("{\"error\":\"unauthorized\"}", response.getContentAsString());
        assertFalse(response.getContentAsString().contains(SECRET));
        assertFalse(response.getContentAsString().contains(NONCE));
    }

    private static final class ChunkedRequest extends MockHttpServletRequest {
        private boolean inputStreamOpened;

        ChunkedRequest(String method, String path, byte[] body) {
            super(method, path);
            setContent(body);
        }

        @Override
        public int getContentLength() {
            return -1;
        }

        @Override
        public long getContentLengthLong() {
            return -1;
        }

        @Override
        public ServletInputStream getInputStream() {
            inputStreamOpened = true;
            return super.getInputStream();
        }
    }
}
