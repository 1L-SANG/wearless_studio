package kr.wearless.fmholder.api;

import kr.wearless.fmholder.protocol.IssueVcDtos;
import kr.wearless.fmholder.protocol.IssueVcService;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Flow B(issue-vc) — 모델 홀더에게 실 VC 를 발급. Python 백엔드가 호출한다.
 * 전제: Flow A 완주(POST /register-did). 미완주면 register-did 를 먼저 실행한다.
 *
 * <p>선택 본문 {@code {"plan":"facelicense"|"mdl","claims":{...}}}:
 * 본문 없음/plan 생략 → MDL(기존 동작, 백워드 호환). plan="facelicense" → FaceLicense 커스텀 VC.
 */
@RestController
@RequestMapping("/holder")
public class IssueController {

    private final IssueVcService issueVc;

    public IssueController(IssueVcService issueVc) {
        this.issueVc = issueVc;
    }

    /**
     * 7스텝 issue-vc 실행 → 복호된 실 VC 반환 {@code { vcId, issuer, txId, vc, status, note, userDid }}.
     * @param body 선택. 생략 시 MDL. plan="facelicense" + claims 시 FaceLicense VC.
     */
    @PostMapping("/models/{modelId}/issue-vc")
    public IssueVcService.IssueResult issueVc(@PathVariable String modelId,
                                              @RequestBody(required = false) IssueVcDtos.IssueRequest body)
            throws Exception {
        return issueVc.issue(modelId, body);
    }
}
