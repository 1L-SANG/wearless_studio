package kr.wearless.fmholder.security;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.boot.autoconfigure.AutoConfigurations;
import org.springframework.boot.autoconfigure.context.PropertyPlaceholderAutoConfiguration;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Import;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class HolderHmacFilterMissingSecretTest {
    private static final String REQUIRED_MAPPING =
            "api-hmac-secret: ${FM_HOLDER_HMAC_SECRET}";

    @Test
    void applicationConfigRequiresHmacEnvironmentWithoutFallback() throws Exception {
        assertRequiredHmacMapping(Files.readString(
                Path.of("src/main/resources/application.yml")));
    }

    @Test
    void applicationConfigContractRejectsInsecureFallback() throws Exception {
        String insecure = Files.readString(Path.of("src/main/resources/application.yml"))
                .replace(
                        "${FM_HOLDER_HMAC_SECRET}",
                        "${FM_HOLDER_HMAC_SECRET:insecure}");

        assertThrows(AssertionError.class, () -> assertRequiredHmacMapping(insecure));
    }

    @Test
    void missingHmacSecretPreventsFilterContextStartup(@TempDir Path dataDir) {
        new ApplicationContextRunner()
                .withConfiguration(AutoConfigurations.of(
                        PropertyPlaceholderAutoConfiguration.class))
                .withUserConfiguration(FilterOnlyConfiguration.class)
                .withPropertyValues("holder.data-dir=" + dataDir)
                .run(context -> {
                    assertNotNull(context.getStartupFailure());
                    assertTrue(stackTrace(context.getStartupFailure())
                            .contains("holder.api-hmac-secret"));
                });
    }

    private static String stackTrace(Throwable error) {
        StringBuilder result = new StringBuilder();
        while (error != null) {
            result.append(error).append('\n');
            error = error.getCause();
        }
        return result.toString();
    }

    private static void assertRequiredHmacMapping(String yaml) {
        List<String> mappings = yaml.lines()
                .map(String::strip)
                .filter(line -> line.startsWith("api-hmac-secret:"))
                .toList();
        assertEquals(List.of(REQUIRED_MAPPING), mappings);
    }

    @Configuration(proxyBeanMethods = false)
    @Import(HolderHmacFilter.class)
    static class FilterOnlyConfiguration {
    }
}
