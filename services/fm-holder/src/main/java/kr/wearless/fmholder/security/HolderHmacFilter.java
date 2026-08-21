package kr.wearless.fmholder.security;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ReadListener;
import jakarta.servlet.ServletException;
import jakarta.servlet.ServletInputStream;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletRequestWrapper;
import jakarta.servlet.http.HttpServletResponse;
import java.io.BufferedReader;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStreamReader;
import java.nio.ByteBuffer;
import java.nio.channels.SeekableByteChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.DirectoryStream;
import java.nio.file.FileAlreadyExistsException;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.security.GeneralSecurityException;
import java.security.MessageDigest;
import java.time.Clock;
import java.time.Instant;
import java.util.HexFormat;
import java.util.Locale;
import java.util.Objects;
import java.util.concurrent.atomic.AtomicLong;
import java.util.regex.Pattern;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

@Component
public final class HolderHmacFilter extends OncePerRequestFilter {
    static final long MAX_SKEW_SECONDS = 60;
    static final int MAX_BODY_BYTES = 256 * 1024;
    private static final long NONCE_RETENTION_SECONDS = 120;
    private static final long CLEANUP_INTERVAL_SECONDS = 60;
    private static final int MAX_CLEANUP_ENTRIES = 256;
    private static final Pattern NONCE_PATTERN = Pattern.compile("[A-Za-z0-9_-]{22,128}");
    private static final Pattern SHA256_HEX_PATTERN = Pattern.compile("[0-9a-f]{64}");
    private static final String UNAUTHORIZED = "{\"error\":\"unauthorized\"}";

    private final byte[] secret;
    private final Path nonceDir;
    private final Clock clock;
    private final AtomicLong nextCleanupAt = new AtomicLong(Long.MIN_VALUE);

    @Autowired
    public HolderHmacFilter(
            @Value("${holder.api-hmac-secret}") String secret,
            @Value("${holder.data-dir}") String dataDir) {
        this(secret, Path.of(dataDir), Clock.systemUTC());
    }

    HolderHmacFilter(String secret, Path dataDir, Clock clock) {
        if (secret == null || secret.isBlank()) {
            throw new IllegalArgumentException("holder.api-hmac-secret is required");
        }
        this.secret = secret.getBytes(StandardCharsets.UTF_8);
        this.nonceDir = Objects.requireNonNull(dataDir).resolve("auth-nonces");
        this.clock = Objects.requireNonNull(clock);
        try {
            Files.createDirectories(nonceDir);
            if (!Files.isDirectory(nonceDir, LinkOption.NOFOLLOW_LINKS)) {
                throw new IOException("nonce path is not a directory");
            }
        } catch (IOException error) {
            throw new IllegalStateException("cannot initialize Holder nonce directory", error);
        }
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        return "GET".equals(request.getMethod())
                && "/holder/health".equals(request.getRequestURI())
                && request.getQueryString() == null;
    }

    @Override
    protected void doFilterInternal(
            HttpServletRequest request,
            HttpServletResponse response,
            FilterChain filterChain) throws ServletException, IOException {
        String timestamp = request.getHeader("X-FM-Timestamp");
        String nonce = request.getHeader("X-FM-Nonce");
        String suppliedSignature = request.getHeader("X-FM-Signature");
        long now = Instant.now(clock).getEpochSecond();
        if (!validTimestamp(timestamp, now)
                || nonce == null
                || !NONCE_PATTERN.matcher(nonce).matches()
                || suppliedSignature == null
                || !SHA256_HEX_PATTERN.matcher(suppliedSignature).matches()) {
            unauthorized(response);
            return;
        }

        if (request.getContentLengthLong() > MAX_BODY_BYTES) {
            unauthorized(response);
            return;
        }
        byte[] body;
        try {
            body = request.getInputStream().readNBytes(MAX_BODY_BYTES + 1);
        } catch (IOException error) {
            unauthorized(response);
            return;
        }
        if (body.length > MAX_BODY_BYTES) {
            unauthorized(response);
            return;
        }

        String query = request.getQueryString();
        String target = query == null
                ? request.getRequestURI()
                : request.getRequestURI() + "?" + query;
        byte[] expected = hmac(secret, request.getMethod(), target, timestamp, nonce, body);
        byte[] supplied = HexFormat.of().parseHex(suppliedSignature);
        if (!MessageDigest.isEqual(expected, supplied)) {
            unauthorized(response);
            return;
        }

        cleanup(now);
        try {
            String digest = HexFormat.of().formatHex(sha256(nonce.getBytes(StandardCharsets.UTF_8)));
            Files.writeString(
                    nonceDir.resolve(digest),
                    timestamp,
                    StandardCharsets.UTF_8,
                    StandardOpenOption.CREATE_NEW,
                    StandardOpenOption.WRITE);
        } catch (FileAlreadyExistsException replay) {
            unauthorized(response);
            return;
        } catch (IOException error) {
            unauthorized(response);
            return;
        }

        filterChain.doFilter(new CachedBodyRequest(request, body), response);
    }

    static String signature(
            String secret,
            String method,
            String target,
            String timestamp,
            String nonce,
            byte[] body) {
        return HexFormat.of().formatHex(hmac(
                secret.getBytes(StandardCharsets.UTF_8), method, target, timestamp, nonce, body));
    }

    private static byte[] hmac(
            byte[] secret,
            String method,
            String target,
            String timestamp,
            String nonce,
            byte[] body) {
        String canonical = String.join(
                "\n",
                "v1",
                method.toUpperCase(Locale.ROOT),
                target,
                timestamp,
                nonce,
                HexFormat.of().formatHex(sha256(body)));
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(secret, "HmacSHA256"));
            return mac.doFinal(canonical.getBytes(StandardCharsets.UTF_8));
        } catch (GeneralSecurityException error) {
            throw new IllegalStateException("HmacSHA256 is unavailable", error);
        }
    }

    private static byte[] sha256(byte[] value) {
        try {
            return MessageDigest.getInstance("SHA-256").digest(value);
        } catch (GeneralSecurityException error) {
            throw new IllegalStateException("SHA-256 is unavailable", error);
        }
    }

    private static boolean validTimestamp(String value, long now) {
        if (value == null) {
            return false;
        }
        try {
            long timestamp = Long.parseLong(value);
            long skew = Math.subtractExact(now, timestamp);
            return skew >= -MAX_SKEW_SECONDS && skew <= MAX_SKEW_SECONDS;
        } catch (ArithmeticException | NumberFormatException error) {
            return false;
        }
    }

    private void cleanup(long now) {
        long eligibleAt = nextCleanupAt.get();
        if (now < eligibleAt || !nextCleanupAt.compareAndSet(eligibleAt, now + CLEANUP_INTERVAL_SECONDS)) {
            return;
        }
        long cutoff = now - NONCE_RETENTION_SECONDS;
        try (DirectoryStream<Path> entries = Files.newDirectoryStream(nonceDir)) {
            int inspected = 0;
            for (Path entry : entries) {
                if (++inspected > MAX_CLEANUP_ENTRIES) {
                    break;
                }
                String name = entry.getFileName().toString();
                if (SHA256_HEX_PATTERN.matcher(name).matches()
                        && Files.isRegularFile(entry, LinkOption.NOFOLLOW_LINKS)
                        && isStaleNonceFile(entry, cutoff)) {
                    Files.deleteIfExists(entry);
                }
            }
        } catch (IOException ignored) {
            // Authentication and replay protection remain fail-closed if housekeeping cannot run.
        }
    }

    private static boolean isStaleNonceFile(Path path, long cutoff) throws IOException {
        try (SeekableByteChannel channel = Files.newByteChannel(
                path, StandardOpenOption.READ, LinkOption.NOFOLLOW_LINKS)) {
            if (channel.size() > 20) {
                return false;
            }
            ByteBuffer value = ByteBuffer.allocate((int) channel.size());
            while (value.hasRemaining() && channel.read(value) != -1) {
                // Read the bounded timestamp file completely.
            }
            value.flip();
            try {
                return Long.parseLong(StandardCharsets.UTF_8.decode(value).toString()) < cutoff;
            } catch (NumberFormatException ignored) {
                return false;
            }
        }
    }

    private static void unauthorized(HttpServletResponse response) throws IOException {
        response.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
        response.setContentType("application/json");
        response.setCharacterEncoding(StandardCharsets.UTF_8.name());
        response.getWriter().write(UNAUTHORIZED);
    }

    private static final class CachedBodyRequest extends HttpServletRequestWrapper {
        private final byte[] body;

        CachedBodyRequest(HttpServletRequest request, byte[] body) {
            super(request);
            this.body = body;
        }

        @Override
        public ServletInputStream getInputStream() {
            ByteArrayInputStream input = new ByteArrayInputStream(body);
            return new ServletInputStream() {
                @Override
                public boolean isFinished() {
                    return input.available() == 0;
                }

                @Override
                public boolean isReady() {
                    return true;
                }

                @Override
                public void setReadListener(ReadListener listener) {
                    Objects.requireNonNull(listener);
                    try {
                        if (!isFinished()) {
                            listener.onDataAvailable();
                        }
                        if (isFinished()) {
                            listener.onAllDataRead();
                        }
                    } catch (IOException error) {
                        listener.onError(error);
                    }
                }

                @Override
                public int read() {
                    return input.read();
                }

                @Override
                public int read(byte[] bytes, int offset, int length) {
                    return input.read(bytes, offset, length);
                }
            };
        }

        @Override
        public BufferedReader getReader() {
            return new BufferedReader(new InputStreamReader(getInputStream(), StandardCharsets.UTF_8));
        }
    }
}
