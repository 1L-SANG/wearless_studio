package kr.wearless.fmholder.wallet;

import org.omnione.did.core.data.rest.DidKeyInfo;
import org.omnione.did.core.manager.DidManager;
import org.omnione.did.data.model.did.DidDocument;
import org.omnione.did.data.model.enums.did.AuthType;
import org.omnione.did.data.model.enums.did.DidKeyType;
import org.omnione.did.data.model.enums.did.ProofPurpose;
import org.omnione.did.wallet.enums.WalletEncryptType;
import org.omnione.did.wallet.key.WalletManagerFactory;
import org.omnione.did.wallet.key.WalletManagerFactory.WalletManagerType;
import org.omnione.did.wallet.key.WalletManagerInterface;
import org.omnione.did.wallet.key.data.CryptoKeyPairInfo.KeyAlgorithmType;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

/**
 * 모델별 커스터디얼 월렛 + DID Document 생성/보관.
 *
 * <p>OpenDID 공식 서버 SDK 사용:
 * <ul>
 *   <li>did-wallet-sdk-server: 파일 월렛(암호화) 생성 + SECP256r1 키쌍 생성/보관</li>
 *   <li>did-core-sdk-server: 생성한 공개키로 DID Document 조립</li>
 * </ul>
 *
 * <p>커스터디얼: 월렛은 서버가 보관한다. 월렛 파일 비밀번호는 모델ID에서 결정적으로
 * 재유도(HMAC-SHA256(pepper, modelId))하므로 별도 비밀 저장이 필요없고 P2 발급 시 재접속할 수 있다.
 */
@Service
public class HolderWalletService {

    private static final WalletEncryptType ENC = WalletEncryptType.AES_256_CBC_PKCS5Padding;

    /** 홀더 DID에 넣을 키 세트. keyagree는 P210 E2E(ECDH), 나머지는 인증/서명/권한. */
    private record KeySpec(String keyId, ProofPurpose purpose) {}

    private static final List<KeySpec> KEYS = List.of(
            new KeySpec("keyagree", ProofPurpose.KEY_AGREEMENT),
            new KeySpec("auth", ProofPurpose.AUTHENTICATION),
            new KeySpec("assert", ProofPurpose.ASSERTION_METHOD),
            new KeySpec("invoke", ProofPurpose.CAPABILITY_INVOCATION)
    );

    private final Path dataDir;
    private final String pepper;

    public HolderWalletService(
            @Value("${holder.data-dir:./data}") String dataDir,
            @Value("${holder.wallet-pepper:fm-holder-dev-pepper}") String pepper) {
        this.dataDir = Path.of(dataDir);
        this.pepper = pepper;
    }

    /** 모델의 월렛 파일 경로(있으면 이미 생성된 것). */
    public Path walletPath(String modelId) {
        return dataDir.resolve("wallets").resolve(modelId + ".wallet");
    }

    public Path didDocPath(String modelId) {
        return dataDir.resolve("dids").resolve(modelId + ".did.json");
    }

    public boolean exists(String modelId) {
        return Files.exists(walletPath(modelId));
    }

    /** 저장된 DID Document JSON을 읽어 DID를 반환(없으면 null). */
    public String readDid(String modelId) throws Exception {
        Path p = didDocPath(modelId);
        if (!Files.exists(p)) return null;
        DidDocument doc = new DidDocument();
        doc.fromJson(Files.readString(p));
        return doc.getId();
    }

    /** 저장된 DID Document JSON 원문(register-user 자기서명 입력). */
    public String didDocJson(String modelId) throws Exception {
        return Files.readString(didDocPath(modelId));
    }

    /** 모델 월렛에 접속(결정적 비밀번호 재유도). 커스터디얼 — 발급/등록 시 재접속용. */
    public WalletManagerInterface connect(String modelId) throws Exception {
        Path p = walletPath(modelId);
        if (!Files.exists(p)) throw new IllegalStateException("no wallet for model " + modelId);
        WalletManagerInterface wm = WalletManagerFactory.getWalletManager(WalletManagerType.FILE);
        wm.connect(p.toString(), derivePassword(modelId));
        return wm;
    }

    /** 모델별 결정적 월렛 식별자(WID). SignedWalletInfo/SignedDidDoc.wallet.id 용. */
    public String walletId(String modelId) {
        return "WID" + hmacHex(modelId).substring(0, 16);
    }

    /**
     * 모델별 월렛 + DID Document 생성.
     *
     * @throws IllegalStateException 이미 존재하는 모델(중복 생성 방지)
     */
    public WalletResult createWallet(String modelId) throws Exception {
        Files.createDirectories(dataDir.resolve("wallets"));
        Files.createDirectories(dataDir.resolve("dids"));

        Path walletPath = walletPath(modelId);
        if (Files.exists(walletPath)) {
            throw new IllegalStateException("wallet already exists for model " + modelId);
        }

        char[] pwd = derivePassword(modelId);
        WalletManagerInterface wm = WalletManagerFactory.getWalletManager(WalletManagerType.FILE);
        wm.create(walletPath.toString(), pwd, ENC);
        wm.connect(walletPath.toString(), pwd);
        try {
            String did = generateDid(modelId);
            List<DidKeyInfo> keyInfos = new ArrayList<>();
            for (KeySpec ks : KEYS) {
                wm.generateRandomKey(ks.keyId(), KeyAlgorithmType.SECP256r1);
                String publicKey = wm.getPublicKey(ks.keyId());

                DidKeyInfo ki = new DidKeyInfo();
                ki.setKeyId(ks.keyId());
                ki.setAlgoType(DidKeyType.SECP256R1_VERIFICATION_KEY_2018.getRawValue());
                ki.setPublicKey(publicKey);
                ki.setController(did);
                ki.setAuthType(AuthType.Free);
                ki.setKeyPurpose(List.of(ks.purpose()));
                keyInfos.add(ki);
            }

            DidManager dm = new DidManager();
            dm.createDocument(did, did, keyInfos); // 자기소유 홀더 DID: controller = did
            String didDocJson = dm.getDocument().toJson();
            Files.writeString(didDocPath(modelId), didDocJson);

            List<String> keyIds = keyInfos.stream().map(DidKeyInfo::getKeyId).toList();
            return new WalletResult(modelId, did, keyIds);
        } finally {
            wm.disConnect();
        }
    }

    /** did:omn:fm<16 hex> — 모델ID 기반 결정적 method-specific id. */
    private String generateDid(String modelId) {
        String hex = hmacHex(modelId).substring(0, 16);
        return "did:omn:fm" + hex;
    }

    /** 월렛 비밀번호 = HMAC-SHA256(pepper, modelId) hex → char[]. 결정적 재유도(커스터디얼). */
    private char[] derivePassword(String modelId) {
        return hmacHex(modelId).toCharArray();
    }

    private String hmacHex(String msg) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(pepper.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
            byte[] out = mac.doFinal(msg.getBytes(StandardCharsets.UTF_8));
            StringBuilder sb = new StringBuilder(out.length * 2);
            for (byte b : out) sb.append(String.format("%02x", b));
            return sb.toString();
        } catch (Exception e) {
            throw new IllegalStateException("hmac failed", e);
        }
    }

    public record WalletResult(String modelId, String did, List<String> keyIds) {}
}
