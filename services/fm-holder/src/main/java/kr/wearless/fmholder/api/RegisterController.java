package kr.wearless.fmholder.api;

import kr.wearless.fmholder.protocol.RegisterUserDtos;
import kr.wearless.fmholder.protocol.TasClient;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

/**
 * 홀더 DID 온체인 등록(register-user, Flow A) 트리거. Python 백엔드가 호출한다.
 * P2 진행 중 — 현재 propose 단계까지 라이브 검증(txId 발급). 이후 ecdh→token→register→confirm 확장.
 */
@RestController
@RequestMapping("/holder/register")
public class RegisterController {

    private final TasClient tas;

    public RegisterController(TasClient tas) {
        this.tas = tas;
    }

    /** propose-register-user 단독 라이브 확인 — TAS 연결 + txId 발급 증명. */
    @PostMapping("/propose")
    public Map<String, Object> propose() {
        String id = TasClient.newId();
        RegisterUserDtos.ProposeRes res = tas.proposeRegisterUser(new RegisterUserDtos.ProposeReq(id));
        return Map.of("id", id, "txId", res.txId());
    }
}
