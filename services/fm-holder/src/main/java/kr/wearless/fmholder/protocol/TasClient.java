package kr.wearless.fmholder.protocol;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

import java.security.SecureRandom;
import java.time.ZonedDateTime;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;

/**
 * OpenDID TAS(:8090) 에이전트 프로토콜 HTTP 클라이언트. base path {@code /tas/api/v1}.
 * 모든 호출 = POST application/json, 인증 헤더 없음(본문 DID 서명/ECDH 로 자가인증).
 */
@Component
public class TasClient {

    private static final DateTimeFormatter ID_TS =
            DateTimeFormatter.ofPattern("yyyyMMddHHmmssSSS").withZone(ZoneOffset.UTC);
    private static final SecureRandom RND = new SecureRandom();

    private final RestClient http;

    public TasClient(@Value("${opendid.tas-url}") String tasUrl) {
        this.http = RestClient.builder()
                .baseUrl(tasUrl + "/tas/api/v1")
                .defaultHeader("Content-Type", MediaType.APPLICATION_JSON_VALUE)
                .defaultHeader("Accept", MediaType.APPLICATION_JSON_VALUE)
                .build();
    }

    /** 28자 타임스탬프 메시지 id (서버 예시 포맷: yyyyMMddHHmmssSSS + 11 hex). */
    public static String newId() {
        StringBuilder sb = new StringBuilder(ID_TS.format(ZonedDateTime.now(ZoneOffset.UTC)));
        while (sb.length() < 28) sb.append(Integer.toHexString(RND.nextInt(16)).toUpperCase());
        return sb.substring(0, 28);
    }

    private <T> T post(String path, Object body, Class<T> res) {
        return http.post().uri(path).body(body).retrieve().body(res);
    }

    // ── Flow A: register-user ────────────────────────────────────
    public RegisterUserDtos.ProposeRes proposeRegisterUser(RegisterUserDtos.ProposeReq req) {
        return post("/propose-register-user", req, RegisterUserDtos.ProposeRes.class);
    }

    public RegisterUserDtos.EcdhRes requestEcdh(RegisterUserDtos.EcdhReq req) {
        return post("/request-ecdh", req, RegisterUserDtos.EcdhRes.class);
    }

    public RegisterUserDtos.CreateTokenRes requestCreateToken(RegisterUserDtos.CreateTokenReq req) {
        return post("/request-create-token", req, RegisterUserDtos.CreateTokenRes.class);
    }

    public RegisterUserDtos.RetrieveKycRes retrieveKyc(RegisterUserDtos.RetrieveKycReq req) {
        return post("/retrieve-kyc", req, RegisterUserDtos.RetrieveKycRes.class);
    }

    public RegisterUserDtos.RegisterUserRes requestRegisterUser(RegisterUserDtos.RegisterUserReq req) {
        return post("/request-register-user", req, RegisterUserDtos.RegisterUserRes.class);
    }

    public RegisterUserDtos.ConfirmRes confirmRegisterUser(RegisterUserDtos.ConfirmReq req) {
        return post("/confirm-register-user", req, RegisterUserDtos.ConfirmRes.class);
    }

    // ── Wallet enroll (once per holder) ──────────────────────────
    public WalletDtos.RegisterWalletRes requestRegisterWallet(WalletDtos.RegisterWalletReq req) {
        return post("/request-register-wallet", req, WalletDtos.RegisterWalletRes.class);
    }

    // ── Flow B: issue-vc ─────────────────────────────────────────
    // request-ecdh / request-create-token 은 Flow A 와 동일 엔드포인트라 위 메서드를 재사용한다.
    public IssueVcDtos.OfferRes offerIssueVcQr(IssueVcDtos.OfferReq req) {
        return post("/offer-issue-vc/qr", req, IssueVcDtos.OfferRes.class);
    }

    public IssueVcDtos.ProposeRes proposeIssueVc(IssueVcDtos.ProposeReq req) {
        return post("/propose-issue-vc", req, IssueVcDtos.ProposeRes.class);
    }

    /** request-issue-profile 은 TAS 가 Gson 원문 JSON 문자열로 응답(VcController toJson) → 원문 반환. */
    public String requestIssueProfile(IssueVcDtos.ProfileReq req) {
        return post("/request-issue-profile", req, String.class);
    }

    public IssueVcDtos.IssueVcRes requestIssueVc(IssueVcDtos.IssueVcReq req) {
        return post("/request-issue-vc", req, IssueVcDtos.IssueVcRes.class);
    }

    public IssueVcDtos.ConfirmRes confirmIssueVc(IssueVcDtos.ConfirmReq req) {
        return post("/confirm-issue-vc", req, IssueVcDtos.ConfirmRes.class);
    }
}
