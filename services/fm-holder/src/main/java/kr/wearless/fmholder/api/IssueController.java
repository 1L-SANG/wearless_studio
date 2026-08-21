package kr.wearless.fmholder.api;

import kr.wearless.fmholder.protocol.IssueVcDtos;
import kr.wearless.fmholder.protocol.IssueVcService;
import java.util.Locale;
import java.util.regex.Pattern;
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
 * <p>선택 본문 {@code {"plan":"facelicense"|"mdl","claims":{...}}}:
 * 본문 없음/plan 생략 → MDL(기존 동작, 백워드 호환). plan="facelicense" → FaceLicense 커스텀 VC.
 */
@RestController
@RequestMapping("/holder")
public class IssueController {
    private static final Pattern FACELICENSE_KEY = Pattern.compile(
            "fm-license:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}");

    private final IssueVcService issueVc;
    private final IssueIdempotencyStore idempotency;

    public IssueController(IssueVcService issueVc, IssueIdempotencyStore idempotency) {
        this.issueVc = issueVc;
        this.idempotency = idempotency;
    }

    /**
     * 7스텝 issue-vc 실행 → 복호된 실 VC 반환 {@code { vcId, issuer, txId, vc, status, note, userDid }}.
     * @param body 선택. 생략 시 MDL. plan="facelicense" + claims 시 FaceLicense VC.
     */
    @PostMapping("/models/{modelId}/issue-vc")
    public IssueVcService.IssueResult issueVc(@PathVariable String modelId,
                                              @RequestBody(required = false) IssueVcDtos.IssueRequest body)
            throws Exception {
        if (!isFaceLicense(body)) {
            return issueVc.issue(modelId, body);
        }
        if (body.idempotencyKey() == null
                || !FACELICENSE_KEY.matcher(body.idempotencyKey()).matches()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "invalid_idempotency_key");
        }
        try {
            return idempotency.execute(modelId, body, () -> issueVc.issue(modelId, body));
        } catch (IssueIdempotencyStore.UnavailableException error) {
            throw new ResponseStatusException(
                    HttpStatus.CONFLICT, "issue_idempotency_unavailable");
        }
    }

    private static boolean isFaceLicense(IssueVcDtos.IssueRequest body) {
        return body != null
                && body.plan() != null
                && "facelicense".equals(body.plan().trim().toLowerCase(Locale.ROOT));
    }
}
