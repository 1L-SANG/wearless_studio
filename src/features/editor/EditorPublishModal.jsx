import { Button } from '@/components/ui.jsx';
import { PUBLISH_PRESETS } from './detailPublish.js';
import { RadioCard } from './ExportRadioCard.jsx';
import shared from './EditorDownloadModal.module.css';
import styles from './EditorPublishModal.module.css';

export function EditorPublishModal({ settings, summary, busy, progress, error, result, previousCount,
  realModelBlocked, onChange, onCancel, onPublish, onCopyHtml, onCopyUrls, onDownloadZip }) {
  const done = Math.max(0, Math.min(progress?.done || 0, progress?.total || 0));
  return (
    <div className={`${shared.modal} ${styles.publish}`}>
      <header className={shared.header}>
        <h3 className={shared.title}>붙여넣을 코드 만들기</h3>
      </header>
      {result ? <>
        <p className={styles.resultMessage}>코드를 만들었어요. 쇼핑몰 HTML 입력칸에 붙여 넣어 주세요</p>
        <div className={styles.thumbnails} aria-label="게시한 이미지 조각">
          {result.files.map((file, i) => <img key={file.index} src={file.previewUrl || file.url} alt={`상세 ${i + 1}`} />)}
        </div>
        <div className={`modal-actions ${styles.actions}`}>
          <Button variant="primary" disabled={busy} onClick={onCopyHtml}>HTML 복사</Button>
          <Button variant="ghost" disabled={busy} onClick={onCopyUrls}>이미지 주소 복사</Button>
          <Button variant="ghost" disabled={busy} onClick={onDownloadZip}>ZIP 받기</Button>
        </div>
        {result.showHtml && <label className={styles.copyLabel}>HTML
          <textarea className={styles.copyText} readOnly value={result.html} onFocus={(event) => event.target.select()} />
        </label>}
        <details className={styles.instructions}>
          <summary>몰에 붙여넣는 방법</summary>
          <p>지그재그: 상품 등록의 상세 설명에서 HTML 편집으로 전환해 붙여넣기</p>
          <p>에이블리: 상품 등록의 상세페이지에서 HTML 모드로 붙여넣기</p>
          <p>스마트스토어: 상세설명 SmartEditor ONE의 HTML 모드</p>
        </details>
        {previousCount > 0 && <p className={styles.notice}>이전 버전 {previousCount}개가 남아 있어요. 몰을 새 주소로 바꾼 뒤 보관함에서 지울 수 있어요.</p>}
        <footer className={shared.footer}><div className={`modal-actions ${shared.actions}`}>
          <Button variant="ghost" onClick={onCancel}>닫기</Button>
        </div></footer>
      </> : <>
        <div className={shared.groups}>
          <fieldset className={shared.group}>
            <legend>이미지 크기</legend>
            <div className={`${shared.optionGrid} ${styles.options}`}>
              {PUBLISH_PRESETS.map((preset) => <RadioCard key={preset.id} name="publish-preset"
                option={{ ...preset, title: preset.label }} checked={settings.preset === preset.id}
                disabled={busy || realModelBlocked} onChange={(preset) => onChange({ preset })} />)}
            </div>
            <p className={`${shared.helper} ${styles.helper}`}>지그재그, 에이블리, 스마트스토어 모두 860이면 충분해요.</p>
          </fieldset>
        </div>
        <section className={`${shared.summary} ${styles.summary}`} aria-label="게시 요약" aria-live="polite">
          <h4>게시 요약</h4>
          <dl><div><dt>조각 수</dt><dd>약 {summary.fileCount}개</dd></div>
            <div><dt>예상 용량</dt><dd>약 {(summary.approxBytes / 1048576).toFixed(1)}MB</dd></div></dl>
        </section>
        <p className={styles.notice}>구독 중에는 계속 보관해요. 구독이 없으면 게시일부터 6개월 뒤 지워져요. 언제든 ZIP으로 받을 수 있어요.</p>
        {realModelBlocked && <p className={styles.notice} role="status">실존 모델 컷은 다운로드로 받아 몰에 직접 올려 주세요</p>}
      </>}
      {busy && <div className={shared.processing} role="status" aria-live="polite">
        <strong>{progress?.stage === 'zip' ? 'ZIP으로 묶는 중' : progress?.stage === 'upload' ? '올리는 중' : '이미지 만들기'} {progress?.total > 0 && `${done}/${progress.total}`}</strong>
        <progress className={shared.progress} aria-label="게시 진행" max={progress?.total || undefined} value={progress?.total ? done : undefined} />
      </div>}
      {error && !busy && <div className={shared.error} role="alert"><p>{error.message || error}</p>
        {error.files?.length > 0 && <div className="modal-actions"><Button onClick={onDownloadZip}>ZIP 받기</Button></div>}
      </div>}
      {!result && <footer className={shared.footer}><div className={`modal-actions ${shared.actions}`}>
        <Button variant="ghost" disabled={busy} onClick={onCancel}>취소</Button>
        <Button variant="primary" icon="link" disabled={busy || realModelBlocked} onClick={onPublish}>{error ? '다시 시도' : '코드 만들기'}</Button>
      </div></footer>}
    </div>
  );
}
