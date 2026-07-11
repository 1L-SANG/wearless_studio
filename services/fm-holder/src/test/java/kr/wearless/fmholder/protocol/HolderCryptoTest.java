package kr.wearless.fmholder.protocol;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.omnione.did.base.datamodel.data.EcdhReqData;
import org.omnione.did.base.datamodel.data.Proof;
import org.omnione.did.base.datamodel.enums.EccCurveType;
import org.omnione.did.base.datamodel.enums.ProofType;
import org.omnione.did.base.datamodel.enums.SymmetricCipherType;
import org.omnione.did.base.datamodel.enums.SymmetricPaddingType;
import org.omnione.did.base.datamodel.enums.ProofPurpose;
import org.omnione.did.wallet.key.WalletManagerFactory;
import org.omnione.did.wallet.key.WalletManagerInterface;
import org.omnione.did.wallet.key.data.CryptoKeyPairInfo.KeyAlgorithmType;
import org.omnione.did.wallet.enums.WalletEncryptType;

import java.nio.file.Path;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * US-1 검증 — 벤더링한 서버 크립토(BaseCryptoUtil)로 홀더 sign→verify 라운드트립 + ECDH 대칭 +
 * AES/토큰 복호화가 자기일관하게 동작. 서버가 쓰는 바로 그 클래스로 검증하므로 실서버 수용의 강한 증거.
 */
class HolderCryptoTest {

    private static WalletManagerInterface wallet(Path dir, String name) throws Exception {
        Path p = dir.resolve(name + ".wallet");
        char[] pwd = "pw-test-1234".toCharArray();
        WalletManagerInterface wm =
                WalletManagerFactory.getWalletManager(WalletManagerFactory.WalletManagerType.FILE);
        wm.create(p.toString(), pwd, WalletEncryptType.AES_256_CBC_PKCS5Padding);
        wm.connect(p.toString(), pwd);
        wm.generateRandomKey("keyagree", KeyAlgorithmType.SECP256r1);
        wm.generateRandomKey("assert", KeyAlgorithmType.SECP256r1);
        return wm;
    }

    private static EcdhReqData ecdhReq(WalletManagerInterface w, String did) throws Exception {
        Proof proof = new Proof();
        proof.setType(ProofType.SECP_256R1_SIGNATURE_2018);
        proof.setCreated("2026-07-10T00:00:00Z");
        proof.setVerificationMethod(did + "?versionId=1#keyagree");
        proof.setProofPurpose(ProofPurpose.KEY_AGREEMENT);

        EcdhReqData req = new EcdhReqData();
        req.setClient(did);
        req.setClientNonce(HolderCrypto.encode(HolderCrypto.nonce16()));
        req.setCurve(EccCurveType.SECP_256_R1);
        req.setPublicKey(w.getPublicKey("keyagree"));
        req.setProof(proof);
        return req;
    }

    @Test
    void signProof_verifiesWithServerCryptoPath(@TempDir Path dir) throws Exception {
        WalletManagerInterface holder = wallet(dir, "holder");
        String did = "did:omn:holdertest";
        EcdhReqData req = ecdhReq(holder, did);

        HolderCrypto.sign(req, req.getProof(), holder, "keyagree");

        // 서버 검증 경로(BaseCryptoUtil.verifySignature)로 홀더 keyagree 공개키 대조 → 유효
        assertTrue(HolderCrypto.verify(holder.getPublicKey("keyagree"), req.getProof(), req));
        // 다른 키(assert 공개키)로는 실패해야 한다
        assertFalse(HolderCrypto.verify(holder.getPublicKey("assert"), req.getProof(), req));
    }

    @Test
    void ecdhSessionKey_isSymmetric(@TempDir Path dir) throws Exception {
        WalletManagerInterface holder = wallet(dir, "holder");
        WalletManagerInterface server = wallet(dir, "server");
        byte[] clientNonce = HolderCrypto.nonce16();
        byte[] serverNonce = HolderCrypto.nonce16();

        byte[] holderKey = HolderCrypto.deriveSessionKey(
                holder, "keyagree", server.getPublicKey("keyagree"),
                clientNonce, serverNonce, SymmetricCipherType.AES_256_CBC);
        byte[] serverKey = HolderCrypto.deriveSessionKey(
                server, "keyagree", holder.getPublicKey("keyagree"),
                clientNonce, serverNonce, SymmetricCipherType.AES_256_CBC);

        assertEquals(32, holderKey.length, "AES-256 세션키 = 32바이트");
        assertArrayEquals(holderKey, serverKey, "ECDH 세션키는 양측 동일해야 한다");
    }

    @Test
    void aesAndTokenDecrypt_roundTrip(@TempDir Path dir) throws Exception {
        WalletManagerInterface holder = wallet(dir, "holder");
        WalletManagerInterface server = wallet(dir, "server");
        byte[] cn = HolderCrypto.nonce16(), sn = HolderCrypto.nonce16();
        byte[] key = HolderCrypto.deriveSessionKey(
                holder, "keyagree", server.getPublicKey("keyagree"), cn, sn, SymmetricCipherType.AES_256_CBC);

        // 순수 AES 라운드트립
        byte[] iv = HolderCrypto.nonce16();
        String plain = "serverToken-abc123DEF";
        byte[] enc = HolderCrypto.aesEncrypt(plain, key, iv, SymmetricCipherType.AES_256_CBC, SymmetricPaddingType.PKCS5);
        byte[] back = HolderCrypto.aesDecrypt(enc, key, iv, SymmetricCipherType.AES_256_CBC, SymmetricPaddingType.PKCS5);
        assertEquals(plain, new String(back, java.nio.charset.StandardCharsets.UTF_8));

        // 토큰 복호화 경로(encStd/iv multibase → serverToken 원문)
        String encStd = HolderCrypto.encode(enc);
        String ivMb = HolderCrypto.encode(iv);
        String token = HolderCrypto.decryptServerToken(
                encStd, ivMb, key, SymmetricCipherType.AES_256_CBC, SymmetricPaddingType.PKCS5);
        assertEquals(plain, token);
    }
}
