/* =============================================================
   관리자 모델 지원서 검토 콘솔 (admin.wearless.kr).

   지원서를 카드로 확인하고 승인/거절한다. 서버가 repo.is_admin 을 강제하므로 비관리자는
   403 을 받는다(이 화면은 그 경우 안내를 보여준다). 거절 시 사유를 함께 보낸다.
   동시 처리 레이스는 서버의 status 가드 UPDATE 가 409 로 막는다 — 목록을 새로고침해 반영.
   설계: docs/designs/facemarket-application-renewal.md

   Task 6: shadcn 마크업으로 이관. 훅·API 호출·objectURL 해제·409 처리는 이관 전과
   한 줄도 다르지 않다. 딱 하나 예외가 있었다 — 거절 사유 입력이 <textarea rows={2}>
   에서 <Input>(=<input type="text">)으로 한 줄로 좁아졌다가, 리뷰에서 잡혀 Textarea 로
   되돌아갔다(admin-ui/textarea.jsx). useToast() 는 ui.jsx 의 유일한 생존 수입이다
   (ToastProvider 는 AppProviders 에 남아 있고 스타일은 studio 레이어가 준다).
   ============================================================= */
import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useToast } from '@/components/ui.jsx';
import { Badge } from '@/components/admin-ui/badge.jsx';
import { Button } from '@/components/admin-ui/button.jsx';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/admin-ui/card.jsx';
import { Skeleton } from '@/components/admin-ui/skeleton.jsx';
import { Textarea } from '@/components/admin-ui/textarea.jsx';
import {
  adminApproveApplication, adminFetchApplicationPhotoUrl,
  adminListApplications, adminRejectApplication, adminResendEmail,
} from '@/lib/api/facemarket.js';

const STATUS_FILTERS = [
  { value: 'under_review', label: '검토 중' },
  { value: 'approved', label: '승인' },
  { value: 'rejected', label: '거절' },
  { value: 'cancelled', label: '취소' },
];
const STATUS_LABEL = {
  under_review: '검토 중', approved: '승인됨', rejected: '거절됨', cancelled: '취소됨',
};
const STATUS_BADGE_VARIANT = {
  under_review: 'secondary', approved: 'default', rejected: 'destructive', cancelled: 'outline',
};
const CATEGORY_LABEL = {
  fashion: '패션', commercial: '커머셜', fitness: '피트니스', lifestyle: '라이프스타일',
};
const EXPERIENCE_LABEL = {
  none: '경력 없음', beginner: '입문', intermediate: '중급', professional: '전문',
};

const PHOTO_KINDS = [
  { kind: 'profile', label: '프로필' },
  { kind: 'closeup', label: '클로즈업' },
  { kind: 'waist_up', label: '상반신' },
  { kind: 'full_length', label: '전신' },
];

function ApplicantPhoto({ applicationId, kind, label, hasPhoto }) {
  const [url, setUrl] = useState(null);
  useEffect(() => {
    if (!hasPhoto) return undefined;
    let alive = true;
    let objectUrl = null;
    adminFetchApplicationPhotoUrl(applicationId, kind)
      .then((u) => { if (alive) { objectUrl = u; setUrl(u); } else { URL.revokeObjectURL(u); } })
      .catch(() => {});
    return () => { alive = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [applicationId, kind, hasPhoto]);
  return (
    <figure className="flex w-24 shrink-0 flex-col gap-1">
      {!hasPhoto && <div className="flex h-32 items-center justify-center rounded-md bg-muted text-muted-foreground">—</div>}
      {hasPhoto && !url && <Skeleton className="h-32 w-24" />}
      {hasPhoto && url && <img className="h-32 w-24 rounded-md object-cover" src={url} alt={`지원자 ${label} 사진`} />}
      <figcaption className="text-center text-xs text-muted-foreground">{label}</figcaption>
    </figure>
  );
}

function ApplicantPhotos({ app }) {
  // 지원서는 프로필 1장이 기본. 추가 종류가 저장돼 있으면 그것만 더 보여준다(빈 슬롯 4개 X).
  const present = Array.isArray(app.photoKinds) ? app.photoKinds : (app.hasProfileImage ? ['profile'] : []);
  const slots = PHOTO_KINDS.filter((k) => k.kind === 'profile' || present.includes(k.kind));
  return (
    <div className="flex flex-col gap-2">
      {slots.map((k) => (
        <ApplicantPhoto key={k.kind} applicationId={app.id} kind={k.kind} label={k.label} hasPhoto={present.includes(k.kind)} />
      ))}
    </div>
  );
}

function ApplicationCard({ app, onApprove, onReject, onResend, busy }) {
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState('');
  const pending = app.status === 'under_review';
  const decided = app.status === 'approved' || app.status === 'rejected';
  const emailFailed = app.lastEmailStatus === 'failed' || (decided && !app.lastEmailStatus);

  return (
    <Card className="flex gap-5 p-5">
      <ApplicantPhotos app={app} />
      <div className="min-w-0 flex-1">
        <div className="mb-3 flex items-center gap-2.5">
          <CardTitle className="text-base">{app.applicantName}</CardTitle>
          <Badge variant={STATUS_BADGE_VARIANT[app.status] || 'secondary'}>{STATUS_LABEL[app.status] || app.status}</Badge>
        </div>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-sm sm:grid-cols-3">
          <div><dt className="text-xs text-muted-foreground">이메일</dt><dd className="truncate">{app.contactEmail}</dd></div>
          <div><dt className="text-xs text-muted-foreground">생년월일</dt><dd className="truncate">{app.birthdate}</dd></div>
          <div><dt className="text-xs text-muted-foreground">지역</dt><dd className="truncate">{app.region}</dd></div>
          <div><dt className="text-xs text-muted-foreground">성별</dt><dd className="truncate">{app.gender === 'male' ? '남성' : app.gender === 'female' ? '여성' : '-'}</dd></div>
          <div><dt className="text-xs text-muted-foreground">키</dt><dd className="truncate">{app.heightCm ? `${app.heightCm}cm` : '-'}</dd></div>
          <div><dt className="text-xs text-muted-foreground">전화</dt><dd className="truncate">{app.phone || '-'}</dd></div>
          <div><dt className="text-xs text-muted-foreground">경력</dt><dd className="truncate">{EXPERIENCE_LABEL[app.experienceLevel] || '-'}</dd></div>
        </dl>
        <div className="mb-2.5 mt-2.5 flex flex-wrap gap-1.5">
          {(app.categories || []).map((c) => (
            <Badge key={c} variant="outline">{CATEGORY_LABEL[c] || c}</Badge>
          ))}
        </div>
        {(app.portfolioUrl || app.snsUrl) && (
          <div className="mb-2.5 flex gap-3.5">
            {app.portfolioUrl && <a className="text-sm text-primary underline-offset-2 hover:underline" href={app.portfolioUrl} target="_blank" rel="noreferrer">포트폴리오</a>}
            {app.snsUrl && <a className="text-sm text-primary underline-offset-2 hover:underline" href={app.snsUrl} target="_blank" rel="noreferrer">SNS</a>}
          </div>
        )}
        {app.bio && <p className="mb-2.5 whitespace-pre-wrap text-sm text-muted-foreground">{app.bio}</p>}
        {app.rejectReason && <p className="mb-2 text-sm text-destructive">거절 사유: {app.rejectReason}</p>}
        {app.identityMismatchCount > 0 && (
          <p className="mb-2 text-xs text-muted-foreground">신분증 대조 실패 {app.identityMismatchCount}회</p>
        )}
        {decided && emailFailed && (
          <div className="my-1 flex items-center gap-2.5">
            <Badge variant="destructive">메일 미발송</Badge>
            <Button variant="ghost" size="sm" disabled={busy} onClick={() => onResend(app)}>메일 다시 보내기</Button>
          </div>
        )}

        {pending && !rejecting && (
          <div className="mt-1.5 flex gap-2">
            <Button variant="default" size="sm" disabled={busy} onClick={() => onApprove(app)}>승인</Button>
            <Button variant="outline" size="sm" disabled={busy} onClick={() => setRejecting(true)}>거절</Button>
          </div>
        )}
        {pending && rejecting && (
          <div className="mt-3 flex flex-col gap-2 sm:flex-row">
            <Textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="거절 사유 (지원자에게 메일로 전달돼요)"
              rows={2}
              className="sm:flex-1"
            />
            <div className="flex gap-2">
              <Button
                variant="destructive"
                size="sm"
                disabled={busy || !reason.trim()}
                onClick={() => onReject(app, reason.trim())}
              >
                거절 확정
              </Button>
              <Button variant="ghost" size="sm" disabled={busy} onClick={() => { setRejecting(false); setReason(''); }}>
                취소
              </Button>
            </div>
          </div>
        )}
      </div>
    </Card>
  );
}

export function AdminApplications() {
  const { push } = useToast();
  const [phase, setPhase] = useState('loading'); // loading | ready | error | forbidden
  const [searchParams] = useSearchParams();
  const [filter, setFilter] = useState(searchParams.get('status') || 'under_review');
  const [apps, setApps] = useState([]);
  const [busyId, setBusyId] = useState(null);

  const load = useCallback(async () => {
    setPhase('loading');
    try {
      const rows = await adminListApplications(filter || undefined);
      setApps(rows || []);
      setPhase('ready');
    } catch (e) {
      if (e?.status === 403) { setPhase('forbidden'); return; }
      push?.(e.message, { icon: 'alertCircle' });
      setPhase('error');
    }
  }, [filter, push]);

  useEffect(() => { load(); }, [load]);

  const approve = useCallback(async (app) => {
    setBusyId(app.id);
    try {
      await adminApproveApplication(app.id);
      push?.(`${app.applicantName}님 지원을 승인했어요.`, { icon: 'check' });
      load();
    } catch (e) {
      push?.(e.message, { icon: 'alertCircle' });
      if (e?.status === 409) load(); // 다른 관리자가 이미 처리 — 새로고침
    } finally { setBusyId(null); }
  }, [load, push]);

  const reject = useCallback(async (app, reason) => {
    setBusyId(app.id);
    try {
      await adminRejectApplication(app.id, reason);
      push?.(`${app.applicantName}님 지원을 거절했어요.`, { icon: 'check' });
      load();
    } catch (e) {
      push?.(e.message, { icon: 'alertCircle' });
      if (e?.status === 409) load();
    } finally { setBusyId(null); }
  }, [load, push]);

  const resend = useCallback(async (app) => {
    setBusyId(app.id);
    try {
      await adminResendEmail(app.id);
      push?.('메일을 다시 보냈어요.', { icon: 'check' });
      load();
    } catch (e) { push?.(e.message, { icon: 'alertCircle' }); }
    finally { setBusyId(null); }
  }, [load, push]);

  return (
    <div className="mx-auto max-w-3xl">
      <header className="mb-6">
        <p className="mb-1.5 text-xs uppercase tracking-wide text-muted-foreground">Wearless 관리자</p>
        <h1 className="mb-2 text-2xl font-semibold tracking-tight">모델 지원 검토</h1>
        <p className="text-sm leading-relaxed text-muted-foreground">지원서를 확인하고 승인 또는 거절해요. 승인된 지원자만 신분증 인증 후 모델 등록을 진행할 수 있어요.</p>
      </header>

      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-2">
          {STATUS_FILTERS.map((f) => (
            <Button
              key={f.value}
              variant={filter === f.value ? 'default' : 'outline'}
              size="sm"
              onClick={() => setFilter(f.value)}
            >
              {f.label}
            </Button>
          ))}
        </div>
        <Button variant="ghost" size="sm" onClick={load}>새로고침</Button>
      </div>

      {phase === 'loading' && <p className="py-10 text-center text-sm text-muted-foreground">불러오는 중…</p>}
      {phase === 'forbidden' && (
        <Card>
          <CardHeader>
            <CardTitle>접근 권한이 없어요</CardTitle>
            <CardDescription>관리자 계정으로 로그인해 주세요.</CardDescription>
          </CardHeader>
        </Card>
      )}
      {phase === 'error' && (
        <Card>
          <CardHeader>
            <CardTitle>문제가 발생했어요</CardTitle>
            <CardDescription>지원서를 불러오지 못했어요.</CardDescription>
          </CardHeader>
          <CardContent>
            <Button variant="outline" onClick={load}>다시 시도</Button>
          </CardContent>
        </Card>
      )}
      {phase === 'ready' && apps.length === 0 && (
        <p className="py-10 text-center text-sm text-muted-foreground">{STATUS_LABEL[filter] || '해당'} 상태의 지원서가 없어요.</p>
      )}
      {phase === 'ready' && apps.length > 0 && (
        <ul className="flex list-none flex-col gap-4 p-0">
          {apps.map((app) => (
            <li key={app.id}>
              <ApplicationCard app={app} onApprove={approve} onReject={reject}
                onResend={resend} busy={busyId === app.id} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default AdminApplications;
