package kr.wearless.fmholder.protocol;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.omnione.did.base.datamodel.data.SignedDidDoc;
import org.omnione.did.core.data.rest.DidKeyInfo;
import org.omnione.did.core.manager.DidManager;
import org.omnione.did.data.model.enums.did.AuthType;
import org.omnione.did.data.model.enums.did.DidKeyType;
import org.omnione.did.data.model.enums.did.ProofPurpose;
import org.omnione.did.wallet.enums.WalletEncryptType;
import org.omnione.did.wallet.key.WalletManagerFactory;
import org.omnione.did.wallet.key.WalletManagerInterface;
import org.omnione.did.wallet.key.data.CryptoKeyPairInfo.KeyAlgorithmType;

import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertTrue;

/** US-2 검증 — 홀더 DID doc 자기서명이 DidManager 검증을 통과하고, SignedDidDoc proof 가 #assert 로 검증된다. */
class HolderDidDocTest {

    private record KeySpec(String id, ProofPurpose purpose) {}
    private static final List<KeySpec> KEYS = List.of(
            new KeySpec("keyagree", ProofPurpose.KEY_AGREEMENT),
            new KeySpec("assert", ProofPurpose.ASSERTION_METHOD),
            new KeySpec("auth", ProofPurpose.AUTHENTICATION),
            new KeySpec("invoke", ProofPurpose.CAPABILITY_INVOCATION));

    private static WalletManagerInterface newWallet(Path dir) throws Exception {
        Path p = dir.resolve("h.wallet");
        char[] pwd = "pw-1234".toCharArray();
        WalletManagerInterface wm =
                WalletManagerFactory.getWalletManager(WalletManagerFactory.WalletManagerType.FILE);
        wm.create(p.toString(), pwd, WalletEncryptType.AES_256_CBC_PKCS5Padding);
        wm.connect(p.toString(), pwd);
        for (KeySpec k : KEYS) wm.generateRandomKey(k.id(), KeyAlgorithmType.SECP256r1);
        return wm;
    }

    private static String buildDidDoc(WalletManagerInterface wm, String did) throws Exception {
        List<DidKeyInfo> infos = new ArrayList<>();
        for (KeySpec k : KEYS) {
            DidKeyInfo ki = new DidKeyInfo();
            ki.setKeyId(k.id());
            ki.setAlgoType(DidKeyType.SECP256R1_VERIFICATION_KEY_2018.getRawValue());
            ki.setPublicKey(wm.getPublicKey(k.id()));
            ki.setController(did);
            ki.setAuthType(AuthType.Free);
            ki.setKeyPurpose(List.of(k.purpose()));
            infos.add(ki);
        }
        DidManager dm = new DidManager();
        dm.createDocument(did, did, infos);
        return dm.getDocument().toJson();
    }

    @Test
    void selfSignAndSignedDidDoc_verify(@TempDir Path dir) throws Exception {
        WalletManagerInterface wm = newWallet(dir);
        String did = "did:omn:holdertest2";
        String didDocJson = buildDidDoc(wm, did);

        // 자기서명 → DidManager 검증 통과
        String signed = HolderDidDoc.selfSign(wm, didDocJson);
        assertTrue(HolderDidDoc.verifySelfSigned(signed), "자기서명된 DID doc 의 proof 가 검증돼야 한다");

        // SignedDidDoc 조립 + #assert proof 가 홀더 assert 공개키로 검증
        SignedDidDoc sdd = HolderDidDoc.buildSignedDidDoc(wm, signed, did, "WID-test-0001");
        assertTrue(HolderCrypto.verify(wm.getPublicKey("assert"), sdd.getProof(), sdd),
                "SignedDidDoc proof 가 wallet #assert 공개키로 검증돼야 한다 (서버 validateSignedDidDoc 경로)");
    }
}
