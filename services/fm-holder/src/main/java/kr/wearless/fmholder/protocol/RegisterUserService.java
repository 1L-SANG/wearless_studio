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
 * Flow A(register-user) 오케스트레이션 — dev 프로파일 TAS 상대 실완주.
 * register-wallet(지갑 등록·DID 앵커) → propose → request-ecdh(keyagree 서명·세션키)
 * → request-create-token(serverToken 유도) → retrieve-kyc(A4) → request-register-user(A5,
 * SignedDidDoc) → confirm-register-user(A6). txId/serverToken 스레딩, 두 DID(WALLET≠USER).
 *
 * <p>serverToken 복호/유도가 실패하면(예: 예상 밖 sample 응답) 위조하지 않고 create-token 직후
 * truthful 상태(status=token_structural_only, flowAComplete=false)로 조기 반환한다 — A4~A6 미실행.
 * dev 프로파일 정상 경로에선 실 유도가 성공해 A4~A6 까지 완주(flowAComplete=true).
 */
@Service
public class RegisterUserService {

    private static final Logger log = LoggerFactory.getLogger(RegisterUserService.class);
    private static final DateTimeFormatter TS = DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss'Z'");

    private static final String CAS_DID = "did:omn:cas";

    private final TasClient tas;
    private final HolderWalletService wallets;
    private final WalletEnrollService walletEnroll;
    private final CasKeyService casKey;
    private final KycSetupService kycSetup;

    public RegisterUserService(TasClient tas, HolderWalletService wallets,
                               WalletEnrollService walletEnroll, CasKeyService casKey,
                               KycSetupService kycSetup) {
        this.tas = tas;
        this.wallets = wallets;
        this.walletEnroll = walletEnroll;
        this.casKey = casKey;
        this.kycSetup = kycSetup;
    }

    public record RegisterResult(
            String did, String userDid, String txId, String status, boolean serverTokenDecrypted,
            String walletEnroll, boolean flowAComplete, String note) {}

    public RegisterResult register(String modelId) throws Exception {
        // 멱등 short-circuit: 이미 Flow A 완주(User 등록 + wallet ASSIGNED)한 모델은 재실행하지 않는다.
        // (프로토콜상 재실행은 create-token 이 CREATED 지갑을 못 찾아 SSRVTRA17502 로 실패한다.)
        if (wallets.isFlowAComplete(modelId)) {
            String didDone = wallets.readDid(modelId);
            String userDidDone = wallets.readUserDid(modelId);
            log.info("Flow A already complete for model {} (idempotent short-circuit)", modelId);
            return new RegisterResult(didDone, userDidDone, null, "already_registered", true,
                    "already_registered", true,
                    "Flow A already completed for this model (idempotent short-circuit); "
                  + "TAS User row + wallet ASSIGNED already present.");
        }

        // US-1: 월렛을 먼저 등록(request-register-wallet) — SSRVTRA17502 해소 + 홀더 DID 온체인 앵커.
        // 멱등: 이미 등록됐으면 tolerated. 이것이 별도 DidAnchorService 앵커를 대체한다.
        WalletEnrollService.EnrollResult enroll = walletEnroll.enroll(modelId);
        log.info("wallet enroll for model {} -> {} (txId={})", modelId, enroll.status(), enroll.txId());

        WalletManagerInterface wallet = wallets.connect(modelId);
        try {
            String did = wallets.readDid(modelId);
            String walletId = wallets.walletId(modelId);

            // A1 propose
            String txId = tas.proposeRegisterUser(new RegisterUserDtos.ProposeReq(TasClient.newId())).txId();

            // A2 request-ecdh — keyagree 서명 후 세션키 유도 (통과 = 홀더 DID 온체인 resolvable 증거)
            byte[] clientNonce = HolderCrypto.nonce16();
            EcdhReqData reqEcdh = buildEcdhReq(wallet, did, clientNonce);
            AccEcdh acc = tas.requestEcdh(new RegisterUserDtos.EcdhReq(TasClient.newId(), txId, reqEcdh)).accEcdh();
            byte[] sessionKey = HolderCrypto.deriveSessionKey(
                    wallet, "keyagree", acc.getPublicKey(),
                    clientNonce, HolderCrypto.decode(acc.getServerNonce()), acc.getCipher());

            // A3 request-create-token — US-2 핵심. walletInfo=홀더 #assert, caAppInfo=did:omn:cas #assert.
            // US-1 게이트(SSRVTRA17502 지갑 조회) + US-2 게이트(SSRVTRA16510 caAppInfo 서명) 모두 통과해야 한다.
            ServerTokenSeed seed = buildTokenSeed(wallet, did, walletId);
            RegisterUserDtos.CreateTokenRes tok;
            try {
                tok = tas.requestCreateToken(new RegisterUserDtos.CreateTokenReq(TasClient.newId(), txId, seed));
            } catch (org.springframework.web.client.RestClientResponseException e) {
                log.error("request-create-token FAILED (wallet enroll={}) status={} body={}",
                        enroll.status(), e.getStatusCode(), e.getResponseBodyAsString());
                throw e;
            }
            // serverToken 유도 — 이후 retrieve-kyc/register-user/confirm 이 요구하는 실토큰.
            // (encStd 복호 평문 = serializeAndSort(ServerTokenData), serverToken = multibase(SHA256(평문)))
            String serverToken;
            try {
                serverToken = HolderCrypto.serverTokenFromEncStd(
                        tok.encStd(), tok.iv(), sessionKey, acc.getCipher(), acc.getPadding());
            } catch (Exception e) {
                // dev 프로파일이면 여기 도달하지 않는다. 복호 실패 = sample/세션키 불일치 → 실토큰 없이
                // A4-A6 진행 불가. 위조 금지: truthful 하게 미완주 반환.
                log.warn("serverToken derive failed (likely TAS sample profile) — cannot run A4-A6 with a real token");
                String userDidOnly = wallets.ensureUserWallet(modelId);
                return new RegisterResult(did, userDidOnly, txId, "token_structural_only", false,
                        enroll.status(), false,
                        "create-token succeeded but serverToken not derivable (TAS not on dev profile?); "
                      + "A4-A6 require a real server token and were not run (no fabrication).");
            }
            log.info("create-token OK model={} did={} serverToken derived", modelId, did);

            // A4 retrieve-kyc — KYC 인프라(멱등) 보장 후 kycTxId 로 PII 조회 트리거.
            // TAS 시퀀스상 create-token 직후 sub-tx == REQUEST_CREATE_TOKEN 일 때만 유효.
            String kycTxId = kycSetup.ensure(modelId);
            tas.retrieveKyc(new RegisterUserDtos.RetrieveKycReq(TasClient.newId(), txId, serverToken, kycTxId));
            log.info("retrieve-kyc OK model={} kycTxId={}", modelId, kycTxId);

            // A5 request-register-user — ownerDidDoc = USER DID(별도 DID, VC subject). RoleType.ETC 앵커 + wallet ASSIGNED.
            String userDid = wallets.ensureUserWallet(modelId);
            String signedUserDidDocJson;
            WalletManagerInterface userWallet = wallets.connectUser(modelId);
            try {
                signedUserDidDocJson = HolderDidDoc.selfSign(userWallet, wallets.userDidDocJson(modelId));
            } finally {
                userWallet.disConnect();
            }
            // SignedDidDoc.proof = WALLET DID #assert 서명, wallet.did = WALLET DID(체인 앵커됨);
            // ownerDidDoc = USER DID doc(자기서명). TAS validateSignedDidDoc 이 wallet.did 로 검증.
            SignedDidDoc sdd = HolderDidDoc.buildSignedDidDoc(wallet, signedUserDidDocJson, did, walletId);
            tas.requestRegisterUser(new RegisterUserDtos.RegisterUserReq(TasClient.newId(), txId, serverToken, sdd));
            log.info("request-register-user OK model={} userDid={}", modelId, userDid);

            // A6 confirm-register-user — 트랜잭션 COMPLETED.
            tas.confirmRegisterUser(new RegisterUserDtos.ConfirmReq(TasClient.newId(), txId, serverToken));
            wallets.markFlowAComplete(modelId);
            log.info("confirm-register-user OK model={} txId={} — Flow A COMPLETE", modelId, txId);

            String note = "Flow A complete. Two-DID model: WALLET DID (" + did + ") anchored RoleType.WALLET "
                    + "via register-wallet (= ecdh client, SignedWalletInfo/SignedDidDoc.wallet.did); "
                    + "USER DID (" + userDid + ") = ownerDidDoc/VC subject, anchored RoleType.ETC by register-user. "
                    + "Wallet flipped CREATED->ASSIGNED; TAS User row created with pii. Flow B (issue-vc) unblocked.";
            return new RegisterResult(did, userDid, txId, "registered", true, enroll.status(), true, note);
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

    private ServerTokenSeed buildTokenSeed(WalletManagerInterface wallet, String did, String walletId)
            throws Exception {
        // walletInfo — 홀더 #assert 서명(변경 없음). TAS validateWalletProof 가 체인의 홀더 DID 로 검증.
        Wallet w = new Wallet();
        w.setId(walletId);
        w.setDid(did);
        Proof wp = proof(did, "assert", ProofPurpose.ASSERTION_METHOD);
        SignedWalletInfo swi = new SignedWalletInfo();
        swi.setWallet(w);
        swi.setNonce(HolderCrypto.encode(HolderCrypto.nonce16()));
        swi.setProof(wp);
        HolderCrypto.sign(swi, wp, wallet, "assert");

        // caAppInfo(AttestedAppInfo) — CAS(did:omn:cas)가 서명하는 앱 어테스테이션(US-2).
        // TAS validateCasProof 요구: (1) extractDid(proof.verificationMethod) == provider.did == did:omn:cas,
        // (2) 체인 앵커된 did:omn:cas assert 공개키(zfN7Sxu...)로 서명 검증, (3) provider.certVcRef 무조건 페치
        // (CA :8094 가 HTTP 200 으로 서빙, credentialSubject.id==did:omn:cas, issuer==did:omn:tas).
        // 그러므로 verificationMethod = did:omn:cas?versionId=1#assert, 서명은 cas.wallet #assert 로.
        Provider prov = new Provider();
        prov.setDid(CAS_DID);
        prov.setCertVcRef("http://localhost:8094/cas/api/v1/certificate-vc");
        Proof ap = proof(CAS_DID, "assert", ProofPurpose.ASSERTION_METHOD);
        AttestedAppInfo cai = new AttestedAppInfo();
        cai.setAppId("fm-holder-app");   // free-form — caAppInfo 검증·Flow A 에서 app 테이블 대조 안 함
        cai.setProvider(prov);
        cai.setNonce(HolderCrypto.encode(HolderCrypto.nonce16()));
        cai.setProof(ap);
        casKey.sign(cai, ap, "assert");  // did:omn:cas #assert 실서명 (홀더 키 아님)

        ServerTokenSeed seed = new ServerTokenSeed();
        seed.setPurpose(ServerTokenPurpose.CREATE_DID);
        seed.setWalletInfo(swi);
        seed.setCaAppInfo(cai);
        return seed;
    }
}
