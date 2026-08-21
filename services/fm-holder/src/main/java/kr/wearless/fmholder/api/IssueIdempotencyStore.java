package kr.wearless.fmholder.api;

import com.fasterxml.jackson.databind.MapperFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.json.JsonMapper;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.channels.FileLock;
import java.nio.channels.OverlappingFileLockException;
import java.nio.charset.StandardCharsets;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.OpenOption;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.StandardOpenOption;
import java.nio.file.attribute.FileAttribute;
import java.nio.file.attribute.PosixFilePermission;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.GeneralSecurityException;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.Locale;
import java.util.Objects;
import java.util.Set;
import java.util.concurrent.Callable;
import kr.wearless.fmholder.protocol.IssueVcDtos;
import kr.wearless.fmholder.protocol.IssueVcService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

@Component
public final class IssueIdempotencyStore {
    private static final Set<PosixFilePermission> DIRECTORY_PERMISSIONS = Set.of(
            PosixFilePermission.OWNER_READ,
            PosixFilePermission.OWNER_WRITE,
            PosixFilePermission.OWNER_EXECUTE);
    private static final Set<PosixFilePermission> FILE_PERMISSIONS = Set.of(
            PosixFilePermission.OWNER_READ,
            PosixFilePermission.OWNER_WRITE);
    private static final FileAttribute<Set<PosixFilePermission>> FILE_ATTRIBUTE =
            PosixFilePermissions.asFileAttribute(FILE_PERMISSIONS);

    private final Path directory;
    private final Runnable directoryForce;
    private final ObjectMapper mapper = JsonMapper.builder()
            .enable(MapperFeature.SORT_PROPERTIES_ALPHABETICALLY)
            .build();

    @Autowired
    public IssueIdempotencyStore(@Value("${holder.data-dir}") String dataDir) {
        this(Path.of(dataDir));
    }

    IssueIdempotencyStore(Path dataDir) {
        this(dataDir, null);
    }

    IssueIdempotencyStore(Path dataDir, Runnable directoryForce) {
        directory = Objects.requireNonNull(dataDir).resolve("issue-idempotency");
        this.directoryForce = directoryForce == null ? this::forceDirectory : directoryForce;
        try {
            createDirectory(directory);
        } catch (IOException error) {
            throw new IllegalStateException("cannot initialize issue idempotency storage", error);
        }
    }

    IssueVcService.IssueResult execute(
            String modelId,
            IssueVcDtos.IssueRequest request,
            Callable<IssueVcService.IssueResult> action) throws Exception {
        Objects.requireNonNull(modelId);
        Objects.requireNonNull(request);
        Objects.requireNonNull(action);

        String fileKey = sha256Hex(request.idempotencyKey().getBytes(StandardCharsets.UTF_8));
        Path lockPath = directory.resolve(fileKey + ".lock");
        Path intentPath = directory.resolve(fileKey + ".intent");
        Path resultPath = directory.resolve(fileKey + ".result");
        Binding expected = new Binding(modelId, semanticDigest(request));

        try (HeldLock ignored = acquire(lockPath)) {
            if (Files.exists(resultPath, LinkOption.NOFOLLOW_LINKS)) {
                StoredResult stored = read(resultPath, StoredResult.class);
                if (!stored.binding().equals(expected) || !isIssued(stored.result())) {
                    throw unavailable();
                }
                return stored.result();
            }
            if (Files.exists(intentPath, LinkOption.NOFOLLOW_LINKS)) {
                Binding stored = read(intentPath, Binding.class);
                if (!stored.equals(expected)) {
                    throw unavailable();
                }
                throw unavailable();
            }

            persistAtomically(intentPath, mapper.writeValueAsBytes(expected), true);
            IssueVcService.IssueResult result = action.call();
            if (!isIssued(result)) {
                throw unavailable();
            }
            persistAtomically(
                    resultPath,
                    mapper.writeValueAsBytes(new StoredResult(expected, result)),
                    false);
            return result;
        }
    }

    private String semanticDigest(IssueVcDtos.IssueRequest request) {
        String plan = request.plan() == null
                ? "mdl"
                : request.plan().trim().toLowerCase(Locale.ROOT);
        try {
            return sha256Hex(mapper.writeValueAsBytes(new SemanticRequest(plan, request.claims())));
        } catch (IOException error) {
            throw unavailable();
        }
    }

    private static boolean isIssued(IssueVcService.IssueResult result) {
        return result != null
                && "issued".equals(result.status())
                && result.vcId() != null
                && !result.vcId().trim().isEmpty();
    }

    private HeldLock acquire(Path path) {
        FileChannel channel = null;
        try {
            channel = open(path, Set.of(
                    StandardOpenOption.CREATE,
                    StandardOpenOption.WRITE,
                    LinkOption.NOFOLLOW_LINKS));
            secureFile(path);
            FileLock lock = channel.tryLock();
            if (lock == null) {
                channel.close();
                throw unavailable();
            }
            return new HeldLock(channel, lock);
        } catch (IOException | OverlappingFileLockException error) {
            if (channel != null) {
                try {
                    channel.close();
                } catch (IOException ignored) {
                    // No request data is exposed by close failures.
                }
            }
            throw unavailable();
        }
    }

    private <T> T read(Path path, Class<T> type) {
        try {
            if (!Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS)) {
                throw unavailable();
            }
            return mapper.readValue(Files.readAllBytes(path), type);
        } catch (IOException error) {
            throw unavailable();
        }
    }

    private void persistAtomically(Path target, byte[] value, boolean replaceSafeTemp) {
        Path temporary = target.resolveSibling(target.getFileName() + ".tmp");
        try {
            if (replaceSafeTemp) {
                Files.deleteIfExists(temporary);
            }
            try (FileChannel channel = open(temporary, Set.of(
                    StandardOpenOption.CREATE_NEW,
                    StandardOpenOption.WRITE,
                    LinkOption.NOFOLLOW_LINKS))) {
                ByteBuffer bytes = ByteBuffer.wrap(value);
                while (bytes.hasRemaining()) {
                    channel.write(bytes);
                }
                channel.force(true);
            }
            Files.move(temporary, target, StandardCopyOption.ATOMIC_MOVE);
            secureFile(target);
            try {
                directoryForce.run();
            } catch (RuntimeException error) {
                throw unavailable();
            }
        } catch (AtomicMoveNotSupportedException error) {
            throw unavailable();
        } catch (IOException error) {
            throw unavailable();
        }
    }

    private static FileChannel open(Path path, Set<OpenOption> options) throws IOException {
        try {
            return FileChannel.open(path, options, FILE_ATTRIBUTE);
        } catch (UnsupportedOperationException error) {
            FileChannel channel = FileChannel.open(path, options);
            secureFile(path);
            return channel;
        }
    }

    private static void createDirectory(Path path) throws IOException {
        try {
            Files.createDirectories(
                    path,
                    PosixFilePermissions.asFileAttribute(DIRECTORY_PERMISSIONS));
            Files.setPosixFilePermissions(path, DIRECTORY_PERMISSIONS);
        } catch (UnsupportedOperationException error) {
            Files.createDirectories(path);
        }
        if (!Files.isDirectory(path, LinkOption.NOFOLLOW_LINKS)) {
            throw new IOException("issue idempotency path is not a directory");
        }
    }

    private static void secureFile(Path path) throws IOException {
        try {
            Files.setPosixFilePermissions(path, FILE_PERMISSIONS);
        } catch (UnsupportedOperationException ignored) {
            // Non-POSIX platforms do not expose owner-only mode bits.
        }
    }

    private void forceDirectory() {
        try (FileChannel channel = FileChannel.open(directory, StandardOpenOption.READ)) {
            channel.force(true);
        } catch (IOException | UnsupportedOperationException error) {
            throw unavailable();
        }
    }

    private static String sha256Hex(byte[] value) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value));
        } catch (GeneralSecurityException error) {
            throw new IllegalStateException("SHA-256 is unavailable", error);
        }
    }

    private static UnavailableException unavailable() {
        return new UnavailableException();
    }

    static final class UnavailableException extends RuntimeException {
        private UnavailableException() {
            super("issue_idempotency_unavailable", null, false, false);
        }
    }

    private record SemanticRequest(String plan, IssueVcDtos.Claims claims) {}

    private record Binding(String modelId, String requestDigest) {}

    private record StoredResult(Binding binding, IssueVcService.IssueResult result) {}

    private record HeldLock(FileChannel channel, FileLock lock) implements AutoCloseable {
        @Override
        public void close() {
            try {
                lock.release();
            } catch (IOException ignored) {
                // Closing the channel below also releases the lock.
            } finally {
                try {
                    channel.close();
                } catch (IOException ignored) {
                    // The durable result, when present, is already forced and moved.
                }
            }
        }
    }
}
