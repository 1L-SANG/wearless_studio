package kr.wearless.fmholder.security;

import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.boot.autoconfigure.AutoConfigurations;
import org.springframework.boot.autoconfigure.context.PropertyPlaceholderAutoConfiguration;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Import;

import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class HolderHmacFilterMissingSecretTest {
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

    @Configuration(proxyBeanMethods = false)
    @Import(HolderHmacFilter.class)
    static class FilterOnlyConfiguration {
    }
}
