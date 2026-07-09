package kr.wearless.fmholder.protocol;

import kr.wearless.fmholder.wallet.HolderWalletService;
import org.omnione.did.base.datamodel.data.AccEcdh;
import org.omnione.did.base.datamodel.data.AttestedAppInfo;
import org.omnione.did.base.datamodel.data.EcdhReqData;
import org.omnione.did.base.datamodel.data.Proof;
import org.omnione.did.base.datamodel.data.Provider;
import org.omnione.did.base.datamodel.data.ServerTokenSeed;
import org.omnione.did.base.datamodel.data.SignedDidDoc;
import org.omnione.did.base.datamodel.data.SignedWalletInfo;
import org.omnione.did.base.datamodel.data.Wallet;
import org.omnione.did.base.datamodel.enums.EccCurveType;
import org.omnione.did.base.datamodel.enums.ProofPurpose;
import org.omnione.did.base.datamodel.enums.ProofType;
import org.omnione.did.base.datamodel.enums.ServerTokenPurpose;
import org.omnione.did.wallet.key.WalletManagerInterface;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.time.ZoneOffset;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;

/**
 * Flow A(register-user) 오케스트레이션 — 홀더 DID 온체인 등록(A5에서 TAS가 앵커).
 * propose → request-ecdh(keyagree 서명·세션키) → request-create-token(serverToken 복호화)
 * → request-register-user(SignedDidDoc) → confirm-register-user. txId/serverToken 스레딩.
 *
 * <p>실행 중 TAS가 sample 프로파일이면 encStd 가 우리 세션키와 무관한 canned 값이라 복호화가
 * 실패한다 — 그 경우 placeholder serverToken 으로 폴백(sample 은 토큰도 무시). dev 프로파일에선
 * 실 복호화가 성공한다.
 */
@Service
public class RegisterUserService {

    private static final Logger log = LoggerFactory.getLogger(RegisterUserService.class);
    private static final DateTimeFormatter TS = DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss'Z'");

    private final TasClient tas;
    private final HolderWalletService wallets;

    public RegisterUserService(TasClient tas, HolderWalletService wallets) {
        this.tas = tas;
        this.wallets = wallets;
    }

    public record RegisterResult(String did, String txId, String status, boolean serverTokenDecrypted) {}

    public RegisterResult register(String modelId) throws Exception {
        WalletManagerInterface wallet = wallets.connect(modelId);
        try {
            String did = wallets.readDid(modelId);
            String walletId = wallets.walletId(modelId);

            // A1 propose
            String txId = tas.proposeRegisterUser(new RegisterUserDtos.ProposeReq(TasClient.newId())).txId();

            // A2 request-ecdh — keyagree 서명 후 세션키 유도
            byte[] clientNonce = HolderCrypto.nonce16();
            EcdhReqData reqEcdh = buildEcdhReq(wallet, did, clientNonce);
            AccEcdh acc = tas.requestEcdh(new RegisterUserDtos.EcdhReq(TasClient.newId(), txId, reqEcdh)).accEcdh();
            byte[] sessionKey = HolderCrypto.deriveSessionKey(
                    wallet, "keyagree", acc.getPublicKey(),
                    clientNonce, HolderCrypto.decode(acc.getServerNonce()), acc.getCipher());

            // A3 request-create-token — serverToken 복호화(sample canned 면 폴백)
            ServerTokenSeed seed = buildTokenSeed(wallet, did, walletId);
            RegisterUserDtos.CreateTokenRes tok =
                    tas.requestCreateToken(new RegisterUserDtos.CreateTokenReq(TasClient.newId(), txId, seed));
            boolean decrypted = true;
            String serverToken;
            try {
                serverToken = HolderCrypto.decryptServerToken(
                        tok.encStd(), tok.iv(), sessionKey, acc.getCipher(), acc.getPadding());
            } catch (Exception e) {
                decrypted = false;
                serverToken = tok.encStd(); // sample 폴백 — 서버가 토큰 미검증. dev 에선 위 복호화 성공.
                log.warn("serverToken decrypt failed (likely TAS sample profile) — using raw encStd as placeholder");
            }

            // A5 request-register-user — SignedDidDoc(자기서명 DID doc + #assert)
            String signedDidDocJson = HolderDidDoc.selfSign(wallet, wallets.didDocJson(modelId));
            SignedDidDoc sdd = HolderDidDoc.buildSignedDidDoc(wallet, signedDidDocJson, did, walletId);
            tas.requestRegisterUser(new RegisterUserDtos.RegisterUserReq(TasClient.newId(), txId, serverToken, sdd));

            // A6 confirm-register-user
            String finalTxId =
                    tas.confirmRegisterUser(new RegisterUserDtos.ConfirmReq(TasClient.newId(), txId, serverToken)).txId();

            return new RegisterResult(did, finalTxId, "registered", decrypted);
        } finally {
            wallet.disConnect();
        }
    }

    private static String now() {
        return TS.format(ZonedDateTime.now(ZoneOffset.UTC));
    }

    private static Proof proof(String did, String keyId, ProofPurpose purpose) {
        Proof p = new Proof();
        p.setType(ProofType.SECP_256R1_SIGNATURE_2018);
        p.setCreated(now());
        p.setVerificationMethod(did + "?versionId=1#" + keyId);
        p.setProofPurpose(purpose);
        return p;
    }

    private static EcdhReqData buildEcdhReq(WalletManagerInterface wallet, String did, byte[] clientNonce)
            throws Exception {
        Proof p = proof(did, "keyagree", ProofPurpose.KEY_AGREEMENT);
        EcdhReqData req = new EcdhReqData();
        req.setClient(did);
        req.setClientNonce(HolderCrypto.encode(clientNonce));
        req.setCurve(EccCurveType.SECP_256_R1);
        req.setPublicKey(wallet.getPublicKey("keyagree"));
        req.setProof(p);
        HolderCrypto.sign(req, p, wallet, "keyagree");
        return req;
    }

    private static ServerTokenSeed buildTokenSeed(WalletManagerInterface wallet, String did, String walletId)
            throws Exception {
        // walletInfo — 홀더 #assert 서명
        Wallet w = new Wallet();
        w.setId(walletId);
        w.setDid(did);
        Proof wp = proof(did, "assert", ProofPurpose.ASSERTION_METHOD);
        SignedWalletInfo swi = new SignedWalletInfo();
        swi.setWallet(w);
        swi.setNonce(HolderCrypto.encode(HolderCrypto.nonce16()));
        swi.setProof(wp);
        HolderCrypto.sign(swi, wp, wallet, "assert");

        // caAppInfo — 실제는 CAS(did:omn:cas)가 서명하는 앱 어테스테이션. 커스터디얼/샘플용 placeholder
        // (홀더 키로 자기서명, 서버 sample 은 미검증. dev 실연동 시 CAS 발급 어테스테이션으로 교체 필요).
        Provider prov = new Provider();
        prov.setDid("did:omn:cas");
        prov.setCertVcRef("http://localhost:8094/cas/api/v1/certificate-vc");
        Proof ap = proof(did, "assert", ProofPurpose.ASSERTION_METHOD);
        AttestedAppInfo cai = new AttestedAppInfo();
        cai.setAppId("fm-holder-app");
        cai.setProvider(prov);
        cai.setNonce(HolderCrypto.encode(HolderCrypto.nonce16()));
        cai.setProof(ap);
        HolderCrypto.sign(cai, ap, wallet, "assert");

        ServerTokenSeed seed = new ServerTokenSeed();
        seed.setPurpose(ServerTokenPurpose.CREATE_DID);
        seed.setWalletInfo(swi);
        seed.setCaAppInfo(cai);
        return seed;
    }
}
