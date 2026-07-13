package kr.wearless.fmholder.protocol;

import com.fasterxml.jackson.annotation.JsonInclude;
import org.omnione.did.base.datamodel.data.AccEcdh;
import org.omnione.did.base.datamodel.data.EcdhReqData;
import org.omnione.did.base.datamodel.data.ServerTokenSeed;
import org.omnione.did.base.datamodel.data.SignedDidDoc;

/**
 * TAS register-user (Flow A) 요청/응답 DTO — 서버 {@code tas.v1.agent.dto.user} 와이어 포맷 미러.
 * 중첩 타입(EcdhReqData·ServerTokenSeed·SignedDidDoc·AccEcdh)은 TAS 정본 datamodel 재사용(직렬화 호환).
 */
public final class RegisterUserDtos {
    private RegisterUserDtos() {}

    @JsonInclude(JsonInclude.Include.NON_NULL)
    public record ProposeReq(String id) {}

    public record ProposeRes(String txId) {}

    @JsonInclude(JsonInclude.Include.NON_NULL)
    public record EcdhReq(String id, String txId, EcdhReqData reqEcdh) {}

    public record EcdhRes(String txId, AccEcdh accEcdh) {}

    @JsonInclude(JsonInclude.Include.NON_NULL)
    public record CreateTokenReq(String id, String txId, ServerTokenSeed seed) {}

    public record CreateTokenRes(String id, String txId, String iv, String encStd) {}

    @JsonInclude(JsonInclude.Include.NON_NULL)
    public record RetrieveKycReq(String id, String txId, String serverToken, String kycTxId) {}

    public record RetrieveKycRes(String txId) {}

    @JsonInclude(JsonInclude.Include.NON_NULL)
    public record RegisterUserReq(String id, String txId, String serverToken, SignedDidDoc signedDidDoc) {}

    public record RegisterUserRes(String txId) {}

    @JsonInclude(JsonInclude.Include.NON_NULL)
    public record ConfirmReq(String id, String txId, String serverToken) {}

    public record ConfirmRes(String txId) {}
}
