package kr.wearless.fmholder.api;

import kr.wearless.fmholder.protocol.RevokeVcDtos;
import kr.wearless.fmholder.protocol.RevokeVcService;
import kr.wearless.fmholder.protocol.VerifyVcService;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * T3/T4(선택과제, FULL) — VC 라이프사이클(verify/revoke). Python 백엔드가 server-to-server 로 호출(무인증).
 *
 * <ul>
 *   <li>POST /holder/vc/verify {vcId} → {verified, status:"valid"|"revoked"|"unknown", onChain}
 *       (실 온체인 VC-Meta 상태 판정 — {@link VerifyVcService})</li>
 *   <li>POST /holder/models/{modelId}/revoke-vc {vcId} → {revoked, txId}
 *       (실 3스텝 온체인 폐기 — {@link RevokeVcService})</li>
 * </ul>
 */
@RestController
@RequestMapping("/holder")
public class VcLifecycleController {

    private final VerifyVcService verifyVc;
    private final RevokeVcService revokeVc;

    public VcLifecycleController(VerifyVcService verifyVc, RevokeVcService revokeVc) {
        this.verifyVc = verifyVc;
        this.revokeVc = revokeVc;
    }

    /** VC 온체인 상태 검증. body {vcId}. */
    @PostMapping("/vc/verify")
    public RevokeVcDtos.VerifyResult verify(@RequestBody RevokeVcDtos.VcIdReq body) {
        return verifyVc.verify(body == null ? null : body.vcId());
    }

    /** VC 온체인 폐기(3스텝). body {vcId}. modelId = 서명 키(USER DID) 로드용. */
    @PostMapping("/models/{modelId}/revoke-vc")
    public RevokeVcDtos.RevokeResult revoke(@PathVariable String modelId,
                                            @RequestBody RevokeVcDtos.VcIdReq body) throws Exception {
        return revokeVc.revoke(modelId, body == null ? null : body.vcId());
    }
}
