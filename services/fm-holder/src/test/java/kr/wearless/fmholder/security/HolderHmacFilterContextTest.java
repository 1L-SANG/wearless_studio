package kr.wearless.fmholder.security;

import java.nio.file.Path;
import kr.wearless.fmholder.FmHolderApplication;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;

import static org.junit.jupiter.api.Assertions.assertEquals;

@SpringBootTest(
        classes = FmHolderApplication.class,
        webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT,
        properties = "logging.level.kr.wearless.fmholder.protocol.CasKeyService=OFF")
class HolderHmacFilterContextTest {
    private static final String SECRET = "context-secret";

    @TempDir
    static Path dataDir;

    @DynamicPropertySource
    static void holderProperties(DynamicPropertyRegistry registry) {
        registry.add("FM_HOLDER_HMAC_SECRET", () -> SECRET);
        registry.add("holder.data-dir", dataDir::toString);
        registry.add("opendid.wallet-provider.file-path",
                () -> dataDir.resolve("wallet-provider.wallet").toString());
        registry.add("opendid.wallet-provider.password", () -> "test-wallet-password");
        registry.add("opendid.cas-provider.file-path",
                () -> dataDir.resolve("cas-provider.wallet").toString());
        registry.add("opendid.cas-provider.password", () -> "test-cas-password");
    }

    private final TestRestTemplate rest;

    @Autowired
    HolderHmacFilterContextTest(TestRestTemplate rest) {
        this.rest = rest;
    }

    @Test
    void configuredSpringHttpContextRegistersFilterOnProtectedEndpoint() {
        ResponseEntity<String> response = rest.getForEntity(
                "/holder/models/context-model", String.class);

        assertEquals(HttpStatus.UNAUTHORIZED, response.getStatusCode());
        assertEquals("{\"error\":\"unauthorized\"}", response.getBody());
    }

}
