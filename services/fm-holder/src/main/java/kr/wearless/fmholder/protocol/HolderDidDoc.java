package kr.wearless.fmholder.protocol;

import org.omnione.did.base.datamodel.data.Proof;
import org.omnione.did.base.datamodel.data.SignedDidDoc;
import org.omnione.did.base.datamodel.data.Wallet;
import org.omnione.did.base.datamodel.enums.ProofPurpose;
import org.omnione.did.base.datamodel.enums.ProofType;
import org.omnione.did.core.data.rest.SignatureParams;
import org.omnione.did.core.manager.DidManager;
import org.omnione.did.crypto.enums.DigestType;
import org.omnione.did.crypto.util.DigestUtils;
import org.omnione.did.wallet.key.WalletManagerInterface;

import java.nio.charset.StandardCharsets;
import java.time.ZoneOffset;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;
import java.util.List;

/**
 * 홀더 DID Document 자기서명 + SignedDidDoc 조립 — register-user(A5) 용.
 * DidManager(core SDK)로 서명키(assert/auth/invoke)별 proof 를 채우고, 월렛 개인키로 컴팩트 서명한다.
 * ownerDidDoc = 서명된 DID doc JSON 을 multibase(base58btc) 인코딩(서버 parseOwnerDidDoc 역연산).
 */
public final class HolderDidDoc {

    private static final DateTimeFormatter TS = DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss'Z'");
    /** 홀더 DID 에 넣는 서명키(keyagree 제외 — 서명 불가). */
    public static final List<String> SIGN_KEYS = List.of("assert", "auth", "invoke");

    private HolderDidDoc() {}

    /**
     * DID doc JSON(무proof) 을 로드해 서명키별 proof 를 채워 자기서명한다.
     * @return 서명된 DID doc JSON. 검증은 {@link #verifySelfSigned}.
     */
    public static String selfSign(WalletManagerInterface wallet, String didDocJson) {
        try {
            DidManager dm = new DidManager();
            dm.parse(didDocJson);
            List<SignatureParams> params = dm.getOriginDataForSign(SIGN_KEYS);
            for (SignatureParams sp : params) {
                byte[] hash = DigestUtils.getDigest(
                        sp.getOriginData().getBytes(StandardCharsets.UTF_8), DigestType.SHA256);
                byte[] sig = wallet.generateCompactSignatureFromHash(sp.getKeyId(), hash);
                sp.setSignatureValue(HolderCrypto.encode(sig));
            }
            dm.addProof(params);
            return dm.getDocument().toJson();
        } catch (Exception e) {
            throw new IllegalStateException("DID doc self-sign failed", e);
        }
    }

    /** 자기서명된 DID doc 의 각 proof 검증(DidManager.verifyDocumentSignature). 유효하면 true. */
    public static boolean verifySelfSigned(String signedDidDocJson) {
        try {
            DidManager dm = new DidManager();
            dm.parse(signedDidDocJson);
            dm.verifyDocumentSignature();
            return true;
        } catch (Exception e) {
            return false;
        }
    }

    /**
     * SignedDidDoc(A5 요청 필드) 조립 + #assert 자기서명.
     * @param signedDidDocJson selfSign 결과. @param holderDid 홀더 DID. @param walletId 월렛 식별자(WID...).
     */
    public static SignedDidDoc buildSignedDidDoc(
            WalletManagerInterface wallet, String signedDidDocJson, String holderDid, String walletId) {
        String ownerDidDoc = HolderCrypto.encode(signedDidDocJson.getBytes(StandardCharsets.UTF_8));

        Wallet w = new Wallet();
        w.setId(walletId);
        w.setDid(holderDid);

        Proof proof = new Proof();
        proof.setType(ProofType.SECP_256R1_SIGNATURE_2018);
        proof.setCreated(TS.format(ZonedDateTime.now(ZoneOffset.UTC)));
        proof.setVerificationMethod(holderDid + "?versionId=1#assert");
        proof.setProofPurpose(ProofPurpose.ASSERTION_METHOD);

        SignedDidDoc sdd = new SignedDidDoc();
        sdd.setOwnerDidDoc(ownerDidDoc);
        sdd.setWallet(w);
        sdd.setNonce(HolderCrypto.encode(HolderCrypto.nonce16()));
        sdd.setProof(proof);

        HolderCrypto.sign(sdd, proof, wallet, "assert");
        return sdd;
    }
}
