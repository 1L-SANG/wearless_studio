/* =============================================================
   관리자 모델 지원서 검토 콘솔 (admin.wearless.kr).

   지원서를 카드로 확인하고 승인/거절한다. 서버가 repo.is_admin 을 강제하므로 비관리자는
   403 을 받는다(이 화면은 그 경우 안내를 보여준다). 거절 시 사유를 함께 보낸다.
   동시 처리 레이스는 서버의 status 가드 UPDATE 가 409 로 막는다 — 목록을 새로고침해 반영.
   설계: docs/designs/facemarket-application-renewal.md
   ============================================================= */
import { useCallback, useEffect, useState } from 'react';
import { Button, Chips, ErrorState, Icon, useToast } from '@/components/ui.jsx';
import {
  adminApproveApplication, adminFetchApplicationPhotoUrl,
  adminListApplications, adminRejectApplication, adminResendEmail,
} from '@/lib/api/facemarket.js';
import s from './AdminApplications.module.css';

const STATUS_FILTERS = [
  { value: 'under_review', label: '검토 중' },
  { value: 'approved', label: '승인' },
  { value: 'rejected', label: '거절' },
  { value: 'cancelled', label: '취소' },
];
const STATUS_LABEL = {
  under_review: '검토 중', approved: '승인됨', rejected: '거절됨', cancelled: '취소됨',
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
    <figure className={s.photoFig}>
      {!hasPhoto && <div className={s.photoEmpty}><Icon name="person" size={22} /></div>}
      {hasPhoto && !url && <div className={s.photoEmpty}>…</div>}
      {hasPhoto && url && <img className={s.photo} src={url} alt={`지원자 ${label} 사진`} />}
      <figcaption className={s.photoCap}>{label}</figcaption>
    </figure>
  );
}

function ApplicantPhotos({ app }) {
  // 지원서는 프로필 1장이 기본. 추가 종류가 저장돼 있으면 그것만 더 보여준다(빈 슬롯 4개 X).
  const present = Array.isArray(app.photoKinds) ? app.photoKinds : (app.hasProfileImage ? ['profile'] : []);
  const slots = PHOTO_KINDS.filter((k) => k.kind === 'profile' || present.includes(k.kind));
  return (
    <div className={s.photos}>
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
    <li className={s.card}>
      <ApplicantPhotos app={app} />
      <div className={s.body}>
        <div className={s.cardHead}>
          <span className={s.name}>{app.applicantName}</span>
          <span className={`${s.badge} ${s[`badge_${app.status}`] || ''}`}>{STATUS_LABEL[app.status] || app.status}</span>
        </div>
        <dl className={s.fields}>
          <div><dt>이메일</dt><dd>{app.contactEmail}</dd></div>
          <div><dt>생년월일</dt><dd>{app.birthdate}</dd></div>
          <div><dt>지역</dt><dd>{app.region}</dd></div>
          <div><dt>성별</dt><dd>{app.gender === 'male' ? '남성' : app.gender === 'female' ? '여성' : '-'}</dd></div>
          <div><dt>키</dt><dd>{app.heightCm ? `${app.heightCm}cm` : '-'}</dd></div>
          <div><dt>에이전시</dt><dd>{app.agencyContracted ? '소속 경험 있음' : '없음'}</dd></div>
          <div><dt>전화</dt><dd>{app.phone || '-'}</dd></div>
          <div><dt>경력</dt><dd>{EXPERIENCE_LABEL[app.experienceLevel] || '-'}</dd></div>
        </dl>
        <div className={s.cats}>
          {(app.categories || []).map((c) => (
            <span key={c} className={s.cat}>{CATEGORY_LABEL[c] || c}</span>
          ))}
        </div>
        {(app.portfolioUrl || app.snsUrl) && (
          <div className={s.links}>
            {app.portfolioUrl && <a href={app.portfolioUrl} target="_blank" rel="noreferrer">포트폴리오</a>}
            {app.snsUrl && <a href={app.snsUrl} target="_blank" rel="noreferrer">SNS</a>}
          </div>
        )}
        {app.bio && <p className={s.bio}>{app.bio}</p>}
        {app.rejectReason && <p className={s.rejectReason}>거절 사유: {app.rejectReason}</p>}
        {app.identityMismatchCount > 0 && (
          <p className={s.mismatch}>신분증 대조 실패 {app.identityMismatchCount}회</p>
        )}
        {decided && emailFailed && (
          <div className={s.emailRow}>
            <span className={s.emailBadge}>메일 미발송</span>
            <Button variant="ghost" size="sm" disabled={busy} onClick={() => onResend(app)}>메일 다시 보내기</Button>
          </div>
        )}

        {pending && !rejecting && (
          <div className={s.actions}>
            <Button variant="primary" size="sm" disabled={busy} onClick={() => onApprove(app)}>승인</Button>
            <Button variant="secondary" size="sm" disabled={busy} onClick={() => setRejecting(true)}>거절</Button>
          </div>
        )}
        {pending && rejecting && (
          <div className={s.rejectBox}>
            <textarea className={s.reasonInput} rows={2} value={reason}
              onChange={(e) => setReason(e.target.value)} placeholder="거절 사유를 입력해 주세요." />
            <div className={s.actions}>
              <Button variant="primary" size="sm" disabled={busy || !reason.trim()}
                onClick={() => onReject(app, reason.trim())}>거절 확정</Button>
              <Button variant="ghost" size="sm" disabled={busy}
                onClick={() => { setRejecting(false); setReason(''); }}>취소</Button>
            </div>
          </div>
        )}
      </div>
    </li>
  );
}

export function AdminApplications() {
  const { push } = useToast();
  const [phase, setPhase] = useState('loading'); // loading | ready | error | forbidden
  const [filter, setFilter] = useState('under_review');
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
    <div className={s.page}>
      <header className={s.head}>
        <p className={s.eyebrow}>Wearless 관리자</p>
        <h1 className={s.title}>모델 지원 검토</h1>
        <p className={s.lead}>지원서를 확인하고 승인 또는 거절해요. 승인된 지원자만 신분증 인증 후 모델 등록을 진행할 수 있어요.</p>
      </header>

      <div className={s.filters}>
        <Chips options={STATUS_FILTERS} value={filter} onChange={(v) => setFilter(v)} allowDeselect={false} />
        <Button variant="ghost" size="sm" icon="refresh" onClick={load}>새로고침</Button>
      </div>

      {phase === 'loading' && <p className={s.state}>불러오는 중…</p>}
      {phase === 'forbidden' && (
        <div className={s.state}><ErrorState title="접근 권한이 없어요" desc="관리자 계정으로 로그인해 주세요." /></div>
      )}
      {phase === 'error' && <div className={s.state}><ErrorState desc="지원서를 불러오지 못했어요." onRetry={load} /></div>}
      {phase === 'ready' && apps.length === 0 && (
        <p className={s.state}>{STATUS_LABEL[filter] || '해당'} 상태의 지원서가 없어요.</p>
      )}
      {phase === 'ready' && apps.length > 0 && (
        <ul className={s.list}>
          {apps.map((app) => (
            <ApplicationCard key={app.id} app={app} onApprove={approve} onReject={reject}
              onResend={resend} busy={busyId === app.id} />
          ))}
        </ul>
      )}
    </div>
  );
}

export default AdminApplications;
