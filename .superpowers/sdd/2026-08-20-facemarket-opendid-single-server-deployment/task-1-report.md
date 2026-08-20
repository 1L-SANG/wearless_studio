# Task 1 report — Holder clean build recovery

Status: DONE

## What changed

- Vendored the approved 15-file `org.omnione.did.base.datamodel.data` import closure into `services/fm-holder/src/main/java`.
- Fixed `services/fm-holder/.gitignore` from `data/` to `/data/` so runtime holder state stays ignored without hiding the Java `datamodel/data` package.
- Added `services/fm-holder/NOTICE` with upstream source, tag, commit, license, vendored file list, and the ZKP exclusion rationale.
- Added `OpenDidDataPackageProvenanceTest` to prove `Proof` resolves from holder compiled source classes, not an SDK/TA jar.

## Source provenance

- Official source: `https://github.com/OmniOneID/did-ta-server`
- Tag: `v2.0.0`
- Commit: `4bdf24d12adaf6abb46df1b713f0396426f9a054`
- License: Apache-2.0
- Vendored files:
  - `AccE2e.java`
  - `AccEcdh.java`
  - `AttestedAppInfo.java`
  - `AttestedDidDoc.java`
  - `Candidate.java`
  - `DidAuth.java`
  - `E2e.java`
  - `EcdhReqData.java`
  - `Proof.java`
  - `Provider.java`
  - `ReqRevokeVc.java`
  - `ServerTokenSeed.java`
  - `SignedDidDoc.java`
  - `SignedWalletInfo.java`
  - `Wallet.java`

All vendored files compare byte-for-byte equal to the pinned upstream files.

## Scope note

The original “full non-ZKP package” request was narrowed by the official-source ruling in `progress.md`: copy the exact 15-file import closure only. Copying all 31 top-level files pulls `VcPlan` into `data.zkp`; copying the full tree would require unused ZKP SDK types. No new dependency, TA fat JAR, or manual boot classpath workaround was added.

## Verification

- RED: `./gradlew clean test` failed at `compileJava` with 61 missing `org.omnione.did.base.datamodel.data` errors before vendoring.
- GREEN: `JAVA_HOME=/opt/homebrew/Cellar/openjdk@21/21.0.11/libexec/openjdk.jdk/Contents/Home ./gradlew clean test` → `BUILD SUCCESSFUL`.
- JAR: `JAVA_HOME=/opt/homebrew/Cellar/openjdk@21/21.0.11/libexec/openjdk.jdk/Contents/Home ./gradlew bootJar` → `BUILD SUCCESSFUL`.
- JAR contents: `jar tf build/libs/fm-holder-0.1.0.jar | rg 'BOOT-INF/classes/kr/wearless/fmholder|BOOT-INF/classes/org/omnione/did/base/datamodel/data/Proof.class'` found holder classes and `BOOT-INF/classes/org/omnione/did/base/datamodel/data/Proof.class`.
- Tracking: after anchoring `.gitignore`, `services/fm-holder/src/main/java/org/omnione/did/base/datamodel/data/` appears as normal untracked source, not ignored.

## Concerns

- Gradle run without Java 21 uses the shell's Java 25 and fails in Lombok annotation processing (`TypeTag.UNKNOWN`). Task verification was run with Java 21 as required.

## Fix round 1 — literal Gradle command uses Java 21

Reviewer finding: `services/fm-holder/build.gradle` used only `sourceCompatibility`, so the literal brief command still ran Gradle under the shell's Java 25.

Changes:

- Replaced `java { sourceCompatibility = '21' }` with a Java 21 Gradle toolchain pin.
- Added `services/fm-holder/gradle.properties` with `org.gradle.java.home=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home` so Gradle 8.8 itself starts on installed Java 21 before buildscript/toolchain resolution.

Evidence:

- RED: `./gradlew clean test` → `BUILD FAILED`; `compileJava` failed with `java.lang.ExceptionInInitializerError` from Lombok/JDK 25.
- Intermediate: after only the toolchain pin, `./gradlew clean test` → `BUILD FAILED`; Gradle 8.8 failed before compile with `Unsupported class file major version 69`.
- GREEN: `./gradlew clean test` → `BUILD SUCCESSFUL in 2s`.
- JAR: `./gradlew bootJar` → `BUILD SUCCESSFUL in 863ms`.
- JAR contents: `jar tf build/libs/fm-holder-0.1.0.jar | rg 'BOOT-INF/classes/kr/wearless/fmholder|BOOT-INF/classes/org/omnione/did/base/datamodel/data/Proof.class'` found holder classes and `BOOT-INF/classes/org/omnione/did/base/datamodel/data/Proof.class`.

Concern removed: the literal brief commands no longer need a manual `JAVA_HOME`.
