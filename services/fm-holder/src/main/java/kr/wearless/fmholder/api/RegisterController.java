package kr.wearless.fmholder.api;

import kr.wearless.fmholder.protocol.RegisterUserDtos;
import kr.wearless.fmholder.protocol.RegisterUserService;
import kr.wearless.fmholder.protocol.TasClient;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
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

    public RegisterController(TasClient tas, RegisterUserService registerUser) {
        this.tas = tas;
        this.registerUser = registerUser;
    }

    /** propose-register-user 단독 라이브 확인 — TAS 연결 + txId 발급. */
    @PostMapping("/register/propose")
    public Map<String, Object> propose() {
        String id = TasClient.newId();
        RegisterUserDtos.ProposeRes res = tas.proposeRegisterUser(new RegisterUserDtos.ProposeReq(id));
        return Map.of("id", id, "txId", res.txId());
    }

    /** 모델의 홀더 DID 를 TAS register-user 5스텝으로 등록. (월렛은 P1에서 생성돼 있어야 함) */
    @PostMapping("/models/{modelId}/register-did")
    public RegisterUserService.RegisterResult registerDid(@PathVariable String modelId) throws Exception {
        return registerUser.register(modelId);
    }
}
