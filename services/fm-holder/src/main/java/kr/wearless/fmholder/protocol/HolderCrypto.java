package kr.wearless.fmholder.protocol;

import org.omnione.did.base.datamodel.data.Proof;
import org.omnione.did.base.datamodel.enums.EccCurveType;
import org.omnione.did.base.datamodel.enums.SymmetricCipherType;
import org.omnione.did.base.datamodel.enums.SymmetricPaddingType;
import org.omnione.did.base.util.BaseCryptoUtil;
import org.omnione.did.base.util.BaseDigestUtil;
import org.omnione.did.base.util.BaseMultibaseUtil;
import org.omnione.did.common.util.JsonUtil;
import org.omnione.did.crypto.enums.DigestType;
import org.omnione.did.crypto.enums.MultiBaseType;
import org.omnione.did.crypto.keypair.KeyPairInterface;
import org.omnione.did.crypto.util.DigestUtils;
import org.omnione.did.wallet.key.WalletManagerInterface;

import java.nio.charset.StandardCharsets;

/**
 * 홀더측 OpenDID 크립토 — 서버 {@link BaseCryptoUtil} 와 동일 연산을 홀더 관점에서 수행.
 * 벤더링한 서버 크립토를 그대로 쓰므로 로컬 sign→verify 라운드트립이 실서버 수용의 강한 증거다.
 *
 * <ul>
 *   <li>proof 서명: 정규 JSON(serializeAndSort) → SHA-256 → wallet 컴팩트 서명 → multibase(base58btc)</li>
 *   <li>ECDH 세션키: wallet.getSharedSecret → mergeNonce(client||server) → mergeSharedSecretAndNonce</li>
 *   <li>AES E2E: 세션키 + 16B IV, cipher/padding = 서버 accEcdh 값</li>
 * </ul>
 */
public final class HolderCrypto {

    public static final EccCurveType CURVE = EccCurveType.SECP_256_R1;

    private HolderCrypto() {}

    /** proofValue=null 상태의 객체를 정규직렬화 후 SHA-256 해시. (서버 extractSignatureMessage 미러) */
    public static byte[] canonicalHash(Object objWithProofValueNull) {
        try {
            String json = JsonUtil.serializeAndSort(objWithProofValueNull);
            return DigestUtils.getDigest(json.getBytes(StandardCharsets.UTF_8), DigestType.SHA256);
        } catch (Exception e) {
            throw new IllegalStateException("canonical hash failed", e);
        }
    }

    /**
     * proof 를 채워 서명한다. 호출 전 obj 안의 proof 는 proofValue 를 제외한 필드가 세팅돼 있어야 한다.
     * proofValue 를 null 로 만든 정규 메시지를 해시·서명해 proof.proofValue 에 기록한다.
     *
     * @param objWithProof 서명 대상(내부에 proof 참조 포함). @param proof obj 안의 proof 인스턴스.
     * @param wallet 홀더 월렛(연결됨). @param keyId 서명 키 id(keyagree/assert/auth ...).
     */
    public static void sign(Object objWithProof, Proof proof, WalletManagerInterface wallet, String keyId) {
        try {
            proof.setProofValue(null);
            byte[] hash = canonicalHash(objWithProof);
            byte[] sig = wallet.generateCompactSignatureFromHash(keyId, hash);
            proof.setProofValue(BaseMultibaseUtil.encode(sig, MultiBaseType.base58btc));
        } catch (Exception e) {
            throw new IllegalStateException("proof sign failed for key " + keyId, e);
        }
    }

    /**
     * proof 서명을 공개키로 검증(서버 검증 경로와 동일: verifyCompactSignWithCompressedKey).
     * @return 유효하면 true. (BaseCryptoUtil.verifySignature 는 실패 시 예외 → false)
     */
    public static boolean verify(String publicKeyMultibase, Proof proof, Object objWithProof) {
        String saved = proof.getProofValue();
        try {
            proof.setProofValue(null);
            byte[] hash = canonicalHash(objWithProof);
            BaseCryptoUtil.verifySignature(publicKeyMultibase, saved, hash, CURVE);
            return true;
        } catch (Exception e) {
            return false;
        } finally {
            proof.setProofValue(saved);
        }
    }

    /**
     * ECDH 세션키 유도. sharedSecret(홀더 keyagree priv × 서버 compressed pub) 과
     * mergedNonce(client||server) 를 해시해 cipher 길이만큼 취한다.
     */
    public static byte[] deriveSessionKey(
            WalletManagerInterface wallet, String keyagreeKeyId, String serverCompressedPubKeyMultibase,
            byte[] clientNonce, byte[] serverNonce, SymmetricCipherType cipher) {
        try {
            byte[] sharedSecret = wallet.getSharedSecret(keyagreeKeyId, serverCompressedPubKeyMultibase);
            byte[] mergedNonce = BaseCryptoUtil.mergeNonce(clientNonce, serverNonce);
            return BaseCryptoUtil.mergeSharedSecretAndNonce(sharedSecret, mergedNonce, cipher);
        } catch (Exception e) {
            throw new IllegalStateException("session key derivation failed", e);
        }
    }

    public static byte[] aesEncrypt(String plain, byte[] key, byte[] iv,
                                    SymmetricCipherType cipher, SymmetricPaddingType padding) {
        return BaseCryptoUtil.encrypt(plain, key, iv, cipher, padding);
    }

    public static byte[] aesDecrypt(byte[] enc, byte[] key, byte[] iv,
                                    SymmetricCipherType cipher, SymmetricPaddingType padding) {
        return BaseCryptoUtil.decrypt(enc, key, iv, cipher, padding);
    }

    /**
     * request-create-token 응답의 encStd(multibase) 를 세션키+iv 로 복호화해 serverToken 원문(문자열) 복원.
     * (서버 TokenServiceImpl: ServerTokenData 직렬화 → AES(sessionKey,iv) → encStd 의 역연산)
     */
    public static String decryptServerToken(String encStdMultibase, String ivMultibase, byte[] sessionKey,
                                            SymmetricCipherType cipher, SymmetricPaddingType padding) {
        byte[] enc = BaseMultibaseUtil.decode(encStdMultibase);
        byte[] iv = BaseMultibaseUtil.decode(ivMultibase);
        byte[] plain = BaseCryptoUtil.decrypt(enc, sessionKey, iv, cipher, padding);
        return new String(plain, StandardCharsets.UTF_8);
    }

    /**
     * create-token 응답에서 <b>serverToken</b>(retrieve-kyc/register-user/confirm 요청 필드)을 유도한다.
     *
     * <p>서버(TokenServiceImpl)는 {@code token = multibase(SHA-256(serializeAndSort(ServerTokenData)))} 를
     * DB 에 저장하고, {@code encStd} 는 <b>동일한</b> {@code serializeAndSort(ServerTokenData)} 를 세션키로
     * 암호화한 것이다. 따라서 복호화한 평문 바이트 = 해시 입력이므로 {@code multibase(SHA-256(평문))} 이
     * 저장 토큰과 바이트 동일하다({@code TokenValidator.validateServerToken} 는 multibase decode 후 바이트 비교).
     * multibase 알파벳은 자기기술 prefix 라 base64/base58btc 무관하게 decode 되어 일치한다.
     */
    public static String serverTokenFromEncStd(String encStdMultibase, String ivMultibase, byte[] sessionKey,
                                               SymmetricCipherType cipher, SymmetricPaddingType padding) {
        String stdJson = decryptServerToken(encStdMultibase, ivMultibase, sessionKey, cipher, padding);
        byte[] hash = BaseDigestUtil.generateHash(stdJson.getBytes(StandardCharsets.UTF_8));
        return BaseMultibaseUtil.encode(hash, MultiBaseType.base64);
    }

    public static byte[] decode(String multibase) {
        return BaseMultibaseUtil.decode(multibase);
    }

    public static String encode(byte[] data) {
        return BaseMultibaseUtil.encode(data, MultiBaseType.base58btc);
    }

    /** 16바이트 nonce (clientNonce 등, 서버 요구 = 정확히 16바이트). */
    public static byte[] nonce16() {
        return BaseCryptoUtil.generateNonce(16);
    }

    /** 16바이트 초기화 벡터(E2E accE2e.iv). */
    public static byte[] initialVector() {
        return BaseCryptoUtil.generateInitialVector();
    }

    // ── Flow B(issue-vc) E2E 발급자 채널 크립토 ────────────────────────────

    /**
     * 발급 요청(B6)용 1회성 E2E 키쌍 — 지갑 키가 아닌 raw EcKeyPair.
     * privateKeyPkcs8(=PrivateKey.getEncoded, PKCS8 DER)를 메모리에 보관해 encReqVc 암호화 +
     * 응답 encVc 복호화에 재사용한다(IssueVCTests.decode 미러).
     */
    public record E2eEphemeral(byte[] privateKeyPkcs8, byte[] compressedPublicKey) {}

    /**
     * 1회성 E2E 키쌍 생성. {@code BaseCryptoUtil.generateKeyPair} 로 raw EC 키쌍을 만들고
     * 공개키를 압축(발급자 accE2e.publicKey 포맷)한다. (IssueServiceBase.setPublicKeyAndNonce 미러)
     */
    public static E2eEphemeral generateE2eEphemeral(EccCurveType curve) {
        KeyPairInterface kp = BaseCryptoUtil.generateKeyPair(curve);
        byte[] priv = kp.getPrivateKey().getEncoded();                                       // PKCS8 DER
        byte[] pub = BaseCryptoUtil.compressPublicKey(kp.getPublicKey().getEncoded(), curve); // 압축 공개키
        return new E2eEphemeral(priv, pub);
    }

    /**
     * 발급자 E2E 세션키 = mergeSharedSecretAndNonce( ECDH(issuerCompressedPub, holderEphemeralPriv), nonce, cipher ).
     * 발급자 {@code IssueServiceBase.issueVc}(generateSharedSecretKey→mergeSharedSecretAndNonce) 및
     * {@code IssueVCTests.decode()} 와 바이트 동일. encReqVc 암호화·응답 encVc 복호화에 공용으로 쓴다.
     *
     * @param issuerCompressedPub 발급자 profile.process.reqE2e.publicKey 를 multibase decode 한 압축 공개키
     * @param holderPrivPkcs8     {@link #generateE2eEphemeral} 의 홀더 1회성 개인키(PKCS8)
     * @param nonce               profile.process.reqE2e.nonce 를 multibase decode 한 raw nonce
     */
    public static byte[] e2eSessionKey(byte[] issuerCompressedPub, byte[] holderPrivPkcs8, byte[] nonce,
                                       EccCurveType curve, SymmetricCipherType cipher) {
        byte[] sharedSecret = BaseCryptoUtil.generateSharedSecret(issuerCompressedPub, holderPrivPkcs8, curve);
        return BaseCryptoUtil.mergeSharedSecretAndNonce(sharedSecret, nonce, cipher);
    }
}
