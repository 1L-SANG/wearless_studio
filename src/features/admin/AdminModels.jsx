import { useCallback, useEffect, useState } from 'react';
import { Button, ErrorState, Icon, useToast } from '@/components/ui.jsx';
import {
  adminDeleteModelTestCut,
  adminFetchModelTestCutUrl,
  adminListModels,
  adminSendModelTestCuts,
  adminUploadModelTestCuts,
} from '@/lib/api/facemarket.js';
import s from './AdminModels.module.css';

const STATUS_LABEL = {
  pending: '대기 중',
  awaiting_confirm: '확인 대기',
  verified: '공개 중',
  suspended: '중지',
  reverification_required: '재확인 필요',
};

const ENROLLMENT_LABEL = {
  photos_pending: '사진 등록 중',
  liveness_pending: '라이브니스 대기',
  processing: '검수 중',
  asset_building: '자산 생성 중',
  license_pending: '조건 입력 대기',
  vc_pending: '증서 발급 중',
  passed: '등록 완료',
  failed: '등록 실패',
  cancelled: '등록 취소',
  expired: '등록 만료',
};

const formatSentAt = (value) => {
  if (!value) return '';
  return new Intl.DateTimeFormat('ko-KR', {
    dateStyle: 'medium', timeStyle: 'short', timeZone: 'Asia/Seoul',
  }).format(new Date(value));
};

function AdminCutImage({ cut, modelName }) {
  const [url, setUrl] = useState(null);

  useEffect(() => {
    let alive = true;
    let objectUrl = null;
    adminFetchModelTestCutUrl(cut.imageUri)
      .then((nextUrl) => {
        if (!alive) { URL.revokeObjectURL(nextUrl); return; }
        objectUrl = nextUrl;
        setUrl(nextUrl);
      })
      .catch(() => {});
    return () => {
      alive = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [cut.imageUri]);

  if (!url) return <div className={s.cutLoading} aria-label="테스트컷 불러오는 중">…</div>;
  return (
    <img
      className={s.cutImage}
      src={url}
      alt={`${modelName} 테스트컷 ${cut.sort + 1}`}
      width="220"
      height="280"
      loading="lazy"
    />
  );
}

function ModelCard({ model, expanded, busy, onToggle, onUpload, onDelete, onSend }) {
  const status = model.redoRequested ? 'redo' : model.status;
  const statusLabel = model.redoRequested ? '재생성 요청' : (STATUS_LABEL[model.status] || model.status);
  const noCuts = model.testCutCount === 0;
  const notReady = !model.readyToSend;
  const inputId = `model-cuts-${model.id}`;

  return (
    <li className={s.card}>
      <button className={s.summary} type="button" onClick={onToggle} aria-expanded={expanded}>
        <span className={s.modelCopy}>
          <strong>{model.displayName}</strong>
          <span>{ENROLLMENT_LABEL[model.enrollmentStatus] || model.enrollmentStatus || '등록 정보 없음'}</span>
        </span>
        <span className={`${s.badge} ${s[`badge_${status}`] || ''}`}>{statusLabel}</span>
        <span className={s.count}>테스트컷 {model.testCutCount}장</span>
        <Icon name={expanded ? 'chevUp' : 'chevDown'} size={18} />
      </button>

      {expanded && (
        <div className={s.detail}>
          {model.testCuts.length > 0 ? (
            <div className={s.cutGrid}>
              {model.testCuts.map((cut) => (
                <figure className={s.cut} key={cut.id}>
                  <AdminCutImage cut={cut} modelName={model.displayName} />
                  <figcaption>
                    <span>컷 {cut.sort + 1}{cut.approved ? ' · 승인됨' : ''}</span>
                    <button
                      className={s.deleteButton}
                      type="button"
                      aria-label={`테스트컷 ${cut.sort + 1} 삭제`}
                      disabled={busy || cut.approved}
                      title={cut.approved ? '모델이 승인한 원본은 삭제할 수 없어요.' : '삭제'}
                      onClick={() => onDelete(cut)}
                    >
                      <Icon name="trash" size={15} />
                    </button>
                  </figcaption>
                </figure>
              ))}
            </div>
          ) : (
            <p className={s.empty}>테스트컷이 아직 없어요. 이미지를 올린 뒤 모델에게 보내 주세요.</p>
          )}

          <div className={s.actions}>
            <label className={`${s.uploadLabel}${busy || model.testCutCount >= 6 ? ` ${s.disabled}` : ''}`} htmlFor={inputId}>
              <Icon name="imagePlus" size={16} />
              이미지 추가
            </label>
            <input
              className={s.fileInput}
              id={inputId}
              type="file"
              accept="image/png,image/jpeg,image/webp"
              multiple
              disabled={busy || model.testCutCount >= 6}
              onChange={(event) => {
                onUpload(Array.from(event.target.files || []));
                event.target.value = '';
              }}
            />
            <span title={noCuts
              ? '테스트컷을 1장 이상 올려야 보낼 수 있어요.'
              : notReady ? '등록과 VC 발급이 끝난 뒤 보낼 수 있어요.' : undefined}>
              <Button variant="primary" size="sm" disabled={busy || noCuts || notReady} onClick={onSend}>
                {busy ? '처리 중…' : '모델에게 보내기'}
              </Button>
            </span>
          </div>

          {model.status === 'awaiting_confirm' && model.confirmRequestedAt && (
            <p className={s.sent}>확인 대기 중 · {formatSentAt(model.confirmRequestedAt)}</p>
          )}
        </div>
      )}
    </li>
  );
}

export function AdminModels() {
  const { push } = useToast();
  const [phase, setPhase] = useState('loading');
  const [models, setModels] = useState([]);
  const [expandedId, setExpandedId] = useState(null);
  const [busyId, setBusyId] = useState(null);

  const load = useCallback(async () => {
    setPhase('loading');
    try {
      const rows = await adminListModels();
      setModels(rows || []);
      setPhase('ready');
    } catch (error) {
      if (error?.status === 403) { setPhase('forbidden'); return; }
      push?.(error.message, { icon: 'alertCircle' });
      setPhase('error');
    }
  }, [push]);

  useEffect(() => { load(); }, [load]);

  const upload = useCallback(async (model, files) => {
    if (!files.length) return;
    if (model.testCutCount + files.length > 6) {
      push?.(`이 모델은 이미 ${model.testCutCount}장이에요. 모두 합쳐 6장까지 올릴 수 있어요.`, { icon: 'alertCircle' });
      return;
    }
    setBusyId(model.id);
    try {
      await adminUploadModelTestCuts(model.id, files);
      push?.(`테스트컷 ${files.length}장을 올렸어요.`, { icon: 'check' });
      await load();
      setExpandedId(model.id);
    } catch (error) { push?.(error.message, { icon: 'alertCircle' }); }
    finally { setBusyId(null); }
  }, [load, push]);

  const remove = useCallback(async (model, cut) => {
    if (!window.confirm(`테스트컷 ${cut.sort + 1}을 삭제할까요?`)) return;
    setBusyId(model.id);
    try {
      await adminDeleteModelTestCut(model.id, cut.id);
      push?.('테스트컷을 삭제했어요.', { icon: 'check' });
      await load();
      setExpandedId(model.id);
    } catch (error) { push?.(error.message, { icon: 'alertCircle' }); }
    finally { setBusyId(null); }
  }, [load, push]);

  const send = useCallback(async (model) => {
    setBusyId(model.id);
    try {
      const result = await adminSendModelTestCuts(model.id);
      push?.(
        result.emailSent ? '모델에게 테스트컷 확인 메일을 보냈어요.' : '확인 대기 상태로 바꿨어요. 메일은 발송되지 않았어요.',
        { icon: result.emailSent ? 'check' : 'info' },
      );
      await load();
      setExpandedId(model.id);
    } catch (error) { push?.(error.message, { icon: 'alertCircle' }); }
    finally { setBusyId(null); }
  }, [load, push]);

  return (
    <div className={s.page}>
      <header className={s.head}>
        <p className={s.eyebrow}>Wearless 관리자</p>
        <h1 className={s.title}>모델 테스트컷</h1>
        <p className={s.lead}>등록이 끝난 모델에게 테스트컷을 보내고 공개 전 확인 상태를 관리해요.</p>
      </header>

      <div className={s.toolbar}>
        <span>모델 {models.length}명</span>
        <Button variant="ghost" size="sm" icon="refresh" onClick={load}>새로고침</Button>
      </div>

      {phase === 'loading' && <p className={s.state}>불러오는 중…</p>}
      {phase === 'forbidden' && (
        <div className={s.state}><ErrorState title="접근 권한이 없어요" desc="관리자 계정으로 로그인해 주세요." /></div>
      )}
      {phase === 'error' && <div className={s.state}><ErrorState desc="모델을 불러오지 못했어요." onRetry={load} /></div>}
      {phase === 'ready' && models.length === 0 && <p className={s.state}>등록된 모델이 없어요.</p>}
      {phase === 'ready' && models.length > 0 && (
        <ul className={s.list}>
          {models.map((model) => (
            <ModelCard
              key={model.id}
              model={model}
              expanded={expandedId === model.id}
              busy={busyId === model.id}
              onToggle={() => setExpandedId((current) => current === model.id ? null : model.id)}
              onUpload={(files) => upload(model, files)}
              onDelete={(cut) => remove(model, cut)}
              onSend={() => send(model)}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

export default AdminModels;
