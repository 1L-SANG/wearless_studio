import { Button } from '@/components/ui.jsx';
import {
  EXPORT_PRESETS,
  normalizeExportSettings,
} from '@/features/editor/editorExport.js';
import styles from './EditorDownloadModal.module.css';
import { missingNoticeFields } from './editorDownloadWarnings.js';

const FORMAT_OPTIONS = [
  { id: 'jpeg', title: 'JPG', desc: '가볍게, 품질 90' },
  { id: 'png', title: 'PNG', desc: '고화질, 파일이 큼' },
];

const DELIVERY_OPTIONS = [
  { id: 'split', title: '나눠서 받기', desc: '블록 경계를 살려 3,000px 이하 조각을 ZIP으로 저장' },
  { id: 'single', title: '한 장으로 받기', desc: '선택한 폭으로 전체 상세페이지를 한 장에 저장' },
];

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
  blocks = [],
  busy,
  progress,
  onChange,
  onCancel,
  onDownload,
}) {
  const settings = normalizeExportSettings(rawSettings);
  const original = settings.preset === 'original';
  const missingFields = missingNoticeFields(blocks);
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
        <h3 className={styles.title}>쇼핑몰에 올릴 파일 만들기</h3>
        <p className={styles.subtitle}>올릴 곳에 맞는 폭과 파일 형식을 골라 주세요.</p>
      </header>

      {missingFields.length > 0 && (
        <p className={styles.notice} role="status">고시정보 입력 필요: {missingFields.join(', ')}</p>
      )}

      <div className={styles.groups}>
        <fieldset className={styles.group}>
          <legend>어디에 올릴지</legend>
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
          <legend>형식</legend>
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
        <h4>받기 전 요약</h4>
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

      <footer className={`modal-actions ${styles.footer}`}>
        <Button variant="quiet" onClick={onCancel} disabled={busy}>취소</Button>
        <Button variant="primary" icon="download" onClick={onDownload} disabled={busy}>
          {busy ? `만드는 중…${progress ? ` ${progress}` : ''}` : missingFields.length ? '그대로 받기' : '다운로드'}
        </Button>
      </footer>
    </div>
  );
}
