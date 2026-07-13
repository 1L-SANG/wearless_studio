package kr.wearless.fmholder.protocol;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

/**
 * Issuer(:8091) <b>agent</b> API HTTP 클라이언트. base path {@code /issuer/api/v1} (dev = permitAll, 무인증).
 *
 * <p>{@link IssuerAdminClient}(=/issuer/admin/v1, user upsert) 와 달리, 이 클라이언트는 VC 상태/폐기
 * 프로토콜(VcController)을 호출한다. 세 스텝(inspect-propose-revoke → revoke-vc → complete-revoke)이
 * 온체인 VC-Meta 상태를 실제로 조회/변경한다(Besu, {@code contractApi.getVcMetadata/updateVcStatus}).
 *
 * <p>{@code inspect-propose-revoke} 는 {@code getVcMetaByVcId}(체인 조회)로 상태를 읽으므로 verify 의
 * 실 온체인 상태 소스로도 쓴다: 200=미폐기(valid), REVOKED_VC=폐기, VC 없음(00206/00605)=unknown.
 */
@Component
public class IssuerAgentClient {

    private final RestClient http;

    public IssuerAgentClient(@Value("${opendid.issuer-url}") String issuerUrl) {
        this.http = RestClient.builder()
                .baseUrl(issuerUrl + "/issuer/api/v1")
                .defaultHeader("Content-Type", MediaType.APPLICATION_JSON_VALUE)
                .defaultHeader("Accept", MediaType.APPLICATION_JSON_VALUE)
                .build();
    }

    /**
     * POST /inspect-propose-revoke — 온체인 VC-Meta 조회 후 폐기 트랜잭션 propose.
     * 성공(200) = VC 존재 & 미폐기. 응답 원문 JSON({txId, issuerNonce, authType}) 반환.
     * 실패 시 {@link org.springframework.web.client.RestClientResponseException}
     * (body {@code {code, description}} — REVOKED_VC=SSRVISS00209, VC 없음=SSRVISS00206/00605).
     */
    public String inspectProposeRevoke(RevokeVcDtos.InspectReq req) {
        return http.post().uri("/inspect-propose-revoke").body(req).retrieve().body(String.class);
    }

    /** POST /revoke-vc — 홀더(USER DID) 서명 proof 로 폐기. 성공 시 {@code {txId}} 반환(온체인 status=REVOKED). */
    public String revokeVc(RevokeVcDtos.RevokeReq req) {
        return http.post().uri("/revoke-vc").body(req).retrieve().body(String.class);
    }

    /** POST /complete-revoke — 폐기 트랜잭션 종료. {@code {txId}} 반환. */
    public String completeRevoke(RevokeVcDtos.CompleteReq req) {
        return http.post().uri("/complete-revoke").body(req).retrieve().body(String.class);
    }
}
