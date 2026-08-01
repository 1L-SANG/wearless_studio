/* =============================================================
   features/editor/VaryReviewModal.jsx — AI 편집 결과 검수 (Phase 3 P0-C)

   서버가 `review_required` 로 판정한 결과는 사람이 원본과 나란히 보기 전에는
   캔버스에 들어가지 않는다. 자동으로 통과시킬 수 없다는 판정을 UI 가 조용히
   덮어쓰면 판정 자체가 의미를 잃는다.

   여기서 내리는 판단은 machine QC 를 **바꾸지 않는다**. 사용자의 결정은 별도
   이력으로 쌓이고, qcStatus 는 서버가 본 그대로 남는다.
   ============================================================= */
import { Button, Icon, Modal } from '@/components/ui.jsx';
import { thumbUrl } from '@/lib/imageCdn.js';

// 서버가 내려주는 안전 요약만 표시한다 — Vision 원문·프롬프트·지표는 애초에 오지 않는다.
const OBS_LABEL = {
  cameraChanged: '카메라 각도', framingChanged: '구도', poseChanged: '포즈',
  backgroundChanged: '배경', lightingChanged: '조명', collarChanged: '칼라',
  sleevesChanged: '소매', buttonsChanged: '단추', pocketsChanged: '주머니',
  patternChanged: '패턴', logoChanged: '로고', mannequinIdentityChanged: '마네킹',
  centerX: '가로 위치', centerY: '세로 위치', subjectHeight: '피사체 크기',
  hemY: '밑단 위치', cuffY: '소매 끝', bodyWidth: '몸통 폭',
  shoulderWidth: '어깨 폭', backgroundDeltaE: '배경 색',
};
const label = (k) => OBS_LABEL[k] || k;

function Findings({ summary }) {
  if (!summary) return <p className="hint">판정 근거를 불러오지 못했어요. 직접 비교해 보고 결정해 주세요.</p>;
  const unexpected = summary.unexpectedChanges || [];
  const violations = summary.lockedInvariantViolations || [];
  return (
    <div style={{ display: 'grid', gap: 10 }}>
      <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
        <Icon name={summary.requestedChangeSatisfied ? 'check' : 'alertTri'} size={14}
          style={{ color: summary.requestedChangeSatisfied ? '#16a34a' : '#d97706' }} />
        <span style={{ fontSize: 13 }}>
          {summary.requestedChangeSatisfied ? '요청한 변경은 반영됐어요' : '요청한 변경이 반영됐는지 확실하지 않아요'}
        </span>
      </div>
      {violations.length > 0 && (
        <div style={{ fontSize: 13 }}>
          <b style={{ color: '#dc2626' }}>바뀌면 안 되는 부분이 달라졌어요</b>
          <div className="hint" style={{ marginTop: 4 }}>{violations.map(label).join(', ')}</div>
        </div>
      )}
      {unexpected.length > 0 && (
        <div style={{ fontSize: 13 }}>
          <b>요청하지 않았는데 달라진 부분</b>
          <div className="hint" style={{ marginTop: 4 }}>{unexpected.map(label).join(', ')}</div>
        </div>
      )}
      {violations.length === 0 && unexpected.length === 0 && (
        <p className="hint">눈에 띄는 차이는 찾지 못했지만 자동으로 통과시킬 만큼 확실하지는 않았어요.</p>
      )}
      {summary.visionStatus && summary.visionStatus !== 'ok' && (
        <p className="hint">이미지 분석이 완료되지 않아 판정 근거가 부족해요.</p>
      )}
    </div>
  );
}

export function VaryReviewModal({ image, busy, onAccept, onReject, onClose }) {
  const src = image?.src;
  const before = image?.sourceSrc;
  return (
    <Modal onClose={busy ? () => {} : onClose} wide>
      <h3 style={{ margin: '0 0 6px' }}>편집 결과를 확인해 주세요</h3>
      <p className="hint" style={{ margin: '0 0 16px' }}>
        요청한 편집 외에 달라진 부분이 있을 수 있어요. 원본과 비교한 뒤 사용할지 정해 주세요.
      </p>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 16 }}>
        <figure style={{ margin: 0 }}>
          <figcaption className="lbl" style={{ marginBottom: 6 }}>원본</figcaption>
          {before
            ? <img src={thumbUrl(before, 520)} alt="원본" style={{ width: '100%', borderRadius: 10, background: '#f4f4f5' }} />
            : <div className="hint" style={{ padding: 24, textAlign: 'center', background: '#f4f4f5', borderRadius: 10 }}>원본을 불러올 수 없어요</div>}
        </figure>
        <figure style={{ margin: 0 }}>
          <figcaption className="lbl" style={{ marginBottom: 6 }}>편집 결과</figcaption>
          <img src={thumbUrl(src, 520)} alt="편집 결과" style={{ width: '100%', borderRadius: 10, background: '#f4f4f5' }} />
        </figure>
      </div>
      <Findings summary={image?.qcSummary} />
      <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 20 }}>
        <Button variant="ghost" onClick={onClose} disabled={busy}>닫기</Button>
        <Button variant="ghost" onClick={onReject} disabled={busy}>사용하지 않음</Button>
        <Button onClick={onAccept} disabled={busy}>확인 후 사용</Button>
      </div>
    </Modal>
  );
}
