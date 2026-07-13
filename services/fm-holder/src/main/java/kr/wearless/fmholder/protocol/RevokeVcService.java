package kr.wearless.fmholder.protocol;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import kr.wearless.fmholder.wallet.HolderWalletService;
import org.omnione.did.base.datamodel.data.Proof;
import org.omnione.did.base.datamodel.data.ReqRevokeVc;
import org.omnione.did.base.datamodel.enums.ProofPurpose;
import org.omnione.did.base.datamodel.enums.ProofType;
import org.omnione.did.wallet.key.WalletManagerInterface;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClientResponseException;

import java.time.ZoneOffset;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;

/**
 * T4(선택과제, FULL) — 홀더 VC 온체인 폐기. Python 백엔드 revoke 엔드포인트의 홀더 arm.
 *
 * <p><b>실 3스텝 폐기 프로토콜</b>(Issuer agent API {@code /issuer/api/v1}, VcController):
 * <ol>
 *   <li><b>inspect-propose-revoke</b> {id, vcId} → {txId, issuerNonce, authType}.
 *       Issuer 가 온체인 VC-Meta 를 조회해(미폐기 확인) 폐기 트랜잭션을 열고 issuerNonce 를 발급.</li>
 *   <li><b>revoke-vc</b> {id, txId, request=ReqRevokeVc{vcId, issuerNonce, proof}} → {txId}.
 *       proof = <b>USER DID(VC subject) #assert</b> 실서명. Issuer 는 verificationMethod 로 온체인 USER DID
 *       doc 을 조회({@code storageService.findDidDoc})해 서명을 검증하고, issuerNonce/vcId 일치 확인 후
 *       {@code contractApi.updateVcStatus(vcId, REVOKED)} 로 <b>온체인 VC-Meta 상태를 REVOKED 로 변경</b>.</li>
 *   <li><b>complete-revoke</b> {id, txId} → {txId}. 트랜잭션 종료.</li>
 * </ol>
 *
 * <p>서명 호환성: 홀더 {@link HolderCrypto#sign}(serializeAndSort→SHA256→compact sig, NON_NULL)은 서버
 * {@code ValidationUtil.verifySign}(RequestProof, 동일 serializeAndSort)과 바이트 동일 정규화를 쓴다.
 * USER 월렛은 keyagree/auth/<b>assert</b>/invoke 4키를 보유하고 USER DID doc 은 Flow A 에서 온체인 앵커됨.
 * (IssueVcService 의 didAuth USER #auth 서명이 이미 라이브 수용되는 것과 동일 신뢰 경계.)
 */
@Service
public class RevokeVcService {

    private static final Logger log = LoggerFactory.getLogger(RevokeVcService.class);
    private static final DateTimeFormatter TS = DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss'Z'");

    private final IssuerAgentClient issuer;
    private final HolderWalletService wallets;
    private final ObjectMapper om = new ObjectMapper();

    public RevokeVcService(IssuerAgentClient issuer, HolderWalletService wallets) {
        this.issuer = issuer;
        this.wallets = wallets;
    }

    /**
     * 모델의 VC 를 온체인 폐기한다. modelId 는 서명 키(USER 월렛/DID)를 로드하기 위한 것.
     * @param modelId 홀더 모델 id(경로변수). @param vcId 폐기할 VC id.
     */
    public RevokeVcDtos.RevokeResult revoke(String modelId, String vcId) throws Exception {
        if (vcId == null || vcId.isBlank()) {
            throw new IllegalArgumentException("vcId is required");
        }
        if (!wallets.exists(modelId)) {
            throw new IllegalStateException("no wallet for model " + modelId
                    + " (issue-vc must have run before revoke)");
        }
        String userDid = wallets.readUserDid(modelId);
        if (userDid == null) {
            throw new IllegalStateException("no USER DID for model " + modelId
                    + " (Flow A/issue-vc must have run before revoke)");
        }

        WalletManagerInterface userWallet = wallets.connectUser(modelId);
        try {
            // 1. inspect-propose-revoke — 온체인 VC-Meta 조회 + txId/issuerNonce 발급.
            String inspect;
            try {
                inspect = issuer.inspectProposeRevoke(new RevokeVcDtos.InspectReq(TasClient.newId(), vcId));
            } catch (RestClientResponseException e) {
                String code = optText(e.getResponseBodyAsString(), "code");
                if (code != null && code.contains("00209")) {          // already REVOKED_VC — 멱등 성공 처리
                    log.info("revoke model={} vc={} — already revoked on-chain (code={})", modelId, vcId, code);
                    return new RevokeVcDtos.RevokeResult(true, null, "revoked",
                            "VC already revoked on-chain (issuer code " + code + "). Idempotent no-op.");
                }
                throw e;
            }
            String txId = text(inspect, "txId");
            String issuerNonce = text(inspect, "issuerNonce");
            log.info("revoke R1 inspect-propose-revoke OK model={} vc={} txId={}", modelId, vcId, txId);

            // 2. revoke-vc — ReqRevokeVc 를 USER DID #assert 로 실서명.
            String vm = userDid + "?versionId=1#assert";
            Proof proof = new Proof();
            proof.setType(ProofType.SECP_256R1_SIGNATURE_2018);
            proof.setCreated(now());
            proof.setVerificationMethod(vm);
            proof.setProofPurpose(ProofPurpose.ASSERTION_METHOD);

            ReqRevokeVc reqRevokeVc = ReqRevokeVc.builder()
                    .vcId(vcId)
                    .issuerNonce(issuerNonce)
                    .proof(proof)
                    .build();                                    // proofs 미설정(null → NON_NULL 직렬화서 생략)
            HolderCrypto.sign(reqRevokeVc, proof, userWallet, "assert");

            String revokeRes = issuer.revokeVc(new RevokeVcDtos.RevokeReq(TasClient.newId(), txId, reqRevokeVc));
            log.info("revoke R2 revoke-vc OK model={} vc={} (USER DID {} #assert signed) res={}",
                    modelId, vcId, userDid, revokeRes);

            // 3. complete-revoke — 트랜잭션 종료.
            String completeRes = issuer.completeRevoke(new RevokeVcDtos.CompleteReq(TasClient.newId(), txId));
            log.info("revoke R3 complete-revoke OK model={} vc={} — REVOKE COMPLETE txId={}", modelId, vcId, txId);

            String note = "On-chain VC-Meta set to REVOKED via issuer 3-step (inspect→revoke-vc→complete-revoke). "
                    + "USER DID " + userDid + " #assert signed. txId=" + txId;
            return new RevokeVcDtos.RevokeResult(true, txId, "revoked", note);
        } catch (RestClientResponseException e) {
            log.error("revoke FAILED model={} vc={} status={} body={}",
                    modelId, vcId, e.getStatusCode(), e.getResponseBodyAsString());
            throw e;
        } finally {
            userWallet.disConnect();
        }
    }

    private static String now() {
        return TS.format(ZonedDateTime.now(ZoneOffset.UTC));
    }

    private String text(String json, String field) {
        String v = optText(json, field);
        if (v == null) throw new IllegalStateException("missing '" + field + "' in issuer response: " + json);
        return v;
    }

    private String optText(String json, String field) {
        try {
            if (json == null || json.isBlank()) return null;
            JsonNode n = om.readTree(json).get(field);
            return (n == null || n.isNull()) ? null : n.asText();
        } catch (Exception e) {
            return null;
        }
    }
}
