/* =============================================================
   features/model — 얼굴 라이선스 (/model/license) · step02
   "모델이 얼굴과 사용 조건을 직접 정하면 검증 가능한 라이선스(VC)로 발행된다".

   생체 등록을 통과한 enrollment만 라이선스 조건 단계에 들어온다. 브라우저는 얼굴을
   다시 올리지 않고 enrollment id와 사용 조건만 JSON으로 전송한다.

   생체 하드룰 — 얼굴은 Bearer fetch + objectURL 로만 표시한다. <img src> 로 공개 URL 을
   만들지 않는다. QR 이 싣는 건 검증 페이지 주소({origin}/verify/{id})뿐이고, 그 페이지는
   얼굴을 아예 렌더하지 않는다(PublicVerify).
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
import { enrollmentReasonMessage } from "./biometricEnrollment.js";
import s from "./ModelLicense.module.css";

// 브랜드 유형(카테고리) 기준 — 모델이 자기 얼굴이 쓰일 브랜드 종류를 허용/금지로 통제.
const ALLOWED_PRESETS = [
    "일반 여성 의류",
    "남성 의류",
    "캐주얼·스트릿",
    "스포츠·애슬레저",
    "뷰티·화장품",
    "액세서리·잡화",
];
const FORBIDDEN_PRESETS = [
    "속옷·란제리",
    "수영복·비키니",
    "성인용품",
    "주류·담배",
    "의료·성형",
    "정치·종교",
];
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
                <span
                    className={`${s.vcStatus}${isActive ? "" : " " + s.vcStatusOff}`}
                >
                    {statusLabel}
                </span>
            </header>

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
                        <div className={s.vcFace}>
                            {faceUrl ? (
                                <img src={faceUrl} alt="라이선스 얼굴" />
                            ) : (
                                <span className={s.vcFaceEmpty}>
                                    <Icon name="person" size={22} />
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
                                <div className={s.vcVcid} title={license.vcId}>
                                    <span>VC ID</span> <code>{vcShort}</code>
                                </div>
                            ) : (
                                <div className={s.vcVcid}>
                                    <span>VC 발급 대기</span>
                                </div>
                            )}
                        </div>
                    </div>

                    <dl className={s.vcRows}>
                        {license.allowedUse?.length > 0 && (
                            <div className={s.vcRow}>
                                <dt>허용 용도</dt>
                                <dd className={s.vcTags}>
                                    {license.allowedUse.map((u) => (
                                        <span key={u} className={s.tagAllow}>
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
                        <div className={s.vcRow}>
                            <dt>단가</dt>
                            <dd className={s.vcPrice}>
                                {won(license.unitPrice)}
                                <em>/건</em>
                            </dd>
                        </div>
                        <div className={s.vcRow}>
                            <dt>유효</dt>
                            <dd>
                                ~{fmtYm(license.licenseValidUntil)}{" "}
                                <span className={s.vcDim}>
                                    ({fmtDate(license.licenseValidUntil)}까지)
                                </span>
                            </dd>
                        </div>
                    </dl>
                </>
            )}

            <footer className={s.vcActions}>
                <button
                    type="button"
                    className={s.vcBtn}
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
    const [allowed, setAllowed] = useState([ALLOWED_PRESETS[0]]);
    const [forbidden, setForbidden] = useState([FORBIDDEN_PRESETS[0]]);
    const [unitPrice, setUnitPrice] = useState(10000);
    const [validDays, setValidDays] = useState(365);
    const [submitting, setSubmitting] = useState(false);

    const onSubmit = async () => {
        if (!enrollmentId || !["license_pending", "vc_pending"].includes(enrollmentStatus)) return;
        setSubmitting(true);
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
        } catch (e) {
            push(e.message, { icon: "alertCircle" });
        } finally {
            setSubmitting(false);
        }
    };

    return (
        <div className="surface">
            <div className={s.termsIntro}>
                <Icon name="checkSquare" size={15} />
                <span>
                    현재 생체 등록에 결속된 얼굴 자산만 이 라이선스에 쓰여요.
                    사용 조건을 정하면 VC 로 발행돼요.
                </span>
            </div>

            {(!enrollmentId || !["license_pending", "vc_pending"].includes(enrollmentStatus)) && (
                <p className={s.privacy}>
                    {enrollmentReasonMessage(enrollmentReason)} 먼저 모델 등록을 완료해 주세요.
                </p>
            )}

            <div className={s.sectionLabel}>허용 브랜드 유형</div>
            <Chips
                options={ALLOWED_PRESETS}
                value={allowed}
                onChange={setAllowed}
                multi
            />

            <div className={s.sectionLabel}>금지 브랜드 유형</div>
            <Chips
                options={FORBIDDEN_PRESETS}
                value={forbidden}
                onChange={setForbidden}
                multi
            />

            <div className={s.row2}>
                <div>
                    <div className={s.sectionLabel}>건당 단가</div>
                    <Field
                        type="number"
                        min={0}
                        step={1000}
                        value={unitPrice}
                        onChange={(e) => setUnitPrice(e.target.value)}
                        hint="셀러가 1회 사용할 때마다 지불"
                    />
                </div>
                <div>
                    <div className={s.sectionLabel}>유효기간</div>
                    <Chips
                        options={VALIDITY}
                        value={validDays}
                        onChange={(v) => v && setValidDays(v)}
                    />
                </div>
            </div>

            <Button
                variant="primary"
                block
                onClick={onSubmit}
                disabled={submitting || !enrollmentId || !["license_pending", "vc_pending"].includes(enrollmentStatus)}
                iconRight="arrowRight"
            >
                {submitting ? "발급 중…" : "라이선스 발급"}
            </Button>

            <div className={s.privacy}>
                <Icon name="lock" size={15} />
                <span>
                    얼굴 이미지는 비공개로 저장되고, 검증된 본인만 열람할 수
                    있어요. QR 에는 검증 주소만 담겨요.
                </span>
            </div>
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
                <div className="surface">불러오는 중…</div>
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
            <div className="page-head">
                <h1>얼굴 라이선스</h1>
                <p>
                    얼굴과 사용 조건을 직접 정하면, 검증 가능한 라이선스(VC)로
                    발행돼요.
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
                    <div className={s.cards}>
                        {licenses.map((lic) => (
                            <div
                                key={lic.id}
                                ref={lic.id === issuedId ? issuedRef : null}
                                className={
                                    lic.id === issuedId ? s.cardNew : undefined
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
                    <Button
                        variant="ghost"
                        block
                        icon="plus"
                        style={{ marginTop: 40 }}
                        onClick={() => navigate("/model/register")}
                    >
                        새 생체 등록으로 라이선스 발급
                    </Button>
                </>
            )}
        </div>
    );
}

export default ModelLicense;
