package kr.wearless.fmholder.protocol;

import org.omnione.did.base.datamodel.data.Proof;
import org.omnione.did.wallet.key.WalletManagerFactory;
import org.omnione.did.wallet.key.WalletManagerFactory.WalletManagerType;
import org.omnione.did.wallet.key.WalletManagerInterface;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

/**
 * did:omn:wallet WALLET_PROVIDER 파일 월렛의 개인키 서명 서비스.
 *
 * <p>request-register-wallet 의 {@code AttestedDidDoc.proof} 는 반드시 did:omn:wallet 의
 * {@code #assert} 개인키로 실서명돼야 한다(TAS WalletServiceImpl.validateAttestedDidDoc 이
 * 체인의 assert 공개키로 검증). 그 개인키는 orchestrator 가 생성한 파일 월렛
 * {@code jars/Wallet/wallet.wallet} 안에 있다. 이 서비스가 그 월렛에 접속해 서명을 위임한다.
 *
 * <p>서명 연산은 홀더 월렛과 동일한 {@link HolderCrypto#sign}(정규직렬화→SHA-256→컴팩트 서명→
 * multibase base58btc)을 재사용한다 — 서버 크립토와 바이트 호환.
 */
@Service
public class ProviderKeyService {

    private final String filePath;
    private final char[] password;

    public ProviderKeyService(
            @Value("${opendid.wallet-provider.file-path}") String filePath,
            @Value("${opendid.wallet-provider.password}") String password) {
        this.filePath = filePath;
        this.password = password.toCharArray();
    }

    /**
     * {@code objWithProof} 안의 proof 를 did:omn:wallet 의 {@code keyId}(보통 "assert") 개인키로 서명한다.
     * 접속→서명→해제를 원자적으로 수행(동시 접속 회피를 위해 synchronized).
     */
    public synchronized void sign(Object objWithProof, Proof proof, String keyId) {
        WalletManagerInterface wm = null;
        try {
            wm = WalletManagerFactory.getWalletManager(WalletManagerType.FILE);
            wm.connect(filePath, password);
            HolderCrypto.sign(objWithProof, proof, wm, keyId);
        } catch (Exception e) {
            throw new IllegalStateException("provider-key sign failed for key " + keyId, e);
        } finally {
            disconnectQuietly(wm);
        }
    }

    /** did:omn:wallet 의 {@code keyId} 공개키(multibase) — 진단/검증용. */
    public synchronized String publicKey(String keyId) {
        WalletManagerInterface wm = null;
        try {
            wm = WalletManagerFactory.getWalletManager(WalletManagerType.FILE);
            wm.connect(filePath, password);
            return wm.getPublicKey(keyId);
        } catch (Exception e) {
            throw new IllegalStateException("provider-key publicKey failed for key " + keyId, e);
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
