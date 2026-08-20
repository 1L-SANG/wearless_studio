package org.omnione.did.base.datamodel.data;

import org.junit.jupiter.api.Test;

import java.nio.file.Path;

import static org.junit.jupiter.api.Assertions.assertTrue;

class OpenDidDataPackageProvenanceTest {

    @Test
    void proofClassIsCompiledFromVendoredHolderSource() throws Exception {
        Path source = Path.of(Proof.class.getProtectionDomain()
                .getCodeSource()
                .getLocation()
                .toURI());

        assertTrue(source.toString().contains("build/classes/java/main"),
                () -> "Proof must come from holder source classes, not an SDK or TA jar: " + source);
    }
}
