package kr.wearless.fmholder.protocol;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

/**
 * CAS(:8094) 엔티티 프로토콜 HTTP 클라이언트 — KYC(PII) seed/조회. base path {@code /cas/api/v1}.
 *
 * <p>retrieve-kyc(TAS)는 kyc 테이블 serverUrl + {@code /api/v1/retrieve-pii} 로 POST 해 사람 PII 를
 * 가져온다. 이 dev 스택에선 그 KYC 서버 = CAS. 커스터디얼 홀더는 실제 KYC 세션이 없으므로,
 * 모델별 결정적 kycTxId(=userId)로 CAS UserPii 를 1회 seed 한다(dev pii = 임의 hex).
 */
@Component
public class CasEntityClient {

    private final RestClient http;

    public CasEntityClient(@Value("${opendid.cas-url}") String casUrl) {
        this.http = RestClient.builder()
                .baseUrl(casUrl + "/cas/api/v1")
                .defaultHeader("Content-Type", MediaType.APPLICATION_JSON_VALUE)
                .defaultHeader("Accept", MediaType.APPLICATION_JSON_VALUE)
                .build();
    }

    /** SaveUserInfoDto{userId, pii}. */
    public record SaveUserInfoReq(String userId, String pii) {}

    /** RetrievePiiReqDto{userId}. */
    public record RetrievePiiReq(String userId) {}

    /** RetrievePiiResDto{pii}. */
    public record RetrievePiiRes(String pii) {}

    /** CAS UserPii 저장(POST /save-user-info). 응답 본문 없음. */
    public void saveUserInfo(String userId, String pii) {
        http.post().uri("/save-user-info").body(new SaveUserInfoReq(userId, pii)).retrieve().toBodilessEntity();
    }

    /** CAS UserPii 조회(POST /retrieve-pii). 미존재 시 HTTP 400(USER_PII_NOT_FOUND). */
    public RetrievePiiRes retrievePii(String userId) {
        return http.post().uri("/retrieve-pii").body(new RetrievePiiReq(userId)).retrieve().body(RetrievePiiRes.class);
    }
}
