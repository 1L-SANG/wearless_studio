import { Button } from '@/components/ui.jsx';
import {
  EXPORT_PRESETS,
  normalizeExportSettings,
} from '@/features/editor/editorExport.js';
import styles from './EditorDownloadModal.module.css';

const FORMAT_OPTIONS = [
  { id: 'jpeg', title: 'JPG', desc: '가볍게, 품질 90' },
  { id: 'png', title: 'PNG', desc: '고화질, 파일이 큼' },
];

const DELIVERY_OPTIONS = [
  { id: 'split', title: '나눠서 받기', desc: '블록 경계를 살려 3,000px 이하 조각을 ZIP으로 저장' },
  { id: 'single', title: '한 장으로 받기', desc: '선택한 폭으로 전체 상세페이지를 한 장에 저장' },
];

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

function RadioCard({ name, option, checked, disabled, onChange }) {
  return (
    <label className={`${styles.option}${checked ? ` ${styles.selected}` : ''}${disabled ? ` ${styles.disabled}` : ''}`}>
      <input
        type="radio"
        name={name}
        value={option.id}
        checked={checked}
        disabled={disabled}
        onChange={() => onChange(option.id)}
      />
      <span className={styles.optionText}>
        <span className={styles.optionTitle}>{option.title}</span>
        {option.desc && <span className={styles.optionDesc}>{option.desc}</span>}
      </span>
    </label>
  );
}

export function EditorDownloadModal({
  settings: rawSettings,
  summary,
  notice,
  busy,
  progress,
  error,
  onChange,
  onCancel,
  onDownload,
}) {
  const settings = normalizeExportSettings(rawSettings);
  const original = settings.preset === 'original';
  const progressInfo = progress && typeof progress === 'object' ? progress : null;
  const blockProgress = progressInfo?.stage === 'capture' && progressInfo.total > 0;
  const completed = blockProgress ? Math.max(0, Math.min(progressInfo.done, progressInfo.total)) : 0;
  const update = (patch) => onChange(normalizeExportSettings({ ...settings, ...patch }));
  const deliveryOptions = original
    ? DELIVERY_OPTIONS.map((option) => option.id === 'split'
      ? { ...option, desc: '폭 2,000px PNG로 저장하고, 긴 블록은 안전하게 나눠요' }
      : option)
    : DELIVERY_OPTIONS;

  return (
    <div className={styles.modal}>
      <header className={styles.header}>
        <span className={styles.eyebrow}>다운로드</span>
        <h3 className={styles.title}>상세페이지 다운로드</h3>
        <p className={styles.subtitle}>쇼핑몰에 맞는 크기와 형식을 선택해 주세요.</p>
      </header>

      <div className={styles.groups}>
        <fieldset className={styles.group}>
          <legend>이미지 크기</legend>
          <div className={styles.optionGrid}>
            {EXPORT_PRESETS.map((preset) => (
              <RadioCard
                key={preset.id}
                name="export-preset"
                option={{ ...preset, title: preset.label }}
                checked={settings.preset === preset.id}
                disabled={busy}
                onChange={(presetId) => update({ preset: presetId })}
              />
            ))}
          </div>
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
                disabled={busy || original}
                onChange={(format) => update({ format })}
              />
            ))}
          </div>
          {original && <p className={styles.helper}>원본 2,000px는 PNG로만 저장해요.</p>}
        </fieldset>

        <fieldset className={styles.group}>
          <legend>받는 방식</legend>
          <div className={styles.inlineOptions}>
            {deliveryOptions.map((option) => (
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
      </div>

      {notice && <p className={styles.notice} role="status">{notice}</p>}

      <section className={styles.summary} aria-live="polite" aria-label="다운로드 요약">
        <h4>다운로드 요약</h4>
        <dl>
          <div>
            <dt>최종 크기</dt>
            <dd>
              {summary.width.toLocaleString('ko-KR')} × 총 {summary.height.toLocaleString('ko-KR')}px
              {summary.maxPieceHeight && ` · 각 최대 ${summary.maxPieceHeight.toLocaleString('ko-KR')}px`}
            </dd>
          </div>
          <div>
            <dt>파일 수</dt>
            <dd>{summary.approximate && settings.delivery === 'split' ? '약 ' : ''}{summary.fileCount}개</dd>
          </div>
          <div>
            <dt>예상 용량</dt>
            <dd>약 {formatBytes(summary.approxBytes)}</dd>
          </div>
        </dl>
      </section>

      <div className={styles.guidance}>
        <p>여러 장을 올린 뒤 쇼핑몰 화면에서 순서를 한 번 확인해 주세요.</p>
        <p>카페24에서는 한글 파일명이 안 보일 수 있어 ZIP 안 파일명은 영문과 숫자로 저장해요.</p>
      </div>

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
        <div className={`modal-actions ${styles.actions}`}>
          <Button variant="ghost" className={styles.cancel} onClick={onCancel} disabled={busy}>취소</Button>
          <Button variant="primary" icon={busy ? undefined : error ? 'refresh' : 'download'} onClick={onDownload} disabled={busy}>
            {busy ? '만드는 중…' : error ? '다시 시도' : '다운로드'}
          </Button>
        </div>
      </footer>
    </div>
  );
}
