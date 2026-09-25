package kr.wearless.fmholder.protocol;

import java.util.Locale;
import java.util.Optional;
import java.util.regex.Pattern;

/**
 * issue-vc 발급 플랜 레지스트리 — 요청 plan 문자열 → Issuer 프로비저닝 값(vcPlanId·스키마·네임스페이스)
 * + 멱등키 형식. 새 VC 종류는 여기 한 줄 + {@code IssueVcService.resolvePlan} 의 클레임 매핑 한 갈래.
 *
 * <p>vc_plan_id 컬럼 = varchar(20) → vcPlanId 는 정확히 20자(생성자에서 강제).
 * 네임스페이스·스키마·플랜은 scripts/issuer-provision-*.sh 로 Issuer 에 먼저 프로비저닝돼 있어야 한다.
 */
public enum IssuePlan {
    /** 본문 없음/plan 생략/"mdl" — 기존 데모 VC. 멱등키 없음(백워드 호환). */
    MDL("mdl", "vcplanid000000000001", "mdl", "org.iso.18013.5.1", null, "MDL"),
    /** 얼굴 라이선스 VC. scripts/issuer-provision-facelicense.sh. */
    FACELICENSE_V2("facelicense-v2", "vcplanface0000000002", "facelicense-v2",
            "kr.wearless.facelicense.v2", "fm-license:", "FaceLicense v2"),
    /** 협찬 동의 VC. scripts/issuer-provision-sponsorship.sh. */
    FMSPONSORSHIP_V1("fmsponsorship-v1", "vcplanspons000000001", "fmsponsorship-v1",
            "kr.wearless.fmsponsorship.v1", "fm-sponsorship:", "FmSponsorship v1");

    private static final String UUID_RE =
            "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";

    private final String requestPlan;
    private final String vcPlanId;
    private final String vcSchemaId;
    private final String namespace;
    private final String keyPrefix;
    private final Pattern keyPattern;
    private final String label;

    IssuePlan(String requestPlan, String vcPlanId, String vcSchemaId, String namespace,
              String keyPrefix, String label) {
        if (vcPlanId.length() != 20) {
            throw new IllegalStateException("vcPlanId must be exactly 20 chars: " + vcPlanId);
        }
        this.requestPlan = requestPlan;
        this.vcPlanId = vcPlanId;
        this.vcSchemaId = vcSchemaId;
        this.namespace = namespace;
        this.keyPrefix = keyPrefix;
        this.keyPattern = keyPrefix == null ? null : Pattern.compile(Pattern.quote(keyPrefix) + UUID_RE);
        this.label = label;
    }

    /** 요청 plan → 플랜. null/생략 = MDL. 대소문자·앞뒤 공백 무시. 모르는 값 = empty. */
    public static Optional<IssuePlan> fromRequest(String plan) {
        String normalized = plan == null ? MDL.requestPlan : plan.trim().toLowerCase(Locale.ROOT);
        for (IssuePlan candidate : values()) {
            if (candidate.requestPlan.equals(normalized)) {
                return Optional.of(candidate);
            }
        }
        return Optional.empty();
    }

    public String requestPlan() { return requestPlan; }
    public String vcPlanId() { return vcPlanId; }
    public String vcSchemaId() { return vcSchemaId; }
    public String namespace() { return namespace; }
    public String label() { return label; }

    /** 멱등 발급 대상인가(키 필수 + 파일 멱등 저장소 경유). MDL 만 아니다. */
    public boolean requiresIdempotencyKey() { return keyPrefix != null; }

    /** 멱등키 = {@code <prefix><소문자 UUID>}. */
    public boolean isValidIdempotencyKey(String key) {
        return keyPattern != null && key != null && keyPattern.matcher(key).matches();
    }

    /** 클레임의 id(licenseId/credentialId)로 기대 멱등키를 만든다. */
    public String idempotencyKeyFor(String id) {
        return keyPrefix + id;
    }
}
