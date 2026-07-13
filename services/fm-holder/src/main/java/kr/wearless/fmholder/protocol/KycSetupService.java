package kr.wearless.fmholder.protocol;

import org.omnione.did.base.util.BaseDigestUtil;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClientResponseException;

import java.nio.charset.StandardCharsets;

/**
 * Flow A retrieve-kyc(A4)를 위한 1회성 KYC 인프라 셋업(멱등).
 *
 * <p>확정 사실: retrieve-kyc 의 KYC 서버 = 실행 중인 CAS(:8094). TAS(UserServiceImpl.requestUserPii)는
 * {@code kyc.serverUrl + "/api/v1/retrieve-pii"} 로 POST(userId=kycTxId)해 PII 를 조회한다. 그러므로:
 * <ol>
 *   <li>TAS kyc 행 등록: {@code serverUrl = http://localhost:8094/cas} → TAS 가 붙이는
 *       {@code /api/v1/retrieve-pii} 와 합쳐 {@code http://localhost:8094/cas/api/v1/retrieve-pii}
 *       (= CasController RETRIEVE_PII 실엔드포인트)로 해소된다.</li>
 *   <li>CAS UserPii seed: {@code POST /cas/api/v1/save-user-info {userId=kycTxId, pii=hex}}.
 *       kycTxId 는 암호 바인딩이 아닌 임의 문자열(모델별 결정적), pii 는 dev 용 임의 hex.</li>
 * </ol>
 *
 * <p>멱등: kyc 행은 이미 원하는 serverUrl 이면 재등록 생략(단일행 upsert 라 재POST 도 무해).
 * UserPii 는 조회(retrieve-pii)로 존재 확인 후 없을 때만 저장(중복 행 방지).
 */
@Service
public class KycSetupService {

    private static final Logger log = LoggerFactory.getLogger(KycSetupService.class);

    private final TasAdminClient admin;
    private final CasEntityClient cas;
    private final String kycName;
    private final String kycServerUrl;

    public KycSetupService(
            TasAdminClient admin, CasEntityClient cas,
            @Value("${opendid.kyc.name:fm-holder-cas}") String kycName,
            @Value("${opendid.kyc.cas-server-url}") String kycServerUrl) {
        this.admin = admin;
        this.cas = cas;
        this.kycName = kycName;
        this.kycServerUrl = kycServerUrl;
    }

    /** 모델별 결정적 kycTxId(= CAS userId). 암호 바인딩 아님 — 사람이 읽을 수 있게 모델ID 기반. */
    public String kycTxId(String modelId) {
        return "fm-holder-" + modelId;
    }

    /**
     * 모델의 TAS user.pii = Flow A 가 CAS→TAS 로 심은 값 = SHA-256("fm-holder-&lt;modelId&gt;") hex.
     * Issuer user upsert(ISSUER_INIT findByPii)가 이 값과 일치해야 발급 시 user 를 찾는다.
     */
    public String modelPii(String modelId) {
        return devPii(kycTxId(modelId));
    }

    /** 모델별 dev PII(임의 hex) = SHA-256(kycTxId) hex. 실제 사람 PII 아님(dev 용). */
    private String devPii(String kycTxId) {
        byte[] h = BaseDigestUtil.generateHash(kycTxId.getBytes(StandardCharsets.UTF_8));
        StringBuilder sb = new StringBuilder(h.length * 2);
        for (byte b : h) sb.append(String.format("%02x", b));
        return sb.toString();
    }

    /**
     * KYC 인프라를 보장한다(멱등). 반환값 = 이 모델의 kycTxId(retrieve-kyc 요청에 사용).
     */
    public String ensure(String modelId) {
        ensureKycRow();
        String kycTxId = kycTxId(modelId);
        ensureUserPii(kycTxId);
        return kycTxId;
    }

    /** TAS kyc 행이 원하는 serverUrl 로 존재하도록 보장. */
    private void ensureKycRow() {
        try {
            TasAdminClient.KycInfo cur = admin.getKyc();
            if (cur != null && kycServerUrl.equals(cur.serverUrl()) && Boolean.TRUE.equals(cur.enabled())) {
                log.debug("kyc row already set (serverUrl={})", cur.serverUrl());
                return;
            }
            TasAdminClient.KycInfo res = admin.registerKyc(new TasAdminClient.RegisterKycReq(kycName, kycServerUrl));
            log.info("kyc row registered: name={} serverUrl={} (id={})", kycName, kycServerUrl,
                    res == null ? null : res.id());
        } catch (RestClientResponseException e) {
            log.error("kyc row setup FAILED status={} body={}", e.getStatusCode(), e.getResponseBodyAsString());
            throw e;
        }
    }

    /** CAS UserPii 가 없으면 seed(멱등). retrieve-pii 200 = 존재, 4xx = 없음 → save. */
    private void ensureUserPii(String kycTxId) {
        try {
            cas.retrievePii(kycTxId);
            log.debug("user pii already seeded for kycTxId={}", kycTxId);
        } catch (RestClientResponseException e) {
            if (e.getStatusCode().is4xxClientError()) {
                cas.saveUserInfo(kycTxId, devPii(kycTxId));
                log.info("user pii seeded for kycTxId={}", kycTxId);
            } else {
                log.error("retrieve-pii probe FAILED status={} body={}", e.getStatusCode(), e.getResponseBodyAsString());
                throw e;
            }
        }
    }
}
