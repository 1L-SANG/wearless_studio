/* =============================================================
   관리자 등록 심사 콘솔 (admin.wearless.kr) — 간편인증(simple_auth) review_pending 큐.

   앵커가 사용자가 손에 들고 촬영한 신분증이다 — 위조 가능한 증거라 기계 점수(match_scores)가
   어느 방향으로도 신뢰 판정을 내리지 못한다(위조된 카드도 진짜 얼굴을 담고, 코팅 반사광
   한 번에 진짜 카드의 점수가 떨어진다). 그래서 점수는 "정보"로만 보여준다 — 백분율 +
   기준선(임계) + 배수 + 배지를 한 줄에 함께 내고, 배지는 승인/거절 버튼을 절대 막지
   않는다(enrollmentReviewMath.js 의 scoreRow). 서버가 검증할 수 없는 유일한 항목
   (주민등록번호 뒷자리 마스킹)만 체크박스로 승인을 막는다.

   좌측 큐(review 필터: pending/approved/rejected) + 우측 카드(AdminModels.jsx 의
   목록+상세 그리드 선례). 카드 선택이 바뀔 때마다 <EnrollmentDetail key={id}> 로
   완전히 새로 마운트한다 — 마스킹 체크는 안전장치라 이전 카드에서 체크한 상태가 다음
   카드로 새어 들어가면 안 된다.

   이미지는 게이트 라우트(no-store, private)라 <img src> 로 못 건다 — 인증 fetch 로
   받아 objectURL 을 만들고(adminFetchEnrollmentImageUrl), 카드가 닫히면(언마운트)
   즉시 해제한다. 생체 이미지를 앱 상태에 오래 남기지 않고, 어디에도 로그로 남기지
   않는다.

   승인·거절 직후 카드를 닫고 큐를 새로고침한다 — 신분증은 결정과 동시에 파기되므로
   다시 불러오면 404 다.
   ============================================================= */
import { useCallback, useEffect, useState } from 'react';
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
  adminApproveEnrollment, adminEnrollmentCard, adminFetchEnrollmentImageUrl,
  adminListEnrollments, adminRejectEnrollment,
} from '@/lib/api/facemarket.js';
import { seoulDateTime } from '@/lib/datetime.js';
import { scoreRow } from './enrollmentReviewMath.js';

const REVIEW_FILTERS = [
  { value: 'pending', label: '대기' },
  { value: 'approved', label: '승인' },
  { value: 'rejected', label: '거절' },
];
const REVIEW_LABEL = { pending: '대기', approved: '승인됨', rejected: '거절됨' };

const IDENTITY_METHOD_LABEL = { mid: '표준인증', simple_auth: '간편인증' };

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
   보여준다 — 안 그러면 결정된 카드를 다시 열었을 때 스켈레톤이 영원히 돈다. */
function EnrollmentImage({ enrollmentId, kind, label }) {
  const [url, setUrl] = useState(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let alive = true;
    let objectUrl = null;
    setUrl(null);
    setFailed(false);
    adminFetchEnrollmentImageUrl(enrollmentId, kind)
      .then((u) => { if (alive) { objectUrl = u; setUrl(u); } else { URL.revokeObjectURL(u); } })
      .catch(() => { if (alive) setFailed(true); });
    return () => { alive = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [enrollmentId, kind]);
  return (
    <figure className="flex w-28 shrink-0 flex-col gap-1">
      {failed && (
        <div className="flex h-36 items-center justify-center rounded-md bg-muted px-1 text-center text-xs text-muted-foreground">
          볼 수 없음{kind === 'id_document' ? ' (파기됨)' : ''}
        </div>
      )}
      {!failed && !url && <Skeleton className="h-36 w-28" />}
      {!failed && url && <img className="h-36 w-28 rounded-md object-cover" src={url} alt={`등록 ${label} 이미지`} />}
      <figcaption className="text-center text-xs text-muted-foreground">{label}</figcaption>
    </figure>
  );
}

function EnrollmentDetail({ enrollmentId, onDecided }) {
  const { push } = useToast();
  const [card, setCard] = useState(null);
  const [detailError, setDetailError] = useState(null);
  const [maskOk, setMaskOk] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [reasonPreset, setReasonPreset] = useState(REJECT_REASON_PRESETS[0].value);
  const [reasonFreeText, setReasonFreeText] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    setCard(null);
    setDetailError(null);
    adminEnrollmentCard(enrollmentId)
      .then(setCard)
      .catch((e) => setDetailError(e.message || '등록 정보를 불러오지 못했어요.'));
  }, [enrollmentId]);

  useEffect(() => { load(); }, [load]);

  if (detailError) {
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
  const presetLabel = REJECT_REASON_PRESETS.find((r) => r.value === reasonPreset)?.label || '';
  const finalReason = reasonPreset === 'other' ? reasonFreeText.trim() : presetLabel;

  // 결정이 끝나면(성공이든 asset_build_error 든) 카드를 닫고 큐를 새로고침한다 — 신분증이
  // 파기돼 다시 불러오면 404 다. 409(다른 관리자가 먼저 처리)도 같은 처리 — 지금 보고
  // 있는 카드는 이미 낡은 상태다.
  const approve = async () => {
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

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center gap-2">
          <CardTitle className="text-base">등록 {card.id.slice(0, 8)}</CardTitle>
          <Badge variant="secondary">{IDENTITY_METHOD_LABEL[card.identityMethod] || card.identityMethod}</Badge>
          <Badge variant={pending ? 'secondary' : card.reviewStatus === 'rejected' ? 'destructive' : 'default'}>
            {REVIEW_LABEL[card.reviewStatus] || card.reviewStatus}
          </Badge>
        </div>
        <CardDescription>{seoulDateTime(card.createdAt)} 제출</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-5 text-sm">
        <section>
          <h4 className="mb-2 text-xs font-medium text-muted-foreground">신분증·등록 사진</h4>
          <div className="flex flex-wrap gap-3">
            {IMAGE_KINDS.map(({ kind, label }) => (
              <EnrollmentImage key={kind} enrollmentId={card.id} kind={kind} label={label} />
            ))}
          </div>
        </section>

        <section>
          <h4 className="mb-1 text-xs font-medium text-muted-foreground">지원서</h4>
          {app ? (
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 sm:grid-cols-3">
              <div><dt className="text-xs text-muted-foreground">이름</dt><dd className="truncate">{app.applicantName || '-'}</dd></div>
              <div><dt className="text-xs text-muted-foreground">생년월일</dt><dd className="truncate">{app.birthdate || '-'}</dd></div>
              <div><dt className="text-xs text-muted-foreground">성별</dt><dd className="truncate">{app.gender === 'male' ? '남성' : app.gender === 'female' ? '여성' : '-'}</dd></div>
              <div><dt className="text-xs text-muted-foreground">지역</dt><dd className="truncate">{app.region || '-'}</dd></div>
              <div><dt className="text-xs text-muted-foreground">키</dt><dd className="truncate">{app.heightCm ? `${app.heightCm}cm` : '-'}</dd></div>
              <div><dt className="text-xs text-muted-foreground">몸무게</dt><dd className="truncate">{app.weightKg != null ? `${app.weightKg}kg` : '-'}</dd></div>
              <div><dt className="text-xs text-muted-foreground">전화</dt><dd className="truncate">{app.phone || '-'}</dd></div>
            </dl>
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

        {pending && !rejecting && (
          <section className="border-t border-border pt-4">
            {/* 서버는 마스킹 여부를 검증할 수 없다 — 이 체크가 그 자리를 메운다. 승인
                버튼을 막는 유일한 게이트다(점수 배지는 절대 여기 쓰지 않는다). */}
            <label className="mb-3 flex items-start gap-2">
              <input
                type="checkbox"
                checked={maskOk}
                onChange={(e) => setMaskOk(e.target.checked)}
                className="mt-0.5"
              />
              주민등록번호 뒷자리가 가려져 있어요
            </label>
            <div className="flex gap-2">
              <Button variant="default" size="sm" disabled={!maskOk || busy} onClick={approve}>승인</Button>
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
  const [review, setReview] = useState('pending');
  const [items, setItems] = useState(null);
  const [listError, setListError] = useState(null);
  const [selectedId, setSelectedId] = useState(null);

  const load = useCallback(() => {
    setItems(null);
    setListError(null);
    adminListEnrollments(review)
      .then(setItems)
      .catch((e) => setListError(e.message || '심사 큐를 불러오지 못했어요.'));
  }, [review]);

  useEffect(() => { load(); }, [load]);

  // 승인·거절 직후 이 콜백이 카드를 닫고 큐를 새로고침한다 — 신분증이 결정과 동시에
  // 파기되므로, 닫지 않고 그대로 두면 다음 조회가 404 를 받는다.
  const handleDecided = useCallback(() => {
    setSelectedId(null);
    load();
  }, [load]);

  return (
    <div className="flex flex-col gap-5">
      <header>
        <h1 className="text-lg font-semibold tracking-tight">등록 심사</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          간편인증으로 들어온 등록의 신분증·등록 사진·지원서를 보고 승인 또는 거절해요.
          대조 점수는 참고용이에요 — 위조 신분증도 점수가 높게 나올 수 있고, 진짜
          신분증도 반사광 때문에 낮게 나올 수 있어요.
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
                    <TableHead>제출일</TableHead>
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
                      <TableCell>{IDENTITY_METHOD_LABEL[row.identityMethod] || row.identityMethod}</TableCell>
                      <TableCell className="text-muted-foreground">{seoulDateTime(row.createdAt)}</TableCell>
                    </TableRow>
                  ))}
                  {items.length === 0 && (
                    <TableRow>
                      <TableCell colSpan={3} className="py-8 text-center text-muted-foreground">
                        {REVIEW_LABEL[review] || '해당'} 상태의 등록이 없어요.
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
        {selectedId && <EnrollmentDetail key={selectedId} enrollmentId={selectedId} onDecided={handleDecided} />}
      </div>
    </div>
  );
}

export default AdminEnrollmentReview;
