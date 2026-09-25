package kr.wearless.fmholder.api;

import kr.wearless.fmholder.protocol.IssuePlan;
import kr.wearless.fmholder.protocol.IssueVcDtos;
import kr.wearless.fmholder.protocol.IssueVcService;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Flow B(issue-vc) — 모델 홀더에게 실 VC 를 발급. Python 백엔드가 호출한다.
 * 전제: Flow A 완주(POST /register-did). 미완주면 register-did 를 먼저 실행한다.
 *
 * <p>선택 본문 {@code {"plan":"facelicense-v2"|"fmsponsorship-v1"|"mdl","claims":{...},"idempotencyKey":...}}:
 * 본문 없음/plan 생략 → MDL(기존 동작, 백워드 호환, 멱등키 없음). 멱등 플랜({@link IssuePlan#requiresIdempotencyKey})
 * 은 플랜별 키(fm-license:/fm-sponsorship: + 소문자 UUID) 필수 + 파일 멱등 저장소 경유.
 * 모르는 plan 은 서비스가 거절한다(기존 동작).
 */
@RestController
@RequestMapping("/holder")
public class IssueController {
    private final IssueVcService issueVc;
    private final IssueIdempotencyStore idempotency;

    public IssueController(IssueVcService issueVc, IssueIdempotencyStore idempotency) {
        this.issueVc = issueVc;
        this.idempotency = idempotency;
    }

    /**
     * 7스텝 issue-vc 실행 → 복호된 실 VC 반환 {@code { vcId, issuer, txId, vc, status, note, userDid }}.
     * @param body 선택. 생략 시 MDL. plan="facelicense-v2"/"fmsponsorship-v1" + claims + idempotencyKey.
     */
    @PostMapping("/models/{modelId}/issue-vc")
    public IssueVcService.IssueResult issueVc(@PathVariable String modelId,
                                              @RequestBody(required = false) IssueVcDtos.IssueRequest body)
            throws Exception {
        IssuePlan plan = body == null ? null : IssuePlan.fromRequest(body.plan()).orElse(null);
        if (plan == null || !plan.requiresIdempotencyKey()) {
            return issueVc.issue(modelId, body);
        }
        if (!plan.isValidIdempotencyKey(body.idempotencyKey())) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "invalid_idempotency_key");
        }
        try {
            return idempotency.execute(modelId, body, () -> issueVc.issue(modelId, body));
        } catch (IssueIdempotencyStore.UnavailableException error) {
            throw new ResponseStatusException(
                    HttpStatus.CONFLICT, "issue_idempotency_unavailable");
        }
    }
}
