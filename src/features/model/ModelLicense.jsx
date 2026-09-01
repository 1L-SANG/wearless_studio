/* =============================================================
   features/model — 얼굴 라이선스 (/model/license) · step02
   "모델이 얼굴과 사용 조건을 직접 정하면 검증 가능한 라이선스(VC)로 발행된다".

   생체 등록을 통과한 enrollment만 라이선스 조건 단계에 들어온다. 브라우저는 얼굴을
   다시 올리지 않고 enrollment id와 사용 조건만 JSON으로 전송한다.

   생체 하드룰 — 얼굴은 Bearer fetch + objectURL 로만 표시한다. <img src> 로 공개 URL 을
   만들지 않는다. QR 이 싣는 건 검증 페이지 주소({origin}/verify/{id})뿐이고, 그 페이지는
   얼굴을 아예 렌더하지 않는다(PublicVerify).

   ── 2026-09-02 디자인 리뉴얼 ────────────────────────────────
   facemarket 랜딩(FacemarketLanding.module.css · LicensingSection)과 같은 언어로
   외형·레이아웃만 다시 짰다. 랜딩의 라이선싱 설명 페이지를 읽고 온 사람이 여기서
   같은 화면의 연장으로 느껴야 해서, 랜딩의 네 가지 눈금을 그대로 가져왔다 —
   eyebrow(한글 자간 0.02em) / 섹션 제목 / 카드 재질(1px 선 + 반투명 흰 면) /
   번호 붙은 스텝(.recordNumber 의 accent 0.72rem·자간 0.14em).

   호출·상태 머신은 한 줄도 건드리지 않았다. createLicense·listLicenses·revokeLicense·
   verifyLicensePublic·fetchLicenseFaceUrl 과 그 처리 흐름은 리뉴얼 전과 동일하다.
   ============================================================= */
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import QRCode from "qrcode";
import {
    Button,
    Chips,
    ErrorState,
    Field,
    Icon,
    useToast,
} from "@/components/ui.jsx";
import {
    createLicense,
    fetchLicenseFaceUrl,
    getEnrollment,
    listLicenses,
    revokeLicense,
    verifyLicensePublic,
} from "@/lib/api/facemarket.js";
import {
    ALLOWED_BRAND_USE_CATEGORIES,
    FORBIDDEN_BRAND_USE_CATEGORIES,
} from "@/lib/brandUseCategories.js";
import { enrollmentReasonMessage } from "./biometricEnrollment.js";
import s from "./ModelLicense.module.css";

// 서버 enum 과 묶인 값이다 — 표시만 바꾸고 value 는 건드리지 않는다.
const VALIDITY = [
    { value: 90, label: "90일" },
    { value: 365, label: "1년" },
    { value: 730, label: "2년" },
];

const won = (n) => `₩${Number(n || 0).toLocaleString("ko-KR")}`;
const fmtDate = (iso) => {
    try {
        return new Date(iso).toLocaleDateString("ko-KR");
    } catch {
        return iso;
    }
};
// PDF 카드 카피 — "유효 ~2027.06"
const fmtYm = (iso) => {
    try {
        const d = new Date(iso);
        return `${d.getFullYear()}.${String(d.getMonth() + 1).padStart(2, "0")}`;
    } catch {
        return iso;
    }
};
// vc:omn:9f2a1c…c481 — 카드 폭에 맞춘 가운데 생략(전체값은 title 로).
const shortVc = (vc) => {
    if (!vc) return null;
    if (vc.length <= 24) return vc;
    return `${vc.slice(0, 14)}…${vc.slice(-4)}`;
};

// 조건 4칸의 번호·설명. 랜딩 .record 의 번호 붙은 칸과 같은 형태로 읽히게 한다.
// 설명 문장은 랜딩 LicensingSection 의 카드 카피와 같은 눈금이다 — 없는 걸 약속하지 않는다.
const TERM_STEPS = {
    allowed: { no: "01", note: "여기서 고른 품목의 컷에만 내 얼굴이 쓰여요." },
    forbidden: { no: "02", note: "고른 품목에는 쓸 수 없어요." },
    // 원래 Field 의 hint(입력칸 아래)였다. 문장은 그대로 두고 자리만 다른 세 칸과 같은
    // 곳(라벨 아래)으로 옮겼다 — 네 칸이 같은 구조여야 2열로 놓았을 때 줄이 맞는다.
    // '지불하는' 이라고 쓰지 마라 — 지급(payout) 기능이 아직 없다. 랜딩이 네 라운드
    // 사실성 감사 끝에 "실제 지급 기능도 아직 준비 중입니다"로 못박은 것과 같은 눈금이어야
    // 하고, 여기는 모델이 그 숫자를 실제로 정하는 자리라 더 정확해야 한다.
    price: { no: "03", note: "사용 1건마다 기록되는 내 몫 금액이에요. 실제 지급 기능은 아직 준비 중이에요." },
    validity: {
        no: "04",
        note: "기간이 끝나면 이 라이선스로는 컷을 만들 수 없어요.",
    },
};

/* ── 얼굴 VC 카드 (PDF step02 — 파란 카드, 모바일 폭 기준) ────────
   앞면 = 얼굴 + 신원(마스킹) + VC ID + 용도 + 단가 + 유효기간, 뒷면 = QR.
   신원(nameMasked·age)은 공개 검증 API 에서 읽는다 — LicenseCard 응답에 그 필드가 없고,
   심사위원이 QR 로 보게 될 값과 카드가 **같은 소스**여야 어긋나지 않는다. */
function VcCard({ license, onRevoked, push }) {
    const [faceUrl, setFaceUrl] = useState(null);
    const [pub, setPub] = useState(null); // { model:{nameMasked,age}, valid, status, ... }
    const [qrUrl, setQrUrl] = useState(null);
    const [showQr, setShowQr] = useState(false);
    const [revoking, setRevoking] = useState(false);

    const verifyUrl = `${window.location.origin}/verify/${license.id}`;
    // 카드 프로필 = 모델 대표 이미지(셀러가 고른 사진). 없으면(구 라이선스·생성 직후 RETURNING)
    // 검증 얼굴로 폴백한다. 검증된 얼굴+digest 는 신뢰 핵심이라 사라지지 않고 코너 배지로 남는다.
    const coverUrl = license.coverImageUrl || null;

    // 얼굴 — 인증 게이트로만(공개 URL 금지). 언마운트가 fetch 보다 빠르면 즉시 해제(누수 방지).
    useEffect(() => {
        let url;
        let alive = true;
        fetchLicenseFaceUrl(license.faceImageUri)
            .then((u) => {
                if (!alive) {
                    URL.revokeObjectURL(u);
                    return;
                }
                url = u;
                setFaceUrl(u);
            })
            .catch(() => {
                /* 표시 실패 — 플레이스홀더 유지(파기된 얼굴은 게이트가 404 로 닫는다) */
            });
        return () => {
            alive = false;
            if (url) URL.revokeObjectURL(url);
        };
    }, [license.faceImageUri]);

    // 신원 마스킹값 + 실시간 유효 판정(무인증 공개 API — 내 라이선스도 같은 창구로 본다).
    useEffect(() => {
        let alive = true;
        verifyLicensePublic(license.id)
            .then((r) => {
                if (alive) setPub(r);
            })
            .catch(() => {
                /* 검증 조회 실패 — 카드는 로컬 status 로 폴백 */
            });
        return () => {
            alive = false;
        };
    }, [license.id, license.status]);

    // QR = 공개 검증 주소만. 얼굴·개인정보는 담기지 않는다(주소 하나가 전부).
    useEffect(() => {
        let alive = true;
        QRCode.toDataURL(verifyUrl, {
            width: 320,
            margin: 1,
            errorCorrectionLevel: "M",
        })
            .then((u) => {
                if (alive) setQrUrl(u);
            })
            .catch(() => {
                /* QR 생성 실패 — 주소 텍스트로 폴백 */
            });
        return () => {
            alive = false;
        };
    }, [verifyUrl]);

    // 만료는 서버 판정(pub.status)을 우선하고, 조회 실패 시에만 로컬 계산으로 폴백.
    const localExpired =
        license.licenseValidUntil &&
        new Date(license.licenseValidUntil) <= new Date();
    const status =
        pub?.status ??
        (license.status === "active" && localExpired
            ? "expired"
            : license.status);
    const isActive = status === "active";
    const statusLabel =
        status === "revoked"
            ? "해지됨"
            : status === "expired"
              ? "만료"
              : "유효";

    const onRevoke = async () => {
        // 해지는 되돌릴 수 없는 표준 조치 — 셀러가 더는 이 얼굴을 쓸 수 없게 된다. 오조작 방지로 확인받는다.
        if (
            !window.confirm(
                "이 라이선스를 해지하면 셀러가 더 이상 사용할 수 없어요. 해지할까요?",
            )
        )
            return;
        setRevoking(true);
        try {
            await revokeLicense(license.id);
            push?.("라이선스를 해지했어요.", { icon: "check" });
            onRevoked?.();
        } catch (e) {
            push?.(e.message || "라이선스 해지에 실패했어요.", {
                icon: "alertCircle",
            });
        } finally {
            setRevoking(false);
        }
    };

    const vcShort = shortVc(license.vcId);

    return (
        <article className={`${s.vc}${isActive ? "" : " " + s.vcOff}`}>
            <header className={s.vcTop}>
                <span className={s.vcBrand}>
                    <Icon name="checkSquare" size={13} />
                    얼굴 라이선스 VC
                </span>
                {/* 상태는 색만으로 알리지 않는다 — 점(형태) + 글자를 함께 둔다(WCAG 1.4.1). */}
                <span
                    className={`${s.vcStatus}${isActive ? "" : " " + s.vcStatusOff}`}
                >
                    <i className={s.vcStatusDot} aria-hidden="true" />
                    {statusLabel}
                </span>
            </header>

            {/* 앞면·뒷면 높이가 달라 토글할 때 카드가 튀었다. 최소 높이를 주는 자리. */}
            <div className={s.vcBody}>
                {showQr ? (
                    <div className={s.vcQr}>
                        {qrUrl ? (
                            <img
                                src={qrUrl}
                                alt="라이선스 검증 QR 코드"
                                className={s.vcQrImg}
                            />
                        ) : (
                            <div className={s.vcQrSkel} />
                        )}
                        <p className={s.vcQrHint}>
                            스캔하면 이 라이선스가 유효한지 로그인 없이 확인할 수
                            있어요.
                        </p>
                        <code className={s.vcQrUrl}>{verifyUrl}</code>
                    </div>
                ) : (
                    <>
                        <div className={s.vcId}>
                            {/* 얼굴 — objectURL 만. 파기 시 게이트가 닫히면 플레이스홀더로 강등된다. */}
                            <div className={s.vcFaceWrap}>
                                <div className={s.vcFace}>
                                    {coverUrl ? (
                                        <img
                                            src={coverUrl}
                                            alt="모델 대표 이미지"
                                        />
                                    ) : faceUrl ? (
                                        <img src={faceUrl} alt="라이선스 얼굴" />
                                    ) : (
                                        <span className={s.vcFaceEmpty}>
                                            <Icon name="person" size={22} />
                                        </span>
                                    )}
                                </div>
                                {coverUrl && faceUrl && (
                                    <span
                                        className={s.vcVerifiedBadge}
                                        title="검증된 얼굴"
                                    >
                                        <img src={faceUrl} alt="검증된 얼굴" />
                                    </span>
                                )}
                            </div>
                            <div className={s.vcWho}>
                                <div className={s.vcName}>
                                    {pub?.model?.nameMasked ?? "—"}
                                    {pub?.model?.age != null && (
                                        <span className={s.vcAge}>
                                            {" "}
                                            · {pub.model.age}세
                                        </span>
                                    )}
                                </div>
                                {vcShort ? (
                                    <div
                                        className={s.vcVcid}
                                        title={license.vcId}
                                    >
                                        <span>VC ID</span>{" "}
                                        <code>{vcShort}</code>
                                    </div>
                                ) : (
                                    <div className={s.vcVcid}>
                                        <span>VC 발급 대기</span>
                                    </div>
                                )}
                            </div>
                        </div>

                        {/* 단가·유효기간은 이 카드에서 가장 먼저 읽혀야 하는 두 숫자다.
                            조건 목록 안의 한 줄이 아니라 나란한 두 칸으로 올렸다(정보는 그대로). */}
                        <dl className={s.vcFigures}>
                            <div className={s.vcFigure}>
                                <dt>건당 단가</dt>
                                <dd className={s.vcPrice}>
                                    {won(license.unitPrice)}
                                    <em>/건</em>
                                </dd>
                            </div>
                            <div className={s.vcFigure}>
                                <dt>유효기간</dt>
                                <dd className={s.vcValid}>
                                    ~{fmtYm(license.licenseValidUntil)}
                                    <span className={s.vcDim}>
                                        {fmtDate(license.licenseValidUntil)}까지
                                    </span>
                                </dd>
                            </div>
                        </dl>

                        {/* 허용/금지가 둘 다 비면 이 <dl> 은 자식이 없다 — :empty 로 접는다.
                            JSX 에 조건을 하나 더 얹는 대신 CSS 로 처리해 렌더 흐름을 그대로 뒀다. */}
                        <dl className={s.vcRows}>
                            {license.allowedUse?.length > 0 && (
                                <div className={s.vcRow}>
                                    <dt>허용 용도</dt>
                                    <dd className={s.vcTags}>
                                        {license.allowedUse.map((u) => (
                                            <span
                                                key={u}
                                                className={s.tagAllow}
                                            >
                                                {u}
                                            </span>
                                        ))}
                                    </dd>
                                </div>
                            )}
                            {license.forbiddenUse?.length > 0 && (
                                <div className={s.vcRow}>
                                    <dt>금지 용도</dt>
                                    <dd className={s.vcTags}>
                                        {license.forbiddenUse.map((u) => (
                                            <span key={u} className={s.tagDeny}>
                                                <Icon name="ban" size={10} />
                                                {u}
                                            </span>
                                        ))}
                                    </dd>
                                </div>
                            )}
                        </dl>
                    </>
                )}
            </div>

            <footer className={s.vcActions}>
                <button
                    type="button"
                    className={s.vcBtn}
                    aria-pressed={showQr}
                    onClick={() => setShowQr((v) => !v)}
                >
                    <Icon name={showQr ? "person" : "grid"} size={14} />
                    {showQr ? "카드 보기" : "QR 보기"}
                </button>
                {isActive && (
                    <button
                        type="button"
                        className={`${s.vcBtn} ${s.vcBtnDanger}`}
                        onClick={onRevoke}
                        disabled={revoking}
                    >
                        <Icon name="ban" size={14} />
                        {revoking ? "해지 중…" : "해지"}
                    </button>
                )}
            </footer>
        </article>
    );
}

/* ── 4단계: 라이선스 조건 + 발급 ──────────────────────────── */
function TermsStep({ enrollmentId, enrollmentStatus, enrollmentReason, onIssued, push }) {
    const [allowed, setAllowed] = useState([ALLOWED_BRAND_USE_CATEGORIES[0]]);
    const [forbidden, setForbidden] = useState([FORBIDDEN_BRAND_USE_CATEGORIES[0]]);
    const [unitPrice, setUnitPrice] = useState(10000);
    const [validDays, setValidDays] = useState(365);
    const [submitting, setSubmitting] = useState(false);
    const [issuePhase, setIssuePhase] = useState(null); // null | 'preparing' | 'issuing'

    // 발급 가능 여부 — 아래 세 곳(안내문·버튼 disabled)이 같은 판정을 쓰게 이름만 붙였다.
    // onSubmit 의 가드는 리뉴얼 전 표현 그대로 둔다(판정이 갈릴 여지를 만들지 않는다).
    const blocked =
        !enrollmentId ||
        !["license_pending", "vc_pending"].includes(enrollmentStatus);

    const onSubmit = async () => {
        if (!enrollmentId || !["license_pending", "vc_pending"].includes(enrollmentStatus)) return;
        setSubmitting(true);
        // VC 발급은 opendid(체인·홀더)를 거친다. 유휴 시 scale-to-zero 라 첫 요청이 콜드스타트
        // (code=vc_issue_delayed)로 돌아온다. 원시 503 을 그대로 던지지 말고, 준비될 때까지 자동
        // 재시도하며 "준비 중 → 진행 중" 진행표시로 사용자가 기다리면 된다는 걸 알게 한다.
        const deadline = Date.now() + 4 * 60 * 1000; // 최대 4분(콜드부트 ~2-3분 + 여유)
        try {
            while (true) {
                setIssuePhase("issuing");
                try {
                    const lic = await createLicense({
                        enrollmentId,
                        allowedUse: allowed,
                        forbiddenUse: forbidden,
                        unitPrice: Number(unitPrice) || 0,
                        validDays,
                    });
                    push("라이선스가 발급됐어요.", { icon: "check" });
                    onIssued(lic);
                    return;
                } catch (e) {
                    const warming = e?.code === "vc_issue_delayed";
                    if (warming && Date.now() < deadline) {
                        setIssuePhase("preparing"); // opendid 깨우는 중 — 자동 재시도
                        await new Promise((r) => setTimeout(r, 8000));
                        continue;
                    }
                    push(
                        warming
                            ? "VC 발급 서버 준비가 늦어지고 있어요. 잠시 후 다시 시도해 주세요."
                            : e.message,
                        { icon: "alertCircle" },
                    );
                    return;
                }
            }
        } finally {
            setSubmitting(false);
            setIssuePhase(null);
        }
    };

    return (
        <div className="surface">
            <div className={s.formHead}>
                <span className={s.eyebrow}>발급 조건</span>
                <h2 className={s.formTitle}>네 가지를 정하면 발급돼요</h2>
                {/* DESIGN.md:309 — '결속'·'자산' 은 화면에 쓰지 않는 개발자 언어라
                    '이번 등록에서 확인한 얼굴 이미지' 로 바꿨다(사실은 같다). */}
                <p className={s.formLead}>
                    이번 등록에서 확인한 얼굴 이미지만 이 라이선스에 쓰여요.
                    사용 조건을 정하면 서명된 자격증명(VC)으로 발급돼요.
                </p>
            </div>

            {blocked && (
                <p className={s.notice}>
                    <Icon name="alertCircle" size={16} />
                    <span>
                        {enrollmentReasonMessage(enrollmentReason)} 먼저 모델
                        등록을 완료해 주세요.
                    </span>
                </p>
            )}

            <div className={s.terms}>
                <section className={s.term}>
                    <span className={s.termNo}>{TERM_STEPS.allowed.no}</span>
                    <h3 className={s.termLabel}>허용 브랜드 유형</h3>
                    <p className={s.termNote}>{TERM_STEPS.allowed.note}</p>
                    <Chips
                        options={ALLOWED_BRAND_USE_CATEGORIES}
                        value={allowed}
                        onChange={setAllowed}
                        multi
                    />
                </section>

                <section className={s.term}>
                    <span className={s.termNo}>{TERM_STEPS.forbidden.no}</span>
                    <h3 className={s.termLabel}>금지 브랜드 유형</h3>
                    <p className={s.termNote}>{TERM_STEPS.forbidden.note}</p>
                    <Chips
                        options={FORBIDDEN_BRAND_USE_CATEGORIES}
                        value={forbidden}
                        onChange={setForbidden}
                        multi
                    />
                </section>

                <div className={s.row2}>
                    <section className={s.term}>
                        <span className={s.termNo}>{TERM_STEPS.price.no}</span>
                        <h3 className={s.termLabel}>건당 단가</h3>
                        <p className={s.termNote}>{TERM_STEPS.price.note}</p>
                        <Field
                            type="number"
                            min={0}
                            step={1000}
                            value={unitPrice}
                            onChange={(e) => setUnitPrice(e.target.value)}
                        />
                    </section>
                    <section className={s.term}>
                        <span className={s.termNo}>
                            {TERM_STEPS.validity.no}
                        </span>
                        <h3 className={s.termLabel}>유효기간</h3>
                        <p className={s.termNote}>{TERM_STEPS.validity.note}</p>
                        <Chips
                            options={VALIDITY}
                            value={validDays}
                            onChange={(v) => v && setValidDays(v)}
                        />
                    </section>
                </div>
            </div>

            <div className={s.submit}>
                <Button
                    variant="primary"
                    block
                    className={s.cta}
                    onClick={onSubmit}
                    disabled={submitting || blocked}
                    iconRight="arrowRight"
                >
                    {submitting
                        ? (issuePhase === "preparing" ? "발급 준비 중…" : "발급 진행 중…")
                        : "라이선스 발급"}
                </Button>

                {/* 대기 화면이 제품의 일부다(PRD §13-2). holder 콜드부트 ~2분이 정상
                    경로에 있어서, 버튼 라벨만으로는 "멈춘 건가"로 읽힌다. 진행 중이라는
                    걸 형태(맥동 점 + 불확정 바)로도 보여주고, 얼마나 걸리는지와 화면을
                    닫지 말라는 지시를 같이 둔다. 문장의 사실(최대 3분)은 그대로다. */}
                {submitting && (
                    <div className={s.wait} role="status" aria-live="polite">
                        <span className={s.waitPulse} aria-hidden="true" />
                        <div className={s.waitText}>
                            <p className={s.waitTitle}>
                                {issuePhase === "preparing"
                                    ? "VC 발급 서버를 준비하고 있어요"
                                    : "VC 발급을 진행하고 있어요"}
                            </p>
                            <p className={s.waitNote}>
                                {/* 남은 시간을 아는 척하지 않는다 — 발급 단계는 홀더
                                    응답에 달려 있어 예측값이 없다. 대신 끝나면 무엇이
                                    보이는지(발급 직후 카드로 이동)를 적는다. */}
                                {issuePhase === "preparing"
                                    ? "최대 3분 정도 걸릴 수 있어요. 이 화면을 닫지 말아 주세요."
                                    : "발급이 끝나면 라이선스 카드가 바로 보여요. 이 화면을 닫지 말아 주세요."}
                            </p>
                        </div>
                        <span className={s.waitBar} aria-hidden="true">
                            <i />
                        </span>
                    </div>
                )}
            </div>

            <p className={s.privacy}>
                <Icon name="lock" size={15} />
                <span>
                    얼굴 이미지는 비공개로 저장되고, 검증된 본인만 열람할 수
                    있어요. QR 에는 검증 주소만 담겨요.
                </span>
            </p>
        </div>
    );
}

/* ── 페이지 ───────────────────────────────────────────────── */
export function ModelLicense() {
    const { push } = useToast(); // 안정 useCallback 만 구조분해 — 불안정한 toast 객체 의존 배제(리로드 루프 방지)
    const navigate = useNavigate();
    const [searchParams, setSearchParams] = useSearchParams();
    const requestedEnrollmentId = searchParams.get("enrollment");
    const termsRequested = searchParams.get("step") === "terms" && !!requestedEnrollmentId;
    const [phase, setPhase] = useState("loading"); // loading | ready | error
    const [view, setView] = useState("cards"); // cards | flow
    const [enrollmentRecord, setEnrollmentRecord] = useState(null);
    const [licenses, setLicenses] = useState([]);
    const [issuedId, setIssuedId] = useState(null); // 방금 발급 — 카드로 스크롤·강조
    const issuedRef = useRef(null);

    const load = useCallback(async () => {
        setPhase("loading");
        try {
            setLicenses(await listLicenses());
            if (termsRequested) {
                try {
                    setEnrollmentRecord(await getEnrollment(requestedEnrollmentId));
                } catch (requestError) {
                    setEnrollmentRecord({
                        id: requestedEnrollmentId,
                        status: "unavailable",
                        reason: requestError?.code,
                    });
                }
                setView("flow");
            } else {
                setEnrollmentRecord(null);
                setView("cards");
            }
            setPhase("ready");
        } catch (requestError) {
            push(requestError.message, { icon: "alertCircle" });
            setPhase("error");
        }
    }, [push, requestedEnrollmentId, termsRequested]);

    useEffect(() => {
        load();
    }, [load]);

    const onIssued = useCallback(
        async (lic) => {
            setIssuedId(lic?.id ?? null);
            setView("cards");
            setSearchParams({}, { replace: true });
            setLicenses(await listLicenses());
        },
        [setSearchParams],
    );

    // 발급 직후 카드로 데려간다(모바일에선 목록이 길어 새 카드가 화면 밖에 있을 수 있다).
    useEffect(() => {
        if (issuedId && issuedRef.current)
            issuedRef.current.scrollIntoView({
                behavior: "smooth",
                block: "center",
            });
    }, [issuedId, licenses]);

    if (phase === "loading")
        return (
            <div className="wizard narrow">
                <div className="surface">
                    <p className={s.loading} role="status" aria-live="polite">
                        <span className={s.waitPulse} aria-hidden="true" />
                        불러오는 중…
                    </p>
                </div>
            </div>
        );
    if (phase === "error")
        return (
            <div className="wizard narrow">
                <div className="surface">
                    <ErrorState
                        desc="라이선스 정보를 불러오지 못했어요."
                        onRetry={load}
                    />
                </div>
            </div>
        );

    return (
        <div className="wizard narrow">
            {/* eyebrow 는 <span> 이다 — facemarketTheme 의 `.page-head p` 규칙(본문 리드)이
                이 줄까지 잡아 muted 리드로 만들어 버려서 요소를 갈랐다. */}
            <div className="page-head">
                <span className={s.eyebrow}>라이선싱</span>
                <h1>얼굴 라이선스</h1>
                {/* '발급'으로 통일한다. 같은 화면 아래(발급 조건·라이선스 발급 버튼)와
                    랜딩·허브가 전부 '발급'인데 여기만 '발행'이었다. 한 사이트에서 같은
                    동작을 두 단어로 부르면 다른 일로 읽힌다. */}
                <p>
                    얼굴과 사용 조건을 직접 정하면, 검증 가능한 라이선스(VC)로
                    발급돼요.
                </p>
            </div>

            {view === "flow" ? (
                <>
                    <TermsStep
                        enrollmentId={enrollmentRecord?.id}
                        enrollmentStatus={enrollmentRecord?.status}
                        enrollmentReason={enrollmentRecord?.reason}
                        onIssued={onIssued}
                        push={push}
                    />
                    {licenses.length > 0 && (
                        <button
                            type="button"
                            className={s.switchLink}
                            onClick={() => {
                                setView("cards");
                                setSearchParams({}, { replace: true });
                            }}
                        >
                            내 라이선스 {licenses.length}건 보기
                            <Icon name="chevRight" size={14} />
                        </button>
                    )}
                </>
            ) : (
                <>
                    {licenses.length > 0 ? (
                        <>
                            {/* 큰 세리프 숫자 = 랜딩 .galleryIndexNow 의 자리표시.
                                Cormorant 는 라틴 전용이라 숫자만 이 서체로 그리고
                                뒤 라벨은 본문 서체로 둔다(한글이 폴백으로 안 떨어지게). */}
                            <div className={s.listHead}>
                                <p className={s.listCount}>
                                    <span className={s.listCountNow}>
                                        {licenses.length}
                                    </span>
                                    <span className={s.listCountLabel}>
                                        건 발급됨
                                    </span>
                                </p>
                                <p className={s.listNote}>
                                    QR 을 찍으면 로그인 없이 조건과 유효 여부를
                                    확인할 수 있어요. 그 화면에 얼굴은 나오지
                                    않아요.
                                </p>
                            </div>
                            <div className={s.cards}>
                                {licenses.map((lic) => (
                                    <div
                                        key={lic.id}
                                        ref={
                                            lic.id === issuedId
                                                ? issuedRef
                                                : null
                                        }
                                        className={
                                            lic.id === issuedId
                                                ? s.cardNew
                                                : undefined
                                        }
                                    >
                                        <VcCard
                                            license={lic}
                                            onRevoked={load}
                                            push={push}
                                        />
                                    </div>
                                ))}
                            </div>
                        </>
                    ) : (
                        // 발급 이력이 없으면 예전엔 버튼 하나만 뜬 빈 화면이었다.
                        // 다음에 뭘 해야 하는지 한 줄로 말해 준다(없는 기능은 약속하지 않는다).
                        <div className={s.empty}>
                            <Icon name="checkSquare" size={22} stroke={1.7} />
                            <h2 className={s.emptyTitle}>
                                아직 발급된 라이선스가 없어요
                            </h2>
                            <p className={s.emptyBody}>
                                모델 등록을 마치면 허용 품목·금지 품목·건당
                                단가·유효기간을 정하고 라이선스를 발급할 수
                                있어요.
                            </p>
                        </div>
                    )}
                    <div className={s.listFoot}>
                        <Button
                            variant="ghost"
                            block
                            icon="plus"
                            className={s.ctaQuiet}
                            onClick={() => navigate("/model/register")}
                        >
                            새 생체 등록으로 라이선스 발급
                        </Button>
                    </div>
                </>
            )}
        </div>
    );
}

export default ModelLicense;
