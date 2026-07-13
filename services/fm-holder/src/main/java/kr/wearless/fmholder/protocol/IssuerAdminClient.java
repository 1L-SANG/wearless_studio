package kr.wearless.fmholder.protocol;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

/**
 * Issuer(:8091) admin API HTTP 클라이언트. base path {@code /issuer/admin/v1} (dev = permitAll, 무인증).
 *
 * <p>발급 전 모델별 Issuer user 를 upsert 하는 데 쓴다. ISSUER_INIT 발급 플랜은 발급 시
 * {@code findByPiiAndVcSchemaId(holder.pii, vcSchemaId)}(IssueInitIssueService)로 user 를 조회해
 * VC credentialSubject 를 채우므로, 발급 전에 (pii, vcSchemaId) 로 user 가 존재해야 한다. Flow A 가
 * TAS user.pii = SHA256("fm-holder-&lt;modelId&gt;") 를 심으므로 Issuer user 도 같은 pii 로 만든다.
 *
 * <p>{@code /users/demo} 는 (did|pii, vcSchemaId)로 findOrNew 후 save 하는 <b>멱등 upsert</b> 이며
 * pii 를 재해시하지 않고 그대로 저장한다({@code /users} 는 firstName/lastName 해시로 pii 를 재계산하므로
 * TAS pii 와 불일치 — 반드시 {@code /demo} 를 쓴다).
 */
@Component
public class IssuerAdminClient {

    private final RestClient http;

    public IssuerAdminClient(@Value("${opendid.issuer-url}") String issuerUrl) {
        this.http = RestClient.builder()
                .baseUrl(issuerUrl + "/issuer/admin/v1")
                .defaultHeader("Content-Type", MediaType.APPLICATION_JSON_VALUE)
                .defaultHeader("Accept", MediaType.APPLICATION_JSON_VALUE)
                .build();
    }

    /**
     * CreateUserInfoFromDemoReqDto{did, pii, vcSchemaId, userInfo}.
     * @param vcSchemaId 스키마 문자열 id("mdl"/"facelicense") — 서버가 numeric id 로 해소.
     * @param userInfo   claim JSON 문자열: {@code {"namespaceId.claimId": "value", ...}} (user.data 에 저장).
     */
    public record DemoUserReq(String did, String pii, String vcSchemaId, String userInfo) {}

    /** POST /users/demo — 모델별 Issuer user upsert(멱등). 응답 본문 없음(200). */
    public void upsertDemoUser(DemoUserReq req) {
        http.post().uri("/users/demo").body(req).retrieve().toBodilessEntity();
    }
}
