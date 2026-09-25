/* 신원 확인 뒤 증서 발급과 학습용 사진 확인을 진행하는 관리자 콘솔이에요.
   대조 점수는 참고 정보로 보여주고, 동일인 확인과 사용 조건 제출 여부로 승인을 막아요.
   카드 선택이 바뀌면 EnrollmentDetail을 새로 마운트해 확인 체크가 넘어가지 않게 해요.
   생체 이미지는 인증된 게이트로 받아 사용한 뒤 objectURL을 해제해요. */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useToast } from '@/components/ui.jsx';
import { Badge } from '@/components/admin-ui/badge.jsx';
import { Button } from '@/components/admin-ui/button.jsx';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/admin-ui/card.jsx';
import { Skeleton } from '@/components/admin-ui/skeleton.jsx';
import { Textarea } from '@/components/admin-ui/textarea.jsx';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/admin-ui/table.jsx';
import {
  adminApproveEnrollment, adminApproveEnrollmentPhotos, adminEnrollmentCard,
  adminFetchApplicationPhotoUrl, adminFetchGatedImageUrl, adminListEnrollments,
  adminListPhotoReview, adminRejectEnrollment, adminRequestEnrollmentReshoot,
} from '@/lib/api/facemarket.js';
import { PHOTO_GROUPS, SLOTS } from '@/features/model/registerSlots.js';
import { seoulDateTime } from '@/lib/datetime.js';
import styles from './AdminEnrollmentReview.module.css';
import { finalRejectReason, imageFailureLabel, scoreRow, reviewActions } from './enrollmentReviewMath.js';

// 마지막 탭만 다른 축이다 — 신원 심사가 아니라 **학습 전 사진 확인**이고, 표준인증(mid)
// 등록까지 포함한다(그쪽은 review_status 가 null 이라 앞의 세 탭에 아예 안 뜬다).
const PHOTO_FILTER = 'photos';
const REVIEW_FILTERS = [
  { value: 'pending', label: '대기' },
  { value: 'approved', label: '승인' },
  { value: 'rejected', label: '거절' },
  { value: PHOTO_FILTER, label: '사진 확인' },
];
const REVIEW_LABEL = { pending: '대기', approved: '승인됨', rejected: '거절됨' };
const ENROLLMENT_STATUS_LABEL = {
  review_pending: '기존 심사 대기', asset_building: '사진 정리 중',
  license_pending: '사용 조건 대기', vc_pending: '신원 확인 대기',
  passed: '증서 발급 완료', failed: '등록 종료',
};

// 학습 전 사진 확인 상태(fm_biometric_enrollments.photo_review_status).
const PHOTO_REVIEW_LABEL = {
  pending: '확인 대기', approved: '확인 완료', reshoot_requested: '재촬영 요청',
};
// 칸 제목·조명 묶음은 등록 화면과 **한 곳**에서 가져온다(registerSlots.js). 여기서 다시
// 적으면 칸이 늘 때 두 곳이 어긋나고, 어긋난 쪽이 조용히 빈 칸으로 보인다.
const SLOT_BY_KEY = new Map(SLOTS.map((slot) => [slot.key, slot]));

const IDENTITY_METHOD_LABEL = { mid: '표준인증', simple_auth: '간편인증' };

// 신분증 마스킹 기하 검증(facemarket_id_mask_verify)이 'auto' 로 통과시키지 못한
// 건(검사에 걸림 = 'manual', 검사가 아예 안 돎 = null 둘 다) 표시. 검사가 안 돈
// 경우를 조용히 "정상"으로 보이게 두면 안 되므로 'auto' 가 아닌 전부를 배지로
// 낸다(Task9) — 아무 것도 안 보이는 것보다 과하게 눈에 띄는 쪽이 안전하다.
const MASK_MODE_LABEL = '수동 마스킹';

// facemarket_admin_review.py 의 PHOTO_ANGLES 와 순서를 맞춘다.
const ANGLES = ['front', 'angle45', 'side'];
const ANGLE_LABEL = { front: '정면', angle45: '45도', side: '측면' };

const IMAGE_KINDS = [
  { kind: 'id_document', label: '신분증 (마스킹 전체본)' },
  { kind: 'front', label: '정면' },
  { kind: 'angle45', label: '45도' },
  { kind: 'side', label: '측면' },
];

const ANCHOR_LABEL = {
  id_document_crop: '신분증 사진',
  oacx_portrait: '표준인증 정부 사진',
};

// 거절은 사유가 반드시 있어야 한다(서버도 빈 문자열을 400 으로 막는다) — 자주 쓰는
// 사유를 먼저 주고, 그 목록에 없으면 자유 입력(기타)으로 받는다.
const REJECT_REASON_PRESETS = [
  { value: 'id_mismatch', label: '신분증-사진 불일치' },
  { value: 'id_illegible', label: '신분증 판독 불가' },
  { value: 'masking_missing', label: '마스킹 미이행' },
  { value: 'forgery_suspected', label: '위조 의심' },
  { value: 'other', label: '기타' },
];

// 배지는 정보일 뿐이다 — 이 컴포넌트를 승인/거절 버튼의 disabled 조건에 절대 쓰지 않는다.
function ScoreBadge({ tone, children }) {
  if (tone === 'danger') return <Badge variant="destructive">{children}</Badge>;
  if (tone === 'ok') {
    return <Badge variant="outline" className="border-emerald-600/40 bg-emerald-50 text-emerald-700">{children}</Badge>;
  }
  if (tone === 'warn') {
    return <Badge variant="outline" className="border-amber-600/40 bg-amber-50 text-amber-700">{children}</Badge>;
  }
  return <Badge variant="outline" className="text-muted-foreground">{children}</Badge>;
}

function ScoreLine({ angle, scores, thresholds }) {
  const score = scores?.[angle] ?? null;
  const threshold = thresholds?.[angle];
  const row = scoreRow(angle, score, threshold);
  return (
    <li className="flex flex-wrap items-center gap-2">
      <span className="w-10 shrink-0 font-medium">{ANGLE_LABEL[angle]}</span>
      {row.tone === 'muted' ? (
        // skipped(그 각도에서 얼굴을 아예 못 찾음) — belowThreshold(점수는 있는데 낮음)와
        // 다른 상태라, 점수/배지 대신 회색 안내 배지로 시각적으로 분리한다.
        <Badge variant="outline" className="text-muted-foreground">{row.label}</Badge>
      ) : (
        <>
          <span>{row.percent}</span>
          <span className="text-muted-foreground">{row.baseline}</span>
          <ScoreBadge tone={row.tone}>{row.badge}</ScoreBadge>
          <span className="text-xs text-muted-foreground">({row.multiple})</span>
        </>
      )}
    </li>
  );
}

/* 신분증·등록 사진 한 장. ApplicantPhoto(AdminApplications.jsx)와 같은 모양이지만, 승인·
   거절 직후 신분증이 파기되므로 404 를 "아직 로딩 중"이 아니라 "볼 수 없음"으로 분리해
   보여준다 — 안 그러면 결정된 카드를 다시 열었을 때 스켈레톤이 영원히 돈다.

   imagePath 는 카드 응답의 `images[kind]` 를 그대로 받는다 — 이 컴포넌트가 URL 을
   다시 조립하지 않는다(fix round 1, minor). */
function EnrollmentImage({ imagePath, kind, label }) {
  const [url, setUrl] = useState(null);
  // null = 아직 안 실패. 숫자/0 = 실패(HTTP status, 0 은 네트워크 등 status 미상).
  const [failedStatus, setFailedStatus] = useState(null);
  useEffect(() => {
    if (!imagePath) { setUrl(null); setFailedStatus(404); return undefined; }
    let alive = true;
    let objectUrl = null;
    setUrl(null);
    setFailedStatus(null);
    adminFetchGatedImageUrl(imagePath)
      .then((u) => { if (alive) { objectUrl = u; setUrl(u); } else { URL.revokeObjectURL(u); } })
      .catch((e) => { if (alive) setFailedStatus(e?.status || 0); });
    return () => { alive = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [imagePath]);
  const failed = failedStatus !== null;
  return (
    <figure className="flex w-28 shrink-0 flex-col gap-1">
      {failed && (
        <div className="flex h-36 items-center justify-center rounded-md bg-muted px-1 text-center text-xs text-muted-foreground">
          {imageFailureLabel(failedStatus, kind)}
        </div>
      )}
      {!failed && !url && <Skeleton className="h-36 w-28" />}
      {!failed && url && <img className="h-36 w-28 rounded-md object-cover" src={url} alt={`등록 ${label} 이미지`} />}
      <figcaption className="text-center text-xs text-muted-foreground">{label}</figcaption>
    </figure>
  );
}

/* 지원서 프로필 사진 — 신분증·등록 사진 3장과는 다른 시점에 찍힌 제3의 독립 얼굴
   사진이다. 지원 시점과 등록 시점 사이 인물이 바뀌었는지(스왑) 심사자가 다른 두
   세트와 눈으로 대조할 수 있게 낸다. 새 이미지 라우트를 만들지 않고 기존 관리자
   지원서 사진 라우트(같은 admin_guard, 같은 private/no-store)를 그대로 재사용한다
   (fix round 1, SPEC GAP 2). hasPhoto 는 AdminApplications.jsx 의 hasProfileImage
   게이트(ApplicantPhoto)와 같은 관례다 — 없는 걸 알면서도 fetch 를 걸어 404 를
   "정상적인 없음"으로 삼지 않는다. */
function ApplicationProfilePhoto({ applicationId, hasPhoto }) {
  const [url, setUrl] = useState(null);
  // 실패를 빈 catch 로 삼키면 아래 Skeleton 이 **영원히 돈다** — 바로 옆
  // EnrollmentImage 의 주석이 하지 말라고 적어 둔 그 상태다(최종리뷰 I10). 기기 게이트가
  // enforce 인 프로덕션에서는 이게 기본 상태였다.
  const [failedStatus, setFailedStatus] = useState(null);
  useEffect(() => {
    if (!hasPhoto) return undefined;
    let alive = true;
    let objectUrl = null;
    setUrl(null);
    setFailedStatus(null);
    adminFetchApplicationPhotoUrl(applicationId, 'profile')
      .then((u) => { if (alive) { objectUrl = u; setUrl(u); } else { URL.revokeObjectURL(u); } })
      .catch((e) => { if (alive) setFailedStatus(e?.status || 0); });
    return () => { alive = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [applicationId, hasPhoto]);
  const failed = failedStatus !== null;
  return (
    <figure className="flex w-28 shrink-0 flex-col gap-1">
      {!hasPhoto && (
        <div className="flex h-36 items-center justify-center rounded-md bg-muted text-muted-foreground">—</div>
      )}
      {hasPhoto && failed && (
        <div className="flex h-36 items-center justify-center rounded-md bg-muted px-1 text-center text-xs text-muted-foreground">
          {imageFailureLabel(failedStatus, 'application_profile')}
        </div>
      )}
      {hasPhoto && !failed && !url && <Skeleton className="h-36 w-28" />}
      {hasPhoto && !failed && url && <img className="h-36 w-28 rounded-md object-cover" src={url} alt="지원서 프로필 사진" />}
      <figcaption className="text-center text-xs text-muted-foreground">지원서 프로필</figcaption>
    </figure>
  );
}

/* 전체 등록 사진 한 칸. EnrollmentImage 와 같은 관례다 — 게이트 라우트라 <img src> 로
   직접 걸지 못하고, 인증 fetch 로 받은 objectURL 을 쓰고 언마운트 때 해제한다.

   누르면 크게 본다. 흐림·눈 감음·안경은 축소판에서 안 보인다 — 축소판만 보고 "확인 완료"를
   누르면 그 사진이 그대로 가중치에 들어가고, 되돌리려면 LoRA 를 다시 학습해야 한다. */
function PhotoSlotTile({ imagePath, slotKey, label, onZoom, checked, onToggle }) {
  const [url, setUrl] = useState(null);
  const [failedStatus, setFailedStatus] = useState(null);
  useEffect(() => {
    if (!imagePath) { setUrl(null); setFailedStatus(404); return undefined; }
    let alive = true;
    let objectUrl = null;
    setUrl(null);
    setFailedStatus(null);
    adminFetchGatedImageUrl(imagePath)
      .then((u) => { if (alive) { objectUrl = u; setUrl(u); } else { URL.revokeObjectURL(u); } })
      .catch((e) => { if (alive) setFailedStatus(e?.status || 0); });
    return () => { alive = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [imagePath]);
  const failed = failedStatus !== null;
  return (
    <figure className="flex w-24 shrink-0 flex-col gap-1">
      {failed && (
        <div className="flex h-32 w-24 items-center justify-center rounded-md bg-muted px-1 text-center text-[11px] text-muted-foreground">
          {imageFailureLabel(failedStatus, slotKey)}
        </div>
      )}
      {!failed && !url && <Skeleton className="h-32 w-24" />}
      {!failed && url && (
        <button
          type="button"
          className="h-32 w-24 overflow-hidden rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          onClick={() => onZoom({ url, label })}
        >
          <img className="h-32 w-24 object-cover" src={url} alt={`등록 사진 ${label}`} />
        </button>
      )}
      <figcaption className="text-center text-[11px] leading-tight text-muted-foreground">{label}</figcaption>
      {onToggle && (
        <label className="flex items-start gap-1 text-[11px] leading-tight">
          <input type="checkbox" checked={checked} onChange={(e) => onToggle(slotKey, e.target.checked)} />
          다시 찍기
        </label>
      )}
    </figure>
  );
}

/* 학습 전 사진 확인 — 등록 사진 전부(동의 판에 따라 16 또는 18칸)를 조명 묶음대로 보고,
   확인 완료를 찍거나 칸을 골라 재촬영을 요청한다.

   확인 완료 전까지 학습 내보내기(fm_export_training_set)가 막혀 있다. 확인이 끝나고
   그 모델의 LoRA 가 켜지면 서버가 전체 칸 열람을 다시 닫는다(FULL_PHOTO_SCOPE) —
   그때부터의 열람은 심사가 아니라 구경이라, 여기서도 안내만 남고 사진은 안 나온다. */
function PhotoReviewSection({ card, onChanged }) {
  const { push } = useToast();
  const [zoom, setZoom] = useState(null);
  const [reshooting, setReshooting] = useState(false);
  const [picked, setPicked] = useState({});   // slot -> reason
  const [busy, setBusy] = useState(false);

  // 확대창은 Esc 로도 닫힌다 — 마우스를 옮기지 않고 여러 장을 훑는 게 이 화면의 일이다.
  useEffect(() => {
    if (!zoom) return undefined;
    const onKey = (e) => { if (e.key === 'Escape') setZoom(null); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [zoom]);

  const slots = card.photoSlots || [];
  const status = card.photoReviewStatus || 'pending';
  const visible = !!card.fullPhotosVisible;
  const { identityCleared } = reviewActions(card);
  const pickedKeys = Object.keys(picked);

  const toggle = (slotKey, on) => setPicked((prev) => {
    const next = { ...prev };
    if (on) next[slotKey] = next[slotKey] || '';
    else delete next[slotKey];
    return next;
  });

  const approvePhotos = async () => {
    if (!identityCleared || busy) return;
    setBusy(true);
    try {
      await adminApproveEnrollmentPhotos(card.id);
      push?.('사진 확인을 완료했어요. 이제 학습에 쓸 수 있어요.', { icon: 'check' });
      onChanged();
    } catch (e) {
      push?.(e.message, { icon: 'alertCircle' });
    } finally { setBusy(false); }
  };

  const requestReshoot = async () => {
    setBusy(true);
    try {
      await adminRequestEnrollmentReshoot(
        card.id,
        pickedKeys.map((slot) => ({ slot, reason: picked[slot] || '' })),
      );
      push?.(`${pickedKeys.length}칸 재촬영을 요청했어요.`, { icon: 'check' });
      onChanged();
    } catch (e) {
      push?.(e.message, { icon: 'alertCircle' });
    } finally { setBusy(false); }
  };

  return (
    <section className="border-t border-border pt-4">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <h4 className="text-xs font-medium text-muted-foreground">학습 전 사진 확인 ({slots.length}칸)</h4>
        <Badge variant={status === 'approved' ? 'default' : status === 'reshoot_requested' ? 'destructive' : 'secondary'}>
          {PHOTO_REVIEW_LABEL[status] || status}
        </Badge>
        {card.photoReviewedAt && (
          <span className="text-xs text-muted-foreground">{seoulDateTime(card.photoReviewedAt)}</span>
        )}
      </div>

      {!visible && (
        <p className="text-sm text-muted-foreground">
          확인이 끝나고 이 모델의 얼굴 자산이 살아 있어요 — 전체 사진은 더 열리지 않아요.
        </p>
      )}

      {visible && PHOTO_GROUPS.map((group) => {
        const groupSlots = slots.filter((key) => SLOT_BY_KEY.get(key)?.group === group.id);
        if (groupSlots.length === 0) return null;
        return (
          <div key={group.id} className="mb-3">
            <p className="mb-1 text-xs font-medium">{group.title} <span className="font-normal text-muted-foreground">· {group.badge}</span></p>
            <div className="flex flex-wrap gap-2">
              {groupSlots.map((key) => (
                <PhotoSlotTile
                  key={key}
                  imagePath={card.images?.[key]}
                  slotKey={key}
                  label={`${SLOT_BY_KEY.get(key)?.n ?? ''}. ${SLOT_BY_KEY.get(key)?.title || key}`}
                  onZoom={setZoom}
                  checked={key in picked}
                  onToggle={reshooting ? toggle : null}
                />
              ))}
            </div>
          </div>
        );
      })}

      {/* 이미 요청해 둔 재촬영 칸 — 모델이 다시 올리기를 기다리는 중이다. */}
      {status === 'reshoot_requested' && (card.reshootSlots || []).length > 0 && (
        <ul className="mb-3 list-none p-0 text-xs text-destructive">
          {card.reshootSlots.map((item) => (
            <li key={item.slot}>
              {SLOT_BY_KEY.get(item.slot)?.title || item.slot}
              {item.reason ? `: ${item.reason}` : ''}
            </li>
          ))}
        </ul>
      )}

      {visible && !identityCleared && (
        <p className="mb-2 text-xs text-muted-foreground">신원 확인을 먼저 마쳐 주세요.</p>
      )}
      {visible && !reshooting && (
        <div className="flex flex-wrap gap-2">
          <Button variant="default" size="sm" disabled={busy || !identityCleared} onClick={approvePhotos}>사진 확인 완료</Button>
          <Button variant="outline" size="sm" disabled={busy} onClick={() => setReshooting(true)}>재촬영 요청</Button>
        </div>
      )}

      {visible && reshooting && (
        <div className="flex flex-col gap-2">
          <p className="text-xs text-muted-foreground">다시 찍을 칸을 고르고, 왜 다시 찍어야 하는지 적어 주세요 (모델이 그대로 읽어요).</p>
          {pickedKeys.map((key) => (
            <label key={key} className="flex flex-col gap-1 text-xs">
              <span className="font-medium">{SLOT_BY_KEY.get(key)?.title || key}</span>
              <Textarea
                rows={1}
                value={picked[key]}
                onChange={(e) => setPicked((prev) => ({ ...prev, [key]: e.target.value }))}
                placeholder="예: 얼굴이 흔들려 흐려요"
              />
            </label>
          ))}
          <div className="flex gap-2">
            <Button variant="destructive" size="sm" disabled={busy || pickedKeys.length === 0} onClick={requestReshoot}>
              재촬영 요청 보내기
            </Button>
            <Button variant="ghost" size="sm" disabled={busy} onClick={() => { setReshooting(false); setPicked({}); }}>
              취소
            </Button>
          </div>
        </div>
      )}

      {zoom && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label={`${zoom.label} 크게 보기`}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4"
          onClick={() => setZoom(null)}
        >
          <figure className="flex max-h-full flex-col items-center gap-2">
            <img className="max-h-[80vh] max-w-full rounded-md object-contain" src={zoom.url} alt={`${zoom.label} 크게 보기`} />
            <figcaption className="text-sm text-white">{zoom.label} · 아무 곳이나 누르거나 Esc 로 닫아요</figcaption>
          </figure>
        </div>
      )}
    </section>
  );
}

function EnrollmentDetail({ enrollmentId, refreshVersion, onDecided }) {
  const { push } = useToast();
  const [card, setCard] = useState(null);
  const [detailError, setDetailError] = useState(null);
  const [identityOk, setIdentityOk] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [reasonPreset, setReasonPreset] = useState(REJECT_REASON_PRESETS[0].value);
  const [reasonFreeText, setReasonFreeText] = useState('');
  const [busy, setBusy] = useState(false);
  const detailRequest = useRef(0);

  const load = useCallback(() => {
    const request = ++detailRequest.current;
    setDetailError(null);
    adminEnrollmentCard(enrollmentId)
      .then((nextCard) => {
        if (request === detailRequest.current) setCard(nextCard);
      })
      .catch((e) => {
        if (request === detailRequest.current) setDetailError(e.message || '등록 정보를 불러오지 못했어요.');
      });
  }, [enrollmentId]);

  useEffect(() => {
    load();
    return () => { detailRequest.current += 1; };
  }, [load, refreshVersion]);

  if (detailError && !card) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">등록 정보를 불러오지 못했어요</CardTitle>
          <CardDescription>{detailError}</CardDescription>
        </CardHeader>
        <CardContent>
          <Button variant="outline" size="sm" onClick={load}>다시 시도</Button>
        </CardContent>
      </Card>
    );
  }

  if (!card) {
    return (
      <Card>
        <CardContent className="pt-5">
          <Skeleton className="h-64" />
        </CardContent>
      </Card>
    );
  }

  const pending = card.reviewStatus === 'pending';
  const { canApproveIdentity, approvalLabel, approvalHint } = reviewActions(card);
  const presetLabel = REJECT_REASON_PRESETS.find((r) => r.value === reasonPreset)?.label || '';
  // finalRejectReason 이 trim 을 전담한다(enrollmentReviewMath.js) — 공백만 입력해도
  // "   " 는 truthy 라 !finalReason 가드를 통과해 버리는 사고를 이 컴포넌트가 아니라
  // 직접 테스트 가능한 순수 함수가 막는다(fix round 1, IMPORTANT).
  const finalReason = finalRejectReason(reasonPreset, reasonFreeText, presetLabel);

  // 결정이 끝나면(성공이든 asset_build_error 든) 카드를 닫고 큐를 새로고침한다 — 신분증이
  // 파기돼 다시 불러오면 404 다. 409(다른 관리자가 먼저 처리)도 같은 처리 — 지금 보고
  // 있는 카드는 이미 낡은 상태다.
  const approve = async () => {
    if (!canApproveIdentity || !identityOk || busy) return;
    setBusy(true);
    try {
      const result = await adminApproveEnrollment(enrollmentId);
      if (result?.assetBuildError) {
        // 승인(review_status='approved')은 이미 커밋됐다 — 이건 그 뒤 자산빌드 재개
        // 실패다. 조용한 성공 토스트로 가리면 안 된다(승인 버튼을 누른 바로 그 관리자가
        // 즉시 알아야 한다).
        push?.(
          `승인은 됐지만 자산 생성을 다시 시작하지 못했어요 (${result.assetBuildError}). 운영팀에 알려주세요.`,
          { icon: 'alertCircle' },
        );
      } else if (result?.status === 'vc_pending') {
        push?.('신원 확인을 마쳤어요. 증서는 몇 분 안에 자동으로 발급돼요.', { icon: 'check' });
      } else {
        push?.('등록을 승인했어요.', { icon: 'check' });
      }
      onDecided();
    } catch (e) {
      push?.(e.message, { icon: 'alertCircle' });
      if (e?.status === 409) onDecided();
    } finally {
      setBusy(false);
    }
  };

  const reject = async () => {
    setBusy(true);
    try {
      await adminRejectEnrollment(enrollmentId, finalReason);
      push?.('등록을 거절했어요.', { icon: 'check' });
      onDecided();
    } catch (e) {
      push?.(e.message, { icon: 'alertCircle' });
      if (e?.status === 409) onDecided();
    } finally {
      setBusy(false);
    }
  };

  const app = card.application;
  const anchor = card.matchScores?.anchor;

  // 카드 사진과 인증된 신원(캐리어가 증명한 이름·생년, Task6)을 나란히 붙인다 —
  // 심사자가 "이 카드가 방금 인증된 그 사람 것인가"를 판단하는 게 이 화면의
  // 유일한 목적이라, 대조 대상 두 가지를 눈을 옮기지 않고 한 번에 봐야 한다.
  // 지원서 자기신고(아래 '지원서' 섹션의 applicantName/birthdate)와 다르다 — 저건
  // 위조될 수 있고, 이건 위조될 수 없는 본인확인 결과다.
  const idDocumentWithIdentity = (
    <div className="flex items-end gap-2">
      <EnrollmentImage
        imagePath={card.images?.id_document}
        kind="id_document"
        label="신분증 (마스킹 전체본)"
      />
      <dl className="text-xs leading-relaxed">
        <div><dt className="text-muted-foreground">인증된 이름</dt><dd>{card.identityNameMasked || '-'}</dd></div>
        <div><dt className="text-muted-foreground">인증된 출생연도</dt><dd>{card.identityBirthYear || '-'}</dd></div>
      </dl>
    </div>
  );

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center gap-2">
          <CardTitle className="text-base">등록 {card.id.slice(0, 8)}</CardTitle>
          <Badge variant="secondary">{IDENTITY_METHOD_LABEL[card.identityMethod] || card.identityMethod}</Badge>
          <Badge variant={pending ? 'secondary' : card.reviewStatus === 'rejected' ? 'destructive' : 'default'}>
            {REVIEW_LABEL[card.reviewStatus] || card.reviewStatus}
          </Badge>
          {/* mask_mode 가 'auto' 가 아니면(기하 검증을 통과 못 했거나, 검사 자체가
              안 돎) 눈에 띄게 낸다 — 서버가 확인해 주지 못한 건이니 심사자가 신분증
              사진을 볼 때 더 꼼꼼히 봐야 한다는 신호다. */}
          {card.maskMode !== 'auto' && (
            <Badge variant="outline" className="border-amber-600/40 bg-amber-50 text-amber-700">
              {MASK_MODE_LABEL}
            </Badge>
          )}
        </div>
        <CardDescription>{seoulDateTime(card.createdAt)} 제출</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-5 text-sm">
        <div className={`${styles.detailRefreshFeedback} flex items-center gap-3`}>
          <p className={`${styles.detailRefreshMessage} text-destructive`} role="alert" tabIndex={detailError ? 0 : undefined}>
            {detailError}
          </p>
          {detailError && <Button variant="outline" size="sm" onClick={load}>다시 시도</Button>}
        </div>
        <section>
          <h4 className="mb-2 text-xs font-medium text-muted-foreground">신분증·등록 사진·지원서 사진</h4>
          <div className="flex flex-wrap gap-3">
            {idDocumentWithIdentity}
            {IMAGE_KINDS.filter(({ kind }) => kind !== 'id_document').map(({ kind, label }) => (
              <EnrollmentImage key={kind} imagePath={card.images?.[kind]} kind={kind} label={label} />
            ))}
            {/* 지원서가 있으면(application_id) 슬롯을 낸다 — AdminApplications.jsx 의
                ApplicantPhoto/hasProfileImage 관례와 같다: 슬롯 자체는 항상 그리고,
                사진이 실제로 있을 때만(hasProfileImage) fetch 를 건다. 무턱대고 걸어
                404 를 받는 대신, 서버가 이미 아는 사실을 그대로 쓴다(fix round 1). */}
            {card.applicationId && (
              <ApplicationProfilePhoto applicationId={card.applicationId} hasPhoto={!!app?.hasProfileImage} />
            )}
          </div>
        </section>

        <section>
          <h4 className="mb-1 text-xs font-medium text-muted-foreground">지원서</h4>
          {app ? (
            <>
              <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 sm:grid-cols-3">
                <div><dt className="text-xs text-muted-foreground">이름</dt><dd className="truncate">{app.applicantName || '-'}</dd></div>
                <div><dt className="text-xs text-muted-foreground">생년월일</dt><dd className="truncate">{app.birthdate || '-'}</dd></div>
                <div><dt className="text-xs text-muted-foreground">성별</dt><dd className="truncate">{app.gender === 'male' ? '남성' : app.gender === 'female' ? '여성' : '-'}</dd></div>
                <div><dt className="text-xs text-muted-foreground">지역</dt><dd className="truncate">{app.region || '-'}</dd></div>
                <div><dt className="text-xs text-muted-foreground">키</dt><dd className="truncate">{app.heightCm ? `${app.heightCm}cm` : '-'}</dd></div>
                <div><dt className="text-xs text-muted-foreground">몸무게</dt><dd className="truncate">{app.weightKg != null ? `${app.weightKg}kg` : '-'}</dd></div>
                <div><dt className="text-xs text-muted-foreground">전화</dt><dd className="truncate">{app.phone || '-'}</dd></div>
              </dl>
              {/* identity_mismatch_count 는 identity_method 로 안 갈린다 — simple_auth
                  등록도 지원서 이름·생년월일이 신분증과 이미 몇 번 어긋났는지 오른다
                  (fix round 1, SPEC GAP 1). 0 이면 굳이 안 보여준다(0 회는 안심 신호가
                  아니라 그냥 '아직 문제 없었다'라 강조할 정보가 아니다). */}
              {app.identityMismatchCount > 0 && (
                <p className="mt-2 text-xs text-destructive">
                  지원서에 적은 이름·생년월일이 신분증과 {app.identityMismatchCount}회 일치하지 않았어요.
                </p>
              )}
            </>
          ) : <p className="text-muted-foreground">연결된 지원서가 없어요.</p>}
        </section>

        <section>
          <h4 className="mb-2 text-xs font-medium text-muted-foreground">
            자동 대조 점수 — 참고용, 승인/거절을 막지 않아요
            {anchor && <span className="ml-1 font-normal">· 대조 기준: {ANCHOR_LABEL[anchor] || anchor}</span>}
          </h4>
          {!card.matchScores && <p className="text-muted-foreground">대조 기록이 없어요.</p>}
          {card.matchScores && (
            <ul className="flex list-none flex-col gap-1.5 p-0">
              {ANGLES.map((angle) => (
                <ScoreLine
                  key={angle}
                  angle={angle}
                  scores={card.matchScores.scores}
                  thresholds={card.matchScores.thresholds}
                />
              ))}
            </ul>
          )}
        </section>

        {/* 사진은 미리 볼 수 있고, 확인 완료는 신원 승인 뒤에 할 수 있어요. */}
        {(card.fullPhotosVisible || card.photoReviewStatus !== 'pending') && (
          <PhotoReviewSection card={card} onChanged={load} />
        )}

        {pending && !rejecting && (
          <section className="border-t border-border pt-4">
            {/* 신분증 사진과 등록 사진의 동일인 여부를 직접 확인해 주세요. */}
            <label className="mb-3 flex items-start gap-2">
              <input
                type="checkbox"
                checked={identityOk}
                onChange={(e) => setIdentityOk(e.target.checked)}
                className="mt-0.5"
              />
              신분증 사진과 등록 사진이 같은 사람이에요. 주민등록번호 뒤 7자리도 가려져 있어요.
            </label>
            {approvalHint && <p className="mb-3 text-xs text-muted-foreground">{approvalHint}</p>}
            <div className="flex flex-wrap gap-2">
              <Button variant="default" size="sm" disabled={!identityOk || !canApproveIdentity || busy} onClick={approve}>{approvalLabel}</Button>
              <Button variant="outline" size="sm" disabled={busy} onClick={() => setRejecting(true)}>거절</Button>
            </div>
          </section>
        )}

        {pending && rejecting && (
          <section className="flex flex-col gap-2 border-t border-border pt-4">
            <div className="flex flex-col gap-1.5">
              {REJECT_REASON_PRESETS.map((opt) => (
                <label key={opt.value} className="flex items-center gap-2">
                  <input
                    type="radio"
                    name={`reject-reason-${card.id}`}
                    value={opt.value}
                    checked={reasonPreset === opt.value}
                    onChange={() => setReasonPreset(opt.value)}
                  />
                  {opt.label}
                </label>
              ))}
            </div>
            {reasonPreset === 'other' && (
              <Textarea
                value={reasonFreeText}
                onChange={(e) => setReasonFreeText(e.target.value)}
                placeholder="거절 사유를 입력해 주세요 (지원자에게 전달될 수 있어요)"
                rows={2}
              />
            )}
            <div className="flex gap-2">
              <Button variant="destructive" size="sm" disabled={busy || !finalReason} onClick={reject}>거절 확정</Button>
              <Button
                variant="ghost"
                size="sm"
                disabled={busy}
                onClick={() => { setRejecting(false); setReasonPreset(REJECT_REASON_PRESETS[0].value); setReasonFreeText(''); }}
              >
                취소
              </Button>
            </div>
          </section>
        )}

        {!pending && (
          <section className="border-t border-border pt-4 text-muted-foreground">
            <p>{REVIEW_LABEL[card.reviewStatus] || card.reviewStatus}{card.reviewedAt ? ` · ${seoulDateTime(card.reviewedAt)}` : ''}</p>
            {card.reviewReason && <p className="mt-1 text-destructive">거절 사유: {card.reviewReason}</p>}
          </section>
        )}
      </CardContent>
    </Card>
  );
}

export function AdminEnrollmentReview() {
  const [searchParams] = useSearchParams();
  const [review, setReview] = useState(() => searchParams.get('tab') === PHOTO_FILTER ? PHOTO_FILTER : 'pending');
  const [items, setItems] = useState(null);
  const [listError, setListError] = useState(null);
  const [selectedId, setSelectedId] = useState(null);
  const [refreshVersion, setRefreshVersion] = useState(0);

  const load = useCallback(() => {
    setListError(null);
    setRefreshVersion((version) => version + 1);
    // '사진 확인' 탭만 다른 라우트다 — 필터 축(photo_review_status)도, 대상(mid 포함)도 다르다.
    const request = review === PHOTO_FILTER
      ? adminListPhotoReview('awaiting')
      : adminListEnrollments(review);
    request
      .then(setItems)
      .catch((e) => setListError(e.message || '심사 큐를 불러오지 못했어요.'));
  }, [review]);

  useEffect(() => { setItems(null); load(); }, [load]);

  // 승인·거절 직후 이 콜백이 카드를 닫고 큐를 새로고침한다 — 신분증이 결정과 동시에
  // 파기되므로, 닫지 않고 그대로 두면 다음 조회가 404 를 받는다.
  const handleDecided = useCallback(() => {
    setSelectedId(null);
    load();
  }, [load]);

  return (
    <div className={`${styles.page} flex flex-col gap-5`}>
      <header>
        <h1 className="text-lg font-semibold tracking-tight">등록 심사</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          간편인증으로 들어온 등록의 신분증과 등록 사진을 보고 신원을 확인해요.
          등록자가 사용 조건까지 마치면 승인할 수 있고, 승인하면 라이선스 증서(VC)가 자동으로 발급돼요.
          등록자는 기다리지 않아요. 5일 안에 결정하지 않으면 등록이 자동 종료돼요.
        </p>
        <p className="mt-1 text-sm text-muted-foreground">
          <strong className="font-medium">사진 확인</strong>은 신원 확인을 마친 뒤 진행해요. 등록한
          얼굴 사진 전부를 학습 전에 보고, 확인 완료를 찍거나 칸을 골라 재촬영을 요청해요.
          신원 확인과 사진 확인을 모두 마쳐야 학습 내보내기가 열려요.
        </p>
      </header>

      <div className="flex flex-wrap items-center gap-2">
        {REVIEW_FILTERS.map((f) => (
          <Button
            key={f.value}
            size="sm"
            variant={f.value === review ? 'default' : 'outline'}
            onClick={() => { setReview(f.value); setSelectedId(null); }}
          >
            {f.label}
          </Button>
        ))}
        <Button variant="ghost" size="sm" onClick={load}>새로고침</Button>
      </div>

      <div className="grid gap-5 lg:grid-cols-[1fr_28rem]">
        <Card>
          <CardContent className="p-0">
            {listError && (
              <div className="flex flex-col items-center gap-3 px-5 py-10 text-center text-sm text-muted-foreground">
                <p>{listError}</p>
                <Button variant="outline" size="sm" onClick={load}>다시 시도</Button>
              </div>
            )}
            {!items && !listError && <Skeleton className="h-64" />}
            {items && (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>등록</TableHead>
                    <TableHead>방식</TableHead>
                    <TableHead>{review === PHOTO_FILTER ? '사진 확인' : '제출일'}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map((row) => (
                    <TableRow
                      key={row.id}
                      tabIndex={0}
                      role="button"
                      aria-pressed={selectedId === row.id}
                      onClick={() => setSelectedId(row.id)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault();
                          setSelectedId(row.id);
                        }
                      }}
                      className={`cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring ${selectedId === row.id ? 'bg-muted' : ''}`}
                    >
                      <TableCell className="font-mono text-xs">{row.id.slice(0, 8)}</TableCell>
                      <TableCell>
                        <div className="flex flex-col items-start gap-1">
                          <span>{IDENTITY_METHOD_LABEL[row.identityMethod] || row.identityMethod}</span>
                          {review !== PHOTO_FILTER && (
                            <Badge variant="secondary">
                              {row.status === 'vc_pending' && row.reviewStatus === 'approved'
                                ? '증서 발급 대기'
                                : ENROLLMENT_STATUS_LABEL[row.status] || row.status}
                            </Badge>
                          )}
                        </div>
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {review === PHOTO_FILTER ? (
                          <span>
                            {PHOTO_REVIEW_LABEL[row.photoReviewStatus] || row.photoReviewStatus}
                            {row.reshootSlotCount > 0 ? ` · ${row.reshootSlotCount}칸` : ''}
                          </span>
                        ) : seoulDateTime(row.createdAt)}
                      </TableCell>
                    </TableRow>
                  ))}
                  {items.length === 0 && (
                    <TableRow>
                      <TableCell colSpan={3} className="py-8 text-center text-muted-foreground">
                        {review === PHOTO_FILTER
                          ? '확인을 기다리는 등록 사진이 없어요.'
                          : `${REVIEW_LABEL[review] || '해당'} 상태의 등록이 없어요.`}
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        {/* key={selectedId} — 카드를 바꿀 때마다 완전히 새로 마운트한다. 마스킹 체크는
            안전장치라, 이전 카드에서 체크한 상태가 다음 카드로 새어 들어가면 안 된다. */}
        {selectedId && <EnrollmentDetail key={selectedId} enrollmentId={selectedId} refreshVersion={refreshVersion} onDecided={handleDecided} />}
      </div>
    </div>
  );
}

export default AdminEnrollmentReview;
