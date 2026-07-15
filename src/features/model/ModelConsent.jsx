/* =============================================================
   features/model — ① 개인화 동의 (/model/consent)
   서비스이용·국외이전(필수, 개별) · 학습활용(선택) 동의를 사전체크 없이
   받는다(api-spec §3.1). 필수 2항목 제출 시 프로필이 none → draft 로 전이.
   ============================================================= */
import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button, ErrorState, Icon, useToast } from '@/components/ui.jsx';
import { getConsents, submitConsents } from '@/lib/api/personalization.js';
import s from './ModelPersonalization.module.css';

// 현재 사용자가 보고 있는 동의 문서 버전 — 서버 현행 버전과 대조된다(불일치 시 400 stale_consent_doc).
// 법무 확정 전 잠정값(ux-flow §3.4 예시와 동일 소스).
const DOC_VERSION = '2026-10-v1';

const ITEMS = [
  {
    type: 'service_use', required: true,
    title: '얼굴·신체 정보 수집·이용에 동의해요',
    desc: '내 모델 생성 목적으로만 사용되고, 설정에서 언제든 삭제할 수 있어요.',
  },
  {
    type: 'cross_border_transfer', required: true,
    title: '국외 이전에 동의해요',
    desc: '얼굴 이미지는 생성 처리를 위해 해외 서버(미국)로 전송돼요. 동의하지 않으면 업로드를 진행할 수 없어요.',
  },
  {
    type: 'training_use', required: false,
    title: 'AI 학습 활용에 동의해요 (선택)',
    desc: '동의하지 않아도 개인화 생성 기능은 그대로 사용할 수 있어요.',
  },
];

export function ModelConsent() {
  const navigate = useNavigate();
  const { push } = useToast();
  const [phase, setPhase] = useState('loading'); // loading|ready|error
  const [granted, setGranted] = useState({});     // type -> 'granted'|'withdrawn'|'none'
  const [checked, setChecked] = useState({});      // type -> bool (로컬 편집값)
  const [submitting, setSubmitting] = useState(false);
  const [minorBlocked, setMinorBlocked] = useState(false);
  const [notice, setNotice] = useState(null);       // { retentionDays, noticeUris }

  const load = useCallback(async () => {
    setPhase('loading');
    try {
      const r = await getConsents();
      const g = {};
      const c = {};
      (r.consents || []).forEach((it) => { g[it.type] = it.status; c[it.type] = it.status === 'granted'; });
      setGranted(g);
      setChecked(c);
      setNotice({ retentionDays: r.retentionDays, noticeUris: r.noticeUris || {} });
      setPhase('ready');
    } catch (e) {
      push?.(e.message, { icon: 'alertCircle' });
      setPhase('error');
    }
  }, [push]);

  useEffect(() => { load(); }, [load]);

  const toggle = (type) => {
    if (granted[type] === 'granted') return; // 이미 동의완료 — 철회는 별도 화면(설정 > 내 모델 관리)
    setChecked((c) => ({ ...c, [type]: !c[type] }));
  };

  const requiredOk = ITEMS.filter((it) => it.required).every((it) => checked[it.type]);

  const onSubmit = async () => {
    if (!requiredOk) return;
    setSubmitting(true);
    setMinorBlocked(false);
    try {
      // 이미 granted 인 항목도 재제출은 멱등(no-op) — 신규로 체크된 항목만 골라 보낼 필요 없이
      // 현재 체크된 전부를 보낸다(서버가 중복 granted 를 안전하게 무시).
      const items = ITEMS.filter((it) => checked[it.type]).map((it) => ({ type: it.type, docVersion: DOC_VERSION }));
      await submitConsents(items);
      push?.('동의가 완료됐어요.', { icon: 'check' });
      navigate('/model/face');
    } catch (e) {
      if (e?.code === 'minor_blocked') { setMinorBlocked(true); return; }
      push?.(e.message || '동의 제출에 실패했어요.', { icon: 'alertCircle' });
    } finally {
      setSubmitting(false);
    }
  };

  if (phase === 'loading') return <div className="wizard narrow"><div className="surface">불러오는 중…</div></div>;
  if (phase === 'error') return <div className="wizard narrow"><div className="surface"><ErrorState desc="동의 상태를 불러오지 못했어요." onRetry={load} /></div></div>;

  return (
    <div className="wizard narrow">
      <div className="page-head">
        <h1>얼굴로 내 모델을 만들기 전에 확인해주세요</h1>
        <p>얼굴은 개인정보보호법상 민감정보라, 명확한 동의를 먼저 받아요.</p>
      </div>

      {minorBlocked && (
        <div className={`${s.banner} ${s.bannerWarn}`}>
          <Icon name="alertTri" size={16} />
          <span>만 14세 미만 등 미성년자는 이 기능을 이용할 수 없어요.</span>
        </div>
      )}

      <div className="surface">
        {ITEMS.map((it) => {
          const on = !!checked[it.type];
          const done = granted[it.type] === 'granted';
          return (
            <div className={s.consentItem} key={it.type}>
              <span
                className={`${s.consentCheck}${on ? ' ' + s.on : ''}${done ? ' ' + s.disabled : ''}`}
                role="checkbox" aria-checked={on} tabIndex={0}
                onClick={() => toggle(it.type)}
                onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(it.type); } }}
              >
                <Icon name="check" size={12} />
              </span>
              <div className={s.consentBody} onClick={() => toggle(it.type)}>
                <div className={s.consentTitleRow}>
                  <span className={s.consentTitle}>{it.title}</span>
                  <span className={`${s.consentTag} ${done ? s.tagDone : it.required ? s.tagRequired : s.tagOptional}`}>
                    {done ? '동의완료' : it.required ? '필수' : '선택'}
                  </span>
                </div>
                <div className={s.consentDesc}>{it.desc}</div>
              </div>
            </div>
          );
        })}

        {notice && (
          <div className={s.noticeLinks}>
            {notice.retentionDays != null && <span className="hint">보관기간 {notice.retentionDays}일</span>}
            {notice.noticeUris.retention && <a href={notice.noticeUris.retention} target="_blank" rel="noreferrer">보관기간 안내</a>}
            {notice.noticeUris.thirdParty && <a href={notice.noticeUris.thirdParty} target="_blank" rel="noreferrer">제3자 제공 안내</a>}
            {notice.noticeUris.crossBorder && <a href={notice.noticeUris.crossBorder} target="_blank" rel="noreferrer">국외이전 고지</a>}
          </div>
        )}

        <Button variant="primary" block onClick={onSubmit} disabled={submitting || !requiredOk} iconRight="arrowRight" style={{ marginTop: 20 }}>
          {submitting ? '제출 중…' : '동의하고 시작하기'}
        </Button>

        <p className="hint" style={{ marginTop: 12 }}>만 14세 미만 등 미성년자는 이용할 수 없어요.</p>
      </div>
    </div>
  );
}

export default ModelConsent;
