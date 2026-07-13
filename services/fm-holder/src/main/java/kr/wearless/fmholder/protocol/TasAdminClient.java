package kr.wearless.fmholder.protocol;

import com.fasterxml.jackson.annotation.JsonInclude;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

import java.util.List;

/**
 * TAS 관리자 프로토콜(/tas/admin/v1) 클라이언트 — DID 온체인 부트스트랩용.
 * 신규 DID 온체인 앵커: register-did/public(저장) → entities/list(entityId 조회) → approve-did(앵커).
 * dev 스택 TAS 는 anyRequest().permitAll() 이라 인증 헤더 불필요.
 */
@Component
public class TasAdminClient {

    private final RestClient http;

    public TasAdminClient(@Value("${opendid.tas-url}") String tasUrl) {
        this.http = RestClient.builder()
                .baseUrl(tasUrl + "/tas/admin/v1")
                .defaultHeader("Content-Type", MediaType.APPLICATION_JSON_VALUE)
                .defaultHeader("Accept", MediaType.APPLICATION_JSON_VALUE)
                .build();
    }

    @JsonInclude(JsonInclude.Include.NON_NULL)
    public record RegisterDidReq(String didDoc, String name, String role, String serverUrl, String certificateUrl) {}

    /** KYC 서버 등록 요청(RegisterKycReqDto). 서버는 단일행 upsert + enabled=true 강제. */
    public record RegisterKycReq(String name, String serverUrl) {}

    /** KYC 서버 조회 응답(KycInfoDto). 미등록이면 필드가 null. */
    public record KycInfo(Long id, String name, String serverUrl, Boolean enabled) {}

    public record ApproveDidReq(Long entityId) {}

    public record EntityInfo(Long id, String did, String name, String role, String status) {}

    public record EntityPage(List<EntityInfo> content) {}

    /** DID doc(multibase) 를 엔티티로 등록(온체인 아님 — 저장). */
    public void registerDidPublic(RegisterDidReq req) {
        http.post().uri("/entities/register-did/public").body(req).retrieve().toBodilessEntity();
    }

    /** DID 로 엔티티 검색 → entityId 조회. */
    public EntityInfo findEntityByDid(String did) {
        EntityPage page = http.get()
                .uri(u -> u.path("/entities/list").queryParam("searchKey", "did").queryParam("searchValue", did).build())
                .retrieve().body(EntityPage.class);
        if (page == null || page.content() == null) return null;
        return page.content().stream().filter(e -> did.equals(e.did())).findFirst().orElse(null);
    }

    /** 엔티티 DID 승인 → 온체인 앵커(storageService.registerDidDoc). */
    public void approveDid(Long entityId) {
        http.post().uri("/entities/approve-did").body(new ApproveDidReq(entityId)).retrieve().toBodilessEntity();
    }

    /** 현재 등록된 KYC 서버 조회(GET /kycs). 미등록이면 필드가 전부 null 인 KycInfo. */
    public KycInfo getKyc() {
        return http.get().uri("/kycs").retrieve().body(KycInfo.class);
    }

    /** KYC 서버 등록/갱신(POST /kycs) — retrieve-kyc 의 PII 조회 대상 서버. */
    public KycInfo registerKyc(RegisterKycReq req) {
        return http.post().uri("/kycs").body(req).retrieve().body(KycInfo.class);
    }
}
