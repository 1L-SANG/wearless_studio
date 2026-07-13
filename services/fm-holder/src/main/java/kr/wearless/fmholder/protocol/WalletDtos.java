package kr.wearless.fmholder.protocol;

import com.fasterxml.jackson.annotation.JsonInclude;
import org.omnione.did.base.datamodel.data.AttestedDidDoc;

/**
 * TAS request-register-wallet(월렛 등록) 요청/응답 DTO — 서버
 * {@code tas.v1.agent.dto.wallet.RegisterWalletReqDto/RegisterWalletResDto} 와이어 포맷 미러.
 * 중첩 {@link AttestedDidDoc} 는 TAS 정본 datamodel 재사용(직렬화 호환).
 */
public final class WalletDtos {
    private WalletDtos() {}

    @JsonInclude(JsonInclude.Include.NON_NULL)
    public record RegisterWalletReq(String id, AttestedDidDoc attestedDidDoc) {}

    public record RegisterWalletRes(String txId) {}
}
