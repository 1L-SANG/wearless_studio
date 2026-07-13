package kr.wearless.fmholder.protocol;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClientResponseException;

/**
 * T3(선택과제, FULL) — 홀더 VC 온체인 상태 검증. Python 백엔드 verify_license() 의 온체인 arm.
 *
 * <p><b>실 온체인 상태 소스</b>: Issuer agent API {@code POST /issuer/api/v1/inspect-propose-revoke}.
 * 이 엔드포인트는 {@code storageService.getVcMetaByVcId(vcId)} → {@code contractApi.getVcMetadata}(Besu)
 * 로 <b>블록체인 VC-Meta 상태를 실제 조회</b>한다. 별도 VC-Meta 조회 HTTP 엔드포인트가 없어(체인 접근은
 * Issuer 내부 ContractApi 뿐) 이 경로를 상태 프로브로 재사용한다.
 *
 * <p><b>매핑</b>:
 * <ul>
 *   <li>HTTP 200 → VC 존재 & 미폐기 → status="valid"(verified=true, onChain=true)</li>
 *   <li>code SSRVISS00209(REVOKED_VC) → status="revoked"(verified=false, onChain=true)</li>
 *   <li>code SSRVISS00206(VC_NOT_FOUND)/00605(체인 조회 실패=미존재) → status="unknown"(onChain=false)</li>
 *   <li>그 외 오류 → status="unknown"(onChain=false)</li>
 * </ul>
 *
 * <p><b>부작용</b>: inspect-propose-revoke 는 REVOKE_VC 트랜잭션 행 1개 + RevokeVc(issuerNonce) 를
 * 생성한다(실제 폐기는 revoke-vc 를 호출해야 발생). verify 마다 미완료 트랜잭션이 남지만 무해하며,
 * 실 폐기(RevokeVcService)는 자체적으로 inspect 를 재실행해 새 nonce 를 받으므로 간섭하지 않는다.
 * 현재 스택에 체인 외 VC-Meta 조회 HTTP API 가 없어 채택한 <b>실 온체인 판정</b> 경로다.
 */
@Service
public class VerifyVcService {

    private static final Logger log = LoggerFactory.getLogger(VerifyVcService.class);

    private final IssuerAgentClient issuer;
    private final ObjectMapper om = new ObjectMapper();

    public VerifyVcService(IssuerAgentClient issuer) {
        this.issuer = issuer;
    }

    public RevokeVcDtos.VerifyResult verify(String vcId) {
        if (vcId == null || vcId.isBlank()) {
            return new RevokeVcDtos.VerifyResult(false, "unknown", false, "vcId is blank");
        }
        try {
            String body = issuer.inspectProposeRevoke(new RevokeVcDtos.InspectReq(TasClient.newId(), vcId));
            String txId = optText(body, "txId");
            log.info("verify vc={} → on-chain VC-Meta present & NOT revoked (valid). inspect txId={}", vcId, txId);
            return new RevokeVcDtos.VerifyResult(true, "valid", true,
                    "On-chain VC-Meta found and not revoked (issuer inspect-propose-revoke 200). txId=" + txId);
        } catch (RestClientResponseException e) {
            String errBody = e.getResponseBodyAsString();
            String code = optText(errBody, "code");
            if (code != null && code.contains("00209")) {                 // REVOKED_VC
                log.info("verify vc={} → on-chain VC-Meta REVOKED (code={})", vcId, code);
                return new RevokeVcDtos.VerifyResult(false, "revoked", true,
                        "On-chain VC-Meta status = REVOKED (issuer code " + code + ").");
            }
            if (code != null && (code.contains("00206") || code.contains("00605"))) { // not found on chain
                log.info("verify vc={} → VC-Meta not found on chain (code={}) → unknown", vcId, code);
                return new RevokeVcDtos.VerifyResult(false, "unknown", false,
                        "VC-Meta not found on chain (issuer code " + code + ").");
            }
            log.warn("verify vc={} → issuer error status={} body={} → unknown", vcId, e.getStatusCode(), errBody);
            return new RevokeVcDtos.VerifyResult(false, "unknown", false,
                    "Issuer inspect error (" + e.getStatusCode() + "): " + code);
        } catch (Exception e) {
            log.warn("verify vc={} → unexpected error → unknown: {}", vcId, e.toString());
            return new RevokeVcDtos.VerifyResult(false, "unknown", false, "verify failed: " + e.getMessage());
        }
    }

    /** JSON body 에서 필드 텍스트 추출(파싱 실패/부재 시 null). */
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
