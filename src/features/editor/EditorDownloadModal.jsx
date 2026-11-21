import { useId, useState } from 'react';
import { Button } from '@/components/ui.jsx';
import {
  CUSTOM_EXPORT_MAX_WIDTH,
  CUSTOM_EXPORT_MIN_WIDTH,
  CUSTOM_EXPORT_WIDTH_ERROR,
  EXPORT_PRESETS,
  EXPORT_WIDTH,
  exportWidth,
  normalizeExportSettings,
  sanitizeExportFileName,
} from '@/features/editor/editorExport.js';
import { colorWithOpacity } from './editorLibrary.js';
import styles from './EditorDownloadModal.module.css';
import { RadioCard } from './ExportRadioCard.jsx';
import { EditorAiDraftConfirmation, useAiDraftConfirmation } from './EditorAiDraftConfirmation.jsx';
import { DownloadChecklist } from './DownloadChecklist.jsx';

const FORMAT_OPTIONS = [
  { id: 'jpeg', title: 'JPG', desc: '품질 (90%), 파일이 가벼움' },
  { id: 'png', title: 'PNG', desc: '원본 화질, 파일이 무거움' },
];

const DELIVERY_OPTIONS = [
  { id: 'split', title: '분할 이미지', desc: '여러 장을 하나씩 ZIP으로 압축해서 다운받기', icon: (
    <svg width="34" height="34" viewBox="0 0 34 34" fill="none" stroke="currentColor" strokeWidth="1.6">
      <rect x="9" y="3" width="16" height="8" rx="1.5" />
      <rect x="9" y="13" width="16" height="8" rx="1.5" />
      <rect x="9" y="23" width="16" height="8" rx="1.5" />
    </svg>
  ) },
  { id: 'single', title: '통 이미지', desc: '전부 들어간 이미지 한 장으로 다운받기', icon: (
    <svg width="34" height="34" viewBox="0 0 34 34" fill="none" stroke="currentColor" strokeWidth="1.6">
      <rect x="9" y="3" width="16" height="28" rx="1.5" />
    </svg>
  ) },
];

const PREVIEW_WIDTH = 200; // Keep in sync with the scale of .previewArtwork.
const PREVIEW_SCALE = PREVIEW_WIDTH / EXPORT_WIDTH;

function DownloadPreview({ blocks, blockMeasurements, summary, renderBlock }) {
  const split = summary?.delivery === 'split';
  const pixelScale = summary ? PREVIEW_WIDTH / summary.width : 0;
  let offset = 0;
  return (
    <aside className={styles.preview} aria-label="미리보기">
      <header className={styles.previewHeader}>
        <h4>미리보기</h4>
        {summary && <p>{split ? `점선에서 약 ${summary.fileCount}장으로 나뉘어요` : '한 장으로 저장돼요'}</p>}
      </header>
      <div className={styles.previewScroll} tabIndex={0} role="region" aria-label="상세페이지 미리보기">
        <div className={styles.previewStack} aria-hidden="true">
          {blocks.map((block, index) => {
            const height = summary ? summary.scaledHeights[index] * pixelScale : (blockMeasurements[index]?.height ?? 0) * PREVIEW_SCALE;
            return (
              <div key={block.id} className={`mini-canvas ${styles.previewBlock}`}>
                <svg width={PREVIEW_WIDTH} height={height}>
                  <rect width="100%" height="100%" fill={colorWithOpacity(block.bg, block.bgOpacity)} />
                </svg>
                <div className={styles.previewArtwork}>{renderBlock?.(block)}</div>
              </div>
            );
          })}
          {split && <svg className={styles.previewCuts} width={PREVIEW_WIDTH} height={summary.height * pixelScale}>
            {summary.pieceHeights.map((height, index) => {
              // 내부 점선은 예상 위치다. 저장할 때 findUniformCutRow가 긴 블록의 자르는 줄을 조금 옮길 수 있다.
              const top = offset * pixelScale;
              offset += height;
              const label = String(index + 1).padStart(2, '0');
              const badgeWidth = 12 + 7 * label.length;
              return (
                <g key={index} transform={`translate(0 ${top})`}>
                  {index > 0 && <line x1="-10" x2={PREVIEW_WIDTH + 10} y1="0" y2="0" />}
                  <rect x="6" y="6" width={badgeWidth} height="18" rx="9" />
                  <text x={6 + badgeWidth / 2} y="19" textAnchor="middle">{label}</text>
                </g>
              );
            })}
          </svg>}
        </div>
      </div>
    </aside>
  );
}

const PROGRESS_TITLES = {
  capture: '페이지를 이미지로 만드는 중…',
  verify: '이미지 확인 중…',
  encode: '파일로 변환 중…',
  zip: 'ZIP으로 묶는 중…',
};

function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0MB';
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
}

export function EditorDownloadModal({
  blocks = [],
  blockMeasurements = [],
  renderPreviewBlock,
  canvasAiJobs,
  settings: rawSettings,
  summary,
  productName = '',
  fileName = productName,
  busy,
  progress,
  error,
  onChange,
  onFileNameChange,
  onCancel,
  onDownload,
  onJumpToBlock,
  gifCount = 0,
  onDownloadGifs,
}) {
  const draft = useAiDraftConfirmation(blocks, { download: onDownload });
  const settings = normalizeExportSettings(rawSettings);
  const [customInput, setCustomInput] = useState(() => String(rawSettings?.customWidth ?? ''));
  const fieldId = useId();
  const custom = settings.preset === 'custom';
  const invalidWidth = exportWidth(settings) === null;
  const original = settings.preset === 'original';
  const savedName = sanitizeExportFileName(fileName);
  const delivery = summary?.delivery ?? settings.delivery;
  const progressInfo = progress && typeof progress === 'object' ? progress : null;
  const blockProgress = progressInfo?.stage === 'capture' && progressInfo.total > 0;
  const completed = blockProgress ? Math.max(0, Math.min(progressInfo.done, progressInfo.total)) : 0;
  const update = (patch) => onChange(normalizeExportSettings({ ...settings, ...patch }));
  const changePreset = (preset) => {
    if (preset !== 'custom') { update({ preset }); return; }
    const input = customInput || String(EXPORT_WIDTH);
    setCustomInput(input);
    update({ preset, customWidth: Number(input) });
  };

  return (
    <div className={`${styles.modal} ${styles.download}`}>
      <DownloadPreview blocks={blocks} blockMeasurements={blockMeasurements} summary={summary} renderBlock={renderPreviewBlock} />
      <div className={styles.settings}>
        <header className={styles.header}>
          <h3 className={styles.title}>상세페이지 다운로드</h3>
          <p className={styles.subtitle}>쇼핑몰에 맞는 크기와 형식을 골라 주세요.</p>
        </header>

        <DownloadChecklist blocks={blocks} canvasAiJobs={canvasAiJobs} busy={busy} onClose={onCancel} onJumpToBlock={onJumpToBlock} />

        <div className={styles.groups}>
          <fieldset className={styles.group}>
            <legend>이미지 크기</legend>
            <div className={styles.optionGrid}>
              {EXPORT_PRESETS.map((preset) => (
                <RadioCard
                  key={preset.id}
                  name="export-preset"
                  option={{ ...preset, title: preset.label,
                    desc: preset.id === 'high' ? '몰에 따라 줄어들 수 있어요' : undefined,
                    tag: preset.id === 'smartstore' ? '(기본)' : undefined,
                    sizeLabel: preset.id === 'custom' ? '640~2,000px' : undefined }}
                  checked={settings.preset === preset.id}
                  disabled={busy}
                  onChange={changePreset}
                />
              ))}
            </div>
            {custom && <div className={styles.customField}>
              <label className={styles.customWidth}>
                <input type="number" inputMode="numeric" min={CUSTOM_EXPORT_MIN_WIDTH} max={CUSTOM_EXPORT_MAX_WIDTH} step="1"
                  aria-label="가로폭" aria-invalid={invalidWidth} aria-describedby={invalidWidth ? `${fieldId}-width-error` : undefined}
                  value={customInput} disabled={busy} onChange={(event) => {
                    setCustomInput(event.target.value);
                    update({ customWidth: event.target.valueAsNumber });
                  }} />
                <span aria-hidden="true">px</span>
              </label>
              {invalidWidth && <p id={`${fieldId}-width-error`} className={styles.validation}>{CUSTOM_EXPORT_WIDTH_ERROR}</p>}
            </div>}
          </fieldset>

          <fieldset className={styles.group}>
            <legend>파일 형식</legend>
            <div className={styles.inlineOptions}>
              {FORMAT_OPTIONS.map((option) => (
                <RadioCard
                  key={option.id}
                  name="export-format"
                  option={option}
                  checked={settings.format === option.id}
                  disabled={busy || (original && option.id === 'jpeg')}
                  onChange={(format) => update({ format })}
                />
              ))}
            </div>
            {original && <p className={styles.helper}>원본 2,000px는 PNG로만 저장해요.</p>}
          </fieldset>

          <fieldset className={styles.group}>
            <legend>받는 방식</legend>
            <div className={styles.inlineOptions}>
              {DELIVERY_OPTIONS.map((option) => (
                <RadioCard
                  key={option.id}
                  name="export-delivery"
                  option={option}
                  checked={settings.delivery === option.id}
                  disabled={busy}
                  onChange={(delivery) => update({ delivery })}
                />
              ))}
            </div>
          </fieldset>

          <div className={styles.fileField}>
            <label htmlFor={`${fieldId}-name`}>파일명</label>
            <input id={`${fieldId}-name`} type="text" autoComplete="off" value={fileName} disabled={busy}
              onChange={(event) => onFileNameChange?.(event.target.value)} />
            <div aria-live="polite">
              {summary && <p className={styles.helper}>
                약 {summary.fileCount.toLocaleString('ko-KR')}개 파일, 약 {formatBytes(summary.approxBytes)}, 총 {summary.height.toLocaleString('ko-KR')}px
              </p>}
              {summary?.fallbackReason && <p className={styles.helper}>{summary.fallbackReason}</p>}
              {savedName !== fileName && <p className={styles.savedName} title={savedName}>저장할 이름: {savedName}</p>}
            </div>
          </div>
        </div>

        {gifCount > 0 && <div className={styles.guidance}>
          <p>{delivery === 'split'
            ? `움직이는 GIF ${gifCount}개는 별도 파일로 같이 담아요`
            : `상세페이지에는 첫 컷이 들어가요 · GIF ${gifCount}개는 따로 받아 주세요`}</p>
          {delivery === 'single' && <div className="modal-actions">
            <Button variant="ghost" onClick={onDownloadGifs} disabled={busy}>GIF 따로 받기</Button>
          </div>}
        </div>}

        <footer className={styles.footer}>
          {busy && (
            <div className={styles.processing} role="status" aria-live="polite">
              <div className={styles.progressLabel}>
                <strong>{PROGRESS_TITLES[progressInfo?.stage] || '다운로드 준비 중…'}</strong>
                {blockProgress && <span>{completed} / {progressInfo.total}개 블록</span>}
                {typeof progress === 'string' && <span>{progress}</span>}
              </div>
              <progress
                className={styles.progress}
                aria-label={blockProgress ? '이미지 만들기 진행' : '다운로드 처리 중'}
                max={blockProgress ? progressInfo.total : undefined}
                value={blockProgress ? completed : undefined}
              />
              <p>페이지가 길면 시간이 걸릴 수 있어요. 이 창을 열어 두세요.</p>
            </div>
          )}
          {error && !busy && (
            <div className={styles.error} role="alert">
              <strong>다운로드를 완료하지 못했어요</strong>
              <p>{error}</p>
              <p>설정을 바꾸거나 다시 시도해 주세요.</p>
            </div>
          )}
          {draft.confirming && !invalidWidth ? <EditorAiDraftConfirmation sectionNames={draft.sectionNames}
            onCancel={onCancel} onConfirm={draft.confirm} busy={busy} /> : <div className={`modal-actions ${styles.actions}`}>
            {invalidWidth && <span className={styles.widthReminder}>가로폭을 확인해 주세요</span>}
            <Button variant="ghost" className={styles.cancel} onClick={onCancel} disabled={busy}>취소</Button>
            <Button variant="primary" icon={busy ? undefined : error ? 'refresh' : 'download'} onClick={() => draft.request('download')} disabled={busy || invalidWidth}>
              {busy ? '만드는 중…' : error ? '다시 시도' : '다운로드'}
            </Button>
          </div>}
        </footer>
      </div>
    </div>
  );
}
