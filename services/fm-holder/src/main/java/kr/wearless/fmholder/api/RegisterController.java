package kr.wearless.fmholder.api;

import kr.wearless.fmholder.protocol.DidAnchorService;
import kr.wearless.fmholder.protocol.RegisterUserDtos;
import kr.wearless.fmholder.protocol.RegisterUserService;
import kr.wearless.fmholder.protocol.TasClient;
import kr.wearless.fmholder.protocol.WalletEnrollService;
import kr.wearless.fmholder.wallet.HolderWalletService;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

/**
 * 홀더 DID 온체인 등록(register-user, Flow A). Python 백엔드가 호출한다.
 * 실행 중 TAS 가 sample 프로파일이면 크립토·온체인이 목킹된다(구조 검증만) — 실 등록은 dev 프로파일.
 */
@RestController
@RequestMapping("/holder")
public class RegisterController {

    private final TasClient tas;
    private final RegisterUserService registerUser;
    private final DidAnchorService didAnchor;
    private final WalletEnrollService walletEnroll;
    private final HolderWalletService wallets;

    public RegisterController(TasClient tas, RegisterUserService registerUser,
                             DidAnchorService didAnchor, WalletEnrollService walletEnroll,
                             HolderWalletService wallets) {
        this.tas = tas;
        this.registerUser = registerUser;
        this.didAnchor = didAnchor;
        this.walletEnroll = walletEnroll;
        this.wallets = wallets;
    }

    /** 홀더 DID 를 TAS 관리자 부트스트랩으로 온체인 앵커(register-did/public → approve-did). */
    @PostMapping("/models/{modelId}/anchor-did")
    public DidAnchorService.AnchorResult anchorDid(@PathVariable String modelId) throws Exception {
        return didAnchor.anchor(modelId);
    }

    /**
     * US-1: 홀더 월렛을 TAS 에 등록(request-register-wallet) — SSRVTRA17502 해소.
     * register-wallet 이 홀더 DID doc 도 온체인 앵커한다. 격리 테스트용 단독 엔드포인트.
     */
    @PostMapping("/models/{modelId}/register-wallet")
    public WalletEnrollService.EnrollResult registerWallet(@PathVariable String modelId) throws Exception {
        return walletEnroll.enroll(modelId);
    }

    /** propose-register-user 단독 라이브 확인 — TAS 연결 + txId 발급. */
    @PostMapping("/register/propose")
    public Map<String, Object> propose() {
        String id = TasClient.newId();
        RegisterUserDtos.ProposeRes res = tas.proposeRegisterUser(new RegisterUserDtos.ProposeReq(id));
        return Map.of("id", id, "txId", res.txId());
    }

    /**
     * 모델의 홀더 DID 를 TAS register-user 5스텝으로 등록. (월렛은 P1에서 생성돼 있어야 함)
     * @param force true 면 Flow A 완주 마커를 먼저 삭제해 강제 재실행한다(dev-DB 리셋 후 stale 마커
     *              short-circuit 방지, 아키텍트 Rec 3). 기본 false = 마커 있으면 멱등 short-circuit.
     */
    @PostMapping("/models/{modelId}/register-did")
    public RegisterUserService.RegisterResult registerDid(
            @PathVariable String modelId,
            @RequestParam(name = "force", required = false, defaultValue = "false") boolean force)
            throws Exception {
        if (force) {
            wallets.clearFlowAComplete(modelId);
        }
        return registerUser.register(modelId);
    }
}
