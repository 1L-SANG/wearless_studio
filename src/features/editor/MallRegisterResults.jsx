import { MALL_LABELS, registrationActive } from './mallRegisterDraft.js';
import styles from './MallRegisterModal.module.css';

const STAGES = { preparing: '등록 준비', creating: '상품 생성', mapping_variants: '옵션 확인', setting_inventory: '재고 설정', uploading_thumbnail: '대표 이미지', uploading_additional_images: '추가 이미지', activating: '진열과 판매 설정' };
export function registrationResultLabel(view) {
  if (registrationActive(view)) return '등록 중';
  if (['unknown', 'duplicate_suspected'].includes(view?.outcome)) return '등록 여부 확인 필요';
  if (view?.outcome === 'created') return view.setupStatus === 'complete' ? '완료' : '설정 확인 필요';
  return '못 올림';
}
export function MallRegisterResults({ batch, onVerify, onResume, onRetryFailed }) {
  return <section aria-label="몰마다 결과" aria-live="polite" aria-atomic="false" className={styles.results}>
    {batch.receiptUnconfirmed && <p>접수 응답을 받지 못했어요. 같은 요청으로 접수를 다시 확인해 주세요.</p>}
    {batch.timedOut && <p role="status">아직 처리 중일 수 있어요. 창을 다시 열면 서버에서 이어 확인해요.</p>}
    {batch.items.map((item) => {
      const view = item.registration, active = registrationActive(view);
      const uncertainActivation = view?.stage === 'activating' && view?.stages?.activating?.status !== 'complete' && view?.setupStatus !== 'complete';
      return <article key={`${item.mall}:${item.jobId || 'blocked'}`} className={styles.resultRow}>
        <img src={`/assets/malls/${item.mall}-48.png`} alt="" className={styles.logo} />
        <div><h3>{MALL_LABELS[item.mall]}</h3><p>{view ? registrationResultLabel(view) : item.status === 'accepted' ? '접수했어요' : '이번에는 등록하지 않았어요'}</p>
          {view?.productId && <p>상품 번호 {view.productId}</p>}
          {active && <p>{STAGES[view.stage] || '등록 준비'} · {view.progress || 0}%</p>}
          {!active && view?.outcome === 'created' && view.setupStatus === 'needs_attention' && <p>{uncertainActivation
            ? '상품은 만들어졌어요. 판매 상태를 확인해 주세요'
            : view.stage === 'setting_inventory' && !view.stages?.activating?.sentAt ? '상품은 만들어졌어요. 재고 설정을 마치지 못해 진열과 판매를 꺼 두었어요'
              : '상품은 만들어졌어요. 설정과 판매 상태를 확인해 주세요'}</p>}
          {!active && view?.outcome === 'created' && view.setupStatus === 'complete' && <p>{view.stages?.activating?.display === 'T' ? '진열 켬' : view.stages?.activating?.display === 'F' ? '진열 안 함' : '진열 상태는 관리자에서 확인해 주세요'} · {view.stages?.activating?.selling === 'T' ? '판매 켬' : view.stages?.activating?.selling === 'F' ? '판매 안 함' : '판매 상태 확인 필요'}</p>}
          {!active && view?.stage && view.setupStatus !== 'complete' && <p>{STAGES[view.stage] || '등록 처리'} 단계 확인 필요</p>}
          {item.issues?.map((issue, index) => <p key={index}>{issue.message}</p>)}
          {view?.warnings?.map((warning, index) => <p key={index}>{typeof warning === 'string' ? warning : warning.message}</p>)}
          {view?.retention && <p className={styles.notice}>{view.retention.message || (view.retention.expiresAt ? `${new Date(view.retention.expiresAt).toLocaleDateString('ko-KR')}까지 상세 이미지를 보관해요` : '상세 이미지 보관 안내를 확인해 주세요')}</p>}
        </div>
        <div className={`modal-actions ${styles.resultActions}`}>
          {view?.adminUrl && <a className={styles.goLink} href={view.adminUrl} target="_blank" rel="noopener noreferrer">{item.mall === 'zigzag' ? '파트너센터에서 보기' : '관리자에서 보기'}</a>}
          {view?.productUrl && <a className={styles.goLink} href={view.productUrl} target="_blank" rel="noopener noreferrer">상품 보기</a>}
          {!active && ['unknown', 'duplicate_suspected'].includes(view?.outcome) && view.retryAction === 'verify' && <button type="button" className="btn btn-ghost" onClick={() => onVerify(view.jobId)}>등록 여부 확인</button>}
          {!active && view?.outcome === 'created' && view.retryAction === 'resume' && <button type="button" className="btn btn-ghost" onClick={() => onResume(view.jobId)}>설정 이어 하기</button>}
          {((item.status === 'blocked' && !item.existingJobId && !item.issues?.some((issue) => issue.code === 'registration_preparing')) || view?.outcome === 'failed' && view.retryAction === 'new_request') && <button type="button" className="btn btn-ghost" onClick={() => onRetryFailed(item.mall)}>{MALL_LABELS[item.mall]} 다시 시도</button>}
        </div>
      </article>;
    })}
  </section>;
}
