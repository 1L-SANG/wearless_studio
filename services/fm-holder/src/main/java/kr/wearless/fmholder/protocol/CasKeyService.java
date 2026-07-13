package kr.wearless.fmholder.protocol;

import jakarta.annotation.PostConstruct;
import org.omnione.did.base.datamodel.data.Proof;
import org.omnione.did.wallet.key.WalletManagerFactory;
import org.omnione.did.wallet.key.WalletManagerFactory.WalletManagerType;
import org.omnione.did.wallet.key.WalletManagerInterface;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

/**
 * did:omn:cas CAS(certificate authority server) 파일 월렛의 개인키 서명 서비스.
 *
 * <p>US-2 — request-create-token 의 {@code caAppInfo}(AttestedAppInfo)는 반드시 did:omn:cas 의
 * {@code #assert} 개인키로 실서명돼야 한다. TAS {@code TokenServiceImpl.validateCasProof} 는
 * (1) {@code extractDid(proof.verificationMethod).equals(provider.did == "did:omn:cas")},
 * (2) 체인 앵커된 did:omn:cas DID doc 의 assert 공개키로 서명 검증을 요구한다. 홀더 자기키로
 * 서명하면 {@code DID mismatch: clientDid=did:omn:cas, didOfKeyUrl=did:omn:fm...} → SSRVTRA16510
 * INVALID_SIGNATURE. 그 개인키는 orchestrator 가 생성한 파일 월렛 {@code jars/CA/cas.wallet} 안에 있다.
 *
 * <p>{@link ProviderKeyService}(did:omn:wallet)의 정확한 클론 — 다른 월렛 파일/비번을 가리킬 뿐,
 * 서명 연산은 동일한 {@link HolderCrypto#sign}(정규직렬화→SHA-256→컴팩트 서명→multibase base58btc)을
 * 재사용한다. 홀더 월렛 등록 경로와 격리해 회귀 위험을 없앤다.
 */
@Service
public class CasKeyService {

    private static final Logger log = LoggerFactory.getLogger(CasKeyService.class);
    /** cas.did 의 assert 공개키 — connect 성공(=비번 일치) 검증용. */
    private static final String EXPECTED_ASSERT_PUBKEY = "zfN7SxuBgogbZ4QCMQc9rVdg7yLBhNCs5y6yQ5fZh31r9";

    private final String filePath;
    private final char[] password;

    public CasKeyService(
            @Value("${opendid.cas-provider.file-path}") String filePath,
            @Value("${opendid.cas-provider.password}") String password) {
        this.filePath = filePath;
        this.password = password.toCharArray();
    }

    /** 부팅 시 cas.wallet 접속·assert 공개키를 로깅해 비번/키 일치를 조기 확인(비파괴적). */
    @PostConstruct
    void verifyOnBoot() {
        try {
            String pub = publicKey("assert");
            boolean match = EXPECTED_ASSERT_PUBKEY.equals(pub);
            log.info("cas-key boot check: assert pubkey={} (matches cas.did={}) file={}", pub, match, filePath);
            if (!match) {
                log.warn("cas-key assert pubkey MISMATCH — expected {} — caAppInfo signatures may be rejected",
                        EXPECTED_ASSERT_PUBKEY);
            }
        } catch (Exception e) {
            log.warn("cas-key boot check failed (wrong password or missing wallet?) file={}: {}",
                    filePath, e.getMessage());
        }
    }

    /**
     * {@code objWithProof} 안의 proof 를 did:omn:cas 의 {@code keyId}(보통 "assert") 개인키로 서명한다.
     * 접속→서명→해제를 원자적으로 수행(동시 접속 회피를 위해 synchronized).
     */
    public synchronized void sign(Object objWithProof, Proof proof, String keyId) {
        WalletManagerInterface wm = null;
        try {
            wm = WalletManagerFactory.getWalletManager(WalletManagerType.FILE);
            wm.connect(filePath, password);
            HolderCrypto.sign(objWithProof, proof, wm, keyId);
        } catch (Exception e) {
            throw new IllegalStateException("cas-key sign failed for key " + keyId, e);
        } finally {
            disconnectQuietly(wm);
        }
    }

    /** did:omn:cas 의 {@code keyId} 공개키(multibase) — 진단/검증용(cas.did assert == zfN7Sxu...). */
    public synchronized String publicKey(String keyId) {
        WalletManagerInterface wm = null;
        try {
            wm = WalletManagerFactory.getWalletManager(WalletManagerType.FILE);
            wm.connect(filePath, password);
            return wm.getPublicKey(keyId);
        } catch (Exception e) {
            throw new IllegalStateException("cas-key publicKey failed for key " + keyId, e);
        } finally {
            disconnectQuietly(wm);
        }
    }

    private static void disconnectQuietly(WalletManagerInterface wm) {
        if (wm == null) return;
        try {
            wm.disConnect();
        } catch (Exception ignore) {
            // best-effort
        }
    }
}
