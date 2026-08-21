package kr.wearless.fmholder.api;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermission;
import java.util.List;
import java.util.Set;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import kr.wearless.fmholder.protocol.IssueVcDtos;
import kr.wearless.fmholder.protocol.IssueVcService;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class IssueIdempotencyStoreTest {
    private static final String KEY = "fm-license:123e4567-e89b-12d3-a456-426614174000";
    private static final String MODEL = "model-1";

    @Test
    void firstIssuePersistsAndRestartReplaysWithoutCallingFlowBAgain(@TempDir Path dataDir)
            throws Exception {
        AtomicInteger calls = new AtomicInteger();
        IssueVcDtos.IssueRequest request = faceLicense(KEY, "allowed-a");
        IssueVcService.IssueResult issued = result("vc-1");

        IssueVcService.IssueResult first = new IssueIdempotencyStore(dataDir)
                .execute(MODEL, request, () -> {
                    calls.incrementAndGet();
                    return issued;
                });
        IssueVcService.IssueResult replayed = new IssueIdempotencyStore(dataDir)
                .execute(MODEL, request, () -> {
                    calls.incrementAndGet();
                    return result("vc-duplicate");
                });

        assertEquals("vc-1", first.vcId());
        assertEquals("vc-1", replayed.vcId());
        assertEquals(1, calls.get());
    }

    @Test
    void concurrentSameKeyAllowsOnlyOneFlowBCall(@TempDir Path dataDir) throws Exception {
        IssueIdempotencyStore store = new IssueIdempotencyStore(dataDir);
        IssueVcDtos.IssueRequest request = faceLicense(KEY, "allowed-a");
        AtomicInteger calls = new AtomicInteger();
        CountDownLatch entered = new CountDownLatch(1);
        CountDownLatch release = new CountDownLatch(1);

        try (var executor = Executors.newFixedThreadPool(2)) {
            var first = executor.submit(() -> store.execute(MODEL, request, () -> {
                calls.incrementAndGet();
                entered.countDown();
                assertTrue(release.await(5, TimeUnit.SECONDS));
                return result("vc-1");
            }));
            assertTrue(entered.await(5, TimeUnit.SECONDS));

            var second = executor.submit(() -> assertThrows(
                    IssueIdempotencyStore.UnavailableException.class,
                    () -> store.execute(MODEL, request, () -> {
                        calls.incrementAndGet();
                        return result("vc-duplicate");
                    })));

            second.get(5, TimeUnit.SECONDS);
            release.countDown();
            assertEquals("vc-1", first.get(5, TimeUnit.SECONDS).vcId());
        }

        assertEquals(1, calls.get());
    }

    @Test
    void sameKeyRejectsDifferentModelOrSemanticBody(@TempDir Path dataDir) throws Exception {
        IssueIdempotencyStore store = new IssueIdempotencyStore(dataDir);
        store.execute(MODEL, faceLicense(KEY, "allowed-a"), () -> result("vc-1"));
        AtomicInteger calls = new AtomicInteger();

        assertThrows(IssueIdempotencyStore.UnavailableException.class,
                () -> store.execute("model-2", faceLicense(KEY, "allowed-a"), () -> {
                    calls.incrementAndGet();
                    return result("vc-2");
                }));
        assertThrows(IssueIdempotencyStore.UnavailableException.class,
                () -> store.execute(MODEL, faceLicense(KEY, "allowed-b"), () -> {
                    calls.incrementAndGet();
                    return result("vc-3");
                }));

        assertEquals(0, calls.get());
    }

    @Test
    void unresolvedIntentAfterFailureBlocksRestartRetry(@TempDir Path dataDir) throws Exception {
        AtomicInteger calls = new AtomicInteger();
        IssueVcDtos.IssueRequest request = faceLicense(KEY, "allowed-a");
        IssueIdempotencyStore first = new IssueIdempotencyStore(dataDir);

        assertThrows(IllegalStateException.class, () -> first.execute(MODEL, request, () -> {
            calls.incrementAndGet();
            throw new IllegalStateException("simulated Flow B crash");
        }));
        assertThrows(IssueIdempotencyStore.UnavailableException.class,
                () -> new IssueIdempotencyStore(dataDir).execute(MODEL, request, () -> {
                    calls.incrementAndGet();
                    return result("vc-duplicate");
                }));

        assertEquals(1, calls.get());
    }

    @Test
    void storageUsesHashedNamesOwnerOnlyPermissionsAndNoRawRequest(@TempDir Path dataDir)
            throws Exception {
        String claimMarker = "sensitive-full-request-marker";
        new IssueIdempotencyStore(dataDir)
                .execute(MODEL, faceLicense(KEY, claimMarker), () -> result("vc-1"));

        Path storeDir = dataDir.resolve("issue-idempotency");
        List<Path> entries;
        try (var stream = Files.list(storeDir)) {
            entries = stream.toList();
        }
        assertFalse(entries.isEmpty());
        for (Path entry : entries) {
            String name = entry.getFileName().toString();
            assertTrue(name.matches("[0-9a-f]{64}\\.(lock|intent|result)"), name);
            assertFalse(name.contains(KEY));
            String content = Files.readString(entry, StandardCharsets.UTF_8);
            assertFalse(content.contains(KEY));
            assertFalse(content.contains(claimMarker));
        }

        if (Files.getFileStore(storeDir).supportsFileAttributeView("posix")) {
            assertEquals(Set.of(
                    PosixFilePermission.OWNER_READ,
                    PosixFilePermission.OWNER_WRITE,
                    PosixFilePermission.OWNER_EXECUTE),
                    Files.getPosixFilePermissions(storeDir));
            for (Path entry : entries) {
                assertEquals(Set.of(
                        PosixFilePermission.OWNER_READ,
                        PosixFilePermission.OWNER_WRITE),
                        Files.getPosixFilePermissions(entry));
            }
        }
    }

    private static IssueVcDtos.IssueRequest faceLicense(String key, String allowedUse) {
        return new IssueVcDtos.IssueRequest(
                "facelicense",
                new IssueVcDtos.Claims(
                        allowedUse,
                        "forbidden",
                        7,
                        "2099-12-31",
                        "sha256:opaque",
                        "model"),
                key);
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
