package kr.wearless.fmholder.protocol;

import kr.wearless.fmholder.wallet.HolderWalletService;
import org.omnione.did.base.datamodel.data.AttestedDidDoc;
import org.omnione.did.base.datamodel.data.Proof;
import org.omnione.did.base.datamodel.data.Provider;
import org.omnione.did.base.datamodel.enums.ProofPurpose;
import org.omnione.did.base.datamodel.enums.ProofType;
import org.omnione.did.wallet.key.WalletManagerInterface;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClientResponseException;

import java.nio.charset.StandardCharsets;
import java.time.ZoneOffset;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;

/**
 * US-1 — 홀더 월렛을 TAS 에 등록(request-register-wallet)해 SSRVTRA17502
 * ("Failed to find wallet: wallet is not registered") 를 해소한다.
 *
 * <p>request-create-token 의 지갑 조회(TokenServiceImpl:258-269)는
 * {@code wallet(wallet_id, did, status=CREATED)} 행을 찾는다. 그 행은 오직
 * request-register-wallet(WalletServiceImpl.insertWallet:124-130)만 쓴다:
 * {@code wallet_id=AttestedDidDoc.walletId, did=ownerDidDoc.id, status=CREATED}. verbatim, no hash.
 *
 * <p>결정적 요구: {@code AttestedDidDoc.proof} 는 did:omn:wallet 의 {@code #assert} 개인키
 * Secp256r1 실서명이어야 한다({@link ProviderKeyService}). {@code provider.certVcRef=null} 로
 * 두어 TAS 의 cert-VC HTTP 페치를 건너뛴다(wallet 경로만 null-guard).
 *
 * <p>부작용: register-wallet 은 홀더 DID doc 도 온체인 앵커한다(RoleType.WALLET,
 * WalletServiceImpl:120) — 별도 {@link DidAnchorService} 앵커를 대체한다.
 *
 * <p>매핑 불변식(create-token 과 반드시 일치):
 * <ul>
 *   <li>{@code AttestedDidDoc.walletId} == {@code SignedWalletInfo.wallet.id} (= {@link HolderWalletService#walletId})</li>
 *   <li>인코딩된 ownerDidDoc 안의 {@code id} == 홀더 DID (= {@link HolderWalletService#readDid})</li>
 * </ul>
 */
@Service
public class WalletEnrollService {

    private static final Logger log = LoggerFactory.getLogger(WalletEnrollService.class);
    private static final DateTimeFormatter TS = DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss'Z'");
    private static final String WALLET_PROVIDER_DID = "did:omn:wallet";
    /** 중복 등록/이미 앵커 — 멱등 처리로 성공 취급하는 TAS 에러코드. */
    private static final String WALLET_ID_ALREADY_EXISTS = "SSRVTRA17501";
    private static final String DID_ALREADY_REGISTERED = "SSRVTRA18020";
    private static final String USER_DID_ALREADY_EXISTS = "SSRVTRA17000";

    private final TasClient tas;
    private final HolderWalletService wallets;
    private final ProviderKeyService providerKey;

    public WalletEnrollService(TasClient tas, HolderWalletService wallets, ProviderKeyService providerKey) {
        this.tas = tas;
        this.wallets = wallets;
        this.providerKey = providerKey;
    }

    public record EnrollResult(String did, String walletId, String txId, String status) {}

    /**
     * 모델의 홀더 월렛을 TAS 에 등록한다. 멱등: 이미 등록된 walletId/DID 면(중복/이미앵커 에러)
     * 성공으로 취급하고 {@code status="already_registered"} 를 돌려준다.
     */
    public EnrollResult enroll(String modelId) throws Exception {
        WalletManagerInterface holderWallet = wallets.connect(modelId);
        try {
            String holderDid = wallets.readDid(modelId);
            String walletId = wallets.walletId(modelId);
            if (holderDid == null) {
                throw new IllegalStateException("no DID document for model " + modelId + " (create wallet first)");
            }

            // ownerDidDoc — 홀더 자기서명 DID doc(자기 키 proof) → multibase. 그 .id == holderDid.
            String signedDidDocJson = HolderDidDoc.selfSign(holderWallet, wallets.didDocJson(modelId));
            String ownerDidDoc = HolderCrypto.encode(signedDidDocJson.getBytes(StandardCharsets.UTF_8));

            // provider — did:omn:wallet, certVcRef=null(→ TAS cert-VC 페치 스킵)
            Provider provider = new Provider();
            provider.setDid(WALLET_PROVIDER_DID);
            provider.setCertVcRef(null);

            // proof — did:omn:wallet #assert, assertionMethod. proofValue 는 서명이 채운다.
            Proof proof = new Proof();
            proof.setType(ProofType.SECP_256R1_SIGNATURE_2018);
            proof.setCreated(now());
            proof.setVerificationMethod(WALLET_PROVIDER_DID + "?versionId=1#assert");
            proof.setProofPurpose(ProofPurpose.ASSERTION_METHOD);

            AttestedDidDoc att = new AttestedDidDoc();
            att.setWalletId(walletId);           // == SignedWalletInfo.wallet.id (create-token 조회 키)
            att.setOwnerDidDoc(ownerDidDoc);
            att.setProvider(provider);
            att.setNonce(HolderCrypto.encode(HolderCrypto.nonce16()));
            att.setProof(proof);

            // did:omn:wallet #assert 개인키로 AttestedDidDoc 실서명
            providerKey.sign(att, proof, "assert");

            try {
                WalletDtos.RegisterWalletRes res = tas.requestRegisterWallet(
                        new WalletDtos.RegisterWalletReq(TasClient.newId(), att));
                log.info("register-wallet OK model={} did={} walletId={} txId={}",
                        modelId, holderDid, walletId, res.txId());
                return new EnrollResult(holderDid, walletId, res.txId(), "registered");
            } catch (RestClientResponseException e) {
                String body = e.getResponseBodyAsString();
                if (isTolerable(body)) {
                    log.info("register-wallet already-registered (tolerated) model={} walletId={} body={}",
                            modelId, walletId, body);
                    return new EnrollResult(holderDid, walletId, null, "already_registered");
                }
                log.error("register-wallet FAILED model={} status={} body={}",
                        modelId, e.getStatusCode(), body);
                throw e;
            }
        } finally {
            holderWallet.disConnect();
        }
    }

    /** 이미 등록/앵커된 상태를 나타내는 에러코드면 멱등 성공으로 취급. */
    private static boolean isTolerable(String responseBody) {
        if (responseBody == null) return false;
        return responseBody.contains(WALLET_ID_ALREADY_EXISTS)
                || responseBody.contains(DID_ALREADY_REGISTERED)
                || responseBody.contains(USER_DID_ALREADY_EXISTS);
    }

    private static String now() {
        return TS.format(ZonedDateTime.now(ZoneOffset.UTC));
    }
}
