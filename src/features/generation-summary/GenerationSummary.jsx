import { useEffect, useRef } from 'react';
import { Button, Icon, Modal } from '@/components/ui.jsx';
import './GenerationSummary.css';

function EditButton({ field, label, onEdit, busy }) {
  return <button type="button" className="generation-summary-edit" aria-label={`${label} 고치기`}
    disabled={busy} onClick={() => onEdit(field)}>고치기</button>;
}

function Fact({ field, label, value, onEdit, busy }) {
  const longestWord = Math.max(...String(value).split(/\s+/).map((word) => word.length));
  return (
    <div className={`generation-summary-fact${longestWord > 8 ? ' has-long-value' : ''}`}>
      <div className="generation-summary-label"><span>{label}</span><EditButton {...{ field, label, onEdit, busy }} /></div>
      <strong>{value}</strong>
    </div>
  );
}

function SelectionCard({ field, label, value, image, icon, onEdit, busy }) {
  return (
    <div className="generation-summary-selection">
      <div className="generation-summary-label"><span>{label}</span><EditButton {...{ field, label, onEdit, busy }} /></div>
      <div className="generation-summary-selection-body">
        <div className={`generation-summary-thumb ${field}`}>
          {image ? <img src={image} alt="" width="56" height="70" /> : <Icon name={icon} size={24} />}
        </div>
        <strong>{value}</strong>
      </div>
    </div>
  );
}

// 데이터와 이동, 생성은 호출 화면이 소유한다. 다른 승인 흐름에서도 같은 props를 받는다.
export function GenerationSummary({ summary, availableCredits, busy = false, onConfirm, onBack, onEdit }) {
  const dialogRef = useRef(null);
  useEffect(() => {
    const previousFocus = document.activeElement;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    dialogRef.current?.focus();
    return () => {
      document.body.style.overflow = previousOverflow;
      if (previousFocus?.isConnected) previousFocus.focus({ preventScroll: true });
    };
  }, []);
  const trapFocus = (event) => {
    if (event.key !== 'Tab') return;
    const controls = [...dialogRef.current.querySelectorAll('button:not(:disabled)')];
    const first = controls[0], last = controls[controls.length - 1];
    if (!first) { event.preventDefault(); return; }
    if (event.shiftKey && (document.activeElement === first || document.activeElement === dialogRef.current)) {
      event.preventDefault(); last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault(); first.focus();
    }
  };
  const editProps = { onEdit, busy };
  return (
    <Modal wide className="generation-summary-modal" onClose={busy ? undefined : onBack}>
      <div className="generation-summary" ref={dialogRef} role="dialog" aria-modal="true"
        aria-labelledby="generation-summary-title" tabIndex={-1} onKeyDown={trapFocus} aria-busy={busy}>
        <header className="generation-summary-head">
          <span className="generation-summary-eyebrow">상세페이지 생성 전</span>
          <h2 id="generation-summary-title">한 장 요약</h2>
        </header>
        <div className="generation-summary-body">
          <section aria-labelledby="generation-summary-analysis">
            <h3 id="generation-summary-analysis">AI가 이해한 내용</h3>
            <div className="generation-summary-facts">
              <Fact field="category" label="카테고리" value={summary.category} {...editProps} />
              <Fact field="fit" label="핏" value={summary.fit} {...editProps} />
            </div>
            <div className="generation-summary-detail">
              <div className="generation-summary-label"><span>소재</span><EditButton field="materials" label="소재" {...editProps} /></div>
              <div>{summary.materials}</div>
            </div>
            <div className="generation-summary-detail">
              <div className="generation-summary-label"><span>강조 특징</span><EditButton field="features" label="강조 특징" {...editProps} /></div>
              <div className="generation-summary-features">
                {summary.featureItems?.length ? summary.featureItems.map((feature, index) => <span key={index}>{feature}</span>) : summary.features}
              </div>
            </div>
          </section>
          <section className="generation-summary-output" aria-labelledby="generation-summary-output">
            <h3 id="generation-summary-output">만들 것</h3>
            <div className="generation-summary-selections">
              <SelectionCard field="model" label="모델" value={summary.model} image={summary.modelThumb} icon="user" {...editProps} />
              <SelectionCard field="matching" label="매칭 의류" value={summary.matching} image={summary.matchingThumb} icon="shirt" {...editProps} />
            </div>
            <div className="generation-summary-cut-head">
              <span>컷 구성 <strong>{summary.cutCount == null ? '확인 중' : `총 ${summary.cutCount}컷`}</strong></span>
              <EditButton field="cuts" label="컷 구성" {...editProps} />
            </div>
            <div className="generation-summary-cuts" aria-label={summary.cuts}>
              {summary.cutTypes?.map(({ label, count }) => (
                <div className="generation-summary-cut" key={label}>
                  <Icon name={label === '제품컷' || label === '디테일' ? 'shirt' : label === '내 사진' ? 'image' : 'user'} size={18} />
                  <span>{label}</span><strong>{count}<small>컷</small></strong>
                </div>
              ))}
            </div>

          </section>
        </div>
        <footer className="generation-summary-footer">
            <div className="generation-summary-copy">
              <span>카피 <strong><span className={`generation-summary-dot${summary.copywriting === '꺼짐' ? ' is-off' : ''}`} />{summary.copywriting}</strong></span>
              <EditButton field="copywriting" label="카피" {...editProps} />
            </div>
          <div className="generation-summary-cost">
            <div><span>예상 크레딧</span><strong>{summary.credits ?? '확인 중'}<small>{summary.credits == null ? '' : ' 크레딧'}</small></strong></div>
            <div className="generation-summary-balance"><span>지금 잔액</span><strong>{Number.isFinite(availableCredits) ? availableCredits : '확인 중'}</strong></div>
          </div>
          {summary.minimumNote && <p className="generation-summary-note">{summary.minimumNote}</p>}
          {summary.credits == null && <p className="generation-summary-note">돌아가서 컷 수를 다시 확인해 주세요.</p>}
          <div className="modal-actions generation-summary-actions">
            <Button variant="ghost" onClick={onBack} disabled={busy}>돌아가기</Button>
            <Button variant="primary" onClick={onConfirm} disabled={busy || summary.credits == null}>
              {busy ? '준비 중…' : `이대로 만들기 · ${summary.credits ?? '…'} 크레딧`}
            </Button>
          </div>
        </footer>
      </div>
    </Modal>
  );
}
