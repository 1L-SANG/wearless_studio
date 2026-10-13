import { useState, useMemo, useRef, useEffect } from 'react';
import { Button, Icon } from '@/components/ui.jsx';
import { thumbUrl } from '@/lib/imageCdn.js';
import { GIF_PRESETS, GIF_HARD_BYTES, cropLossRatio, presetSize } from '@/lib/gif/gifLadder.js';
import { encodeGif } from '@/lib/gif/encodeGif.js';
import { inspectGifAttachment, prepareGifAttachment, reduceGifAttachment } from '@/lib/gif/gifAttachment.js';
import { isGifImage, wardrobePhotoGroups } from '@/lib/gif/gifMedia.js';
import styles from './GifThumbnailPanel.module.css';

export function GifThumbnailPanel({ wardrobe, detailColorOpts = [], onSaveGif, onInsert, onDeleteImage, isImageUsed, onFreshSeen, onDownloadGif }) {
  const [creating, setCreating] = useState(false);
  const [selectedIds, setSelectedIds] = useState([]);
  const [preset, setPreset] = useState('original');
  const [busy, setBusy] = useState(false);
  const [reducing, setReducing] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState('');
  const [attachment, setAttachment] = useState(null);
  const [hoveredGif, setHoveredGif] = useState(null);
  const [dimensions, setDimensions] = useState({});
  const fileInput = useRef(null);
  const operation = useRef(null);
  const groups = useMemo(() => wardrobePhotoGroups(wardrobe, detailColorOpts), [wardrobe, detailColorOpts]);
  const photos = useMemo(() => groups.flatMap(group => group.photos), [groups]);
  const selected = selectedIds.map(id => photos.find(photo => photo.id === id)).filter(Boolean);
  const selectedSources = JSON.stringify(selected.map(({ id, src }) => [id, src]));
  const madeGifs = Object.values(wardrobe || {}).flat().filter(isGifImage);
  const sizeOf = photo => dimensions[photo?.src] || (photo?.width && photo?.height ? photo : null);
  const firstSize = sizeOf(selected[0]);
  const targetSize = preset !== 'original' || firstSize ? presetSize(preset, 1, firstSize) : null;

  useEffect(() => {
    setSelectedIds(ids => {
      const remaining = ids.filter(id => photos.some(photo => photo.id === id));
      return remaining.length === ids.length ? ids : remaining;
    });
  }, [photos]);
  useEffect(() => {
    operation.current?.abort();
    operation.current = null;
    setBusy(false); setProgress(0); setError('');
  }, [selectedSources, preset]);
  useEffect(() => () => operation.current?.abort(), []);

  const cancel = () => {
    operation.current?.abort(); operation.current = null;
    setBusy(false); setError(''); setCreating(false); setSelectedIds([]); setAttachment(null);
  };
  const perform = async (task, isReduction = false) => {
    if (operation.current) return;
    const controller = new AbortController();
    operation.current = controller;
    setBusy(true); setReducing(isReduction); setProgress(0); setError('');
    const options = { signal: controller.signal, onProgress: value => { if (!controller.signal.aborted) setProgress(value); } };
    try {
      await task(options);
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure.message || 'GIF를 담지 못했어요. 다시 시도해 주세요');
    } finally {
      if (operation.current === controller) { operation.current = null; setBusy(false); }
    }
  };
  const save = async (result, options, allowOversize = false) => {
    if (options.signal.aborted) return;
    if (!allowOversize && Math.max(result.bytes || 0, result.blob.size) > GIF_HARD_BYTES) {
      throw new Error(creating ? '용량이 커서 담지 못했어요. 사진 수를 줄여 주세요' : '줄여도 4.7MB를 넘어요. 더 짧은 GIF를 올려 주세요');
    }
    await onSaveGif(result, { signal: options.signal, allowOversize });
    if (options.signal.aborted) return;
    setCreating(false); setSelectedIds([]); setAttachment(null);
  };
  const makeGif = () => {
    if (selected.length < 2 || !onSaveGif) return;
    return perform(async options => save(await encodeGif({ sources: selected.map(photo => photo.src), preset, ...options }), options));
  };
  const attach = event => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    setAttachment(null);
    return perform(async options => {
      const info = await inspectGifAttachment(file, options);
      if (options.signal.aborted) return;
      if (info.bytes > GIF_HARD_BYTES) setAttachment(info);
      else await save(await prepareGifAttachment(info, options), options);
    });
  };

  return <div className={styles.panel}>
    {creating ? <>
      <div className={styles.header}><h3 className={styles.label}>새 GIF 만들기</h3>
        <button type="button" className={styles.textButton} onClick={cancel}>취소</button></div>
      <section aria-label="보관함 사진" className={styles.section}>
        <p className={styles.hint}>사진 2~8장을 고르세요 · 고른 순서로 돌아가요 · {selected.length}/8</p>
        {groups.map(group => <div className="wardrobe-group" key={group.id}>
          <h3 className={styles.groupHeading}>
            <svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="8" fill={group.color || 'currentColor'} /></svg>
            {group.label}<span>{group.photos.length}</span>
          </h3>
          <div className={`wardrobe-grid ${styles.photoGrid}`}>
            {group.photos.map((photo, index) => {
              const order = selectedIds.indexOf(photo.id), size = sizeOf(photo);
              const cropped = order >= 0 && size && targetSize && cropLossRatio(size.width, size.height, targetSize.width, targetSize.height) > 0.25;
              return <button type="button" key={photo.id} className={`ward-cell ${styles.tile}${order >= 0 ? ` ${styles.selected}` : ''}`}
                aria-pressed={order >= 0} aria-label={`${group.label} 사진 ${index + 1}${order >= 0 ? `, 선택 순서 ${order + 1}` : ''}`}
                disabled={busy || (order < 0 && selectedIds.length >= 8)}
                onClick={() => setSelectedIds(ids => ids.includes(photo.id) ? ids.filter(id => id !== photo.id) : ids.length < 8 ? [...ids, photo.id] : ids)}>
                <img src={thumbUrl(photo.src, 240)} alt="" loading="lazy" decoding="async" draggable={false}
                  onLoad={event => {
                    const { naturalWidth: width, naturalHeight: height } = event.currentTarget;
                    if (width && height) setDimensions(current => current[photo.src]?.width === width && current[photo.src]?.height === height
                      ? current : { ...current, [photo.src]: { width, height } });
                  }} />
                {order >= 0 && <span className={styles.order}>{order + 1}</span>}
                {cropped && <span className={styles.cropBadge}>많이 잘려요</span>}
              </button>;
            })}
          </div>
        </div>)}
        {!photos.length && <p className={styles.hint}>이미지 탭에서 사진을 추가해 주세요</p>}
      </section>
      <fieldset className={styles.options} disabled={busy}>
        <legend className={styles.label}>규격</legend>
        <div className={styles.ratios}>{GIF_PRESETS.map(option => <button type="button" className={styles.ratio} key={option.id}
          aria-pressed={preset === option.id} onClick={() => setPreset(option.id)}>
          <span aria-hidden="true" className={`${styles.ratioFrame} ${styles[option.id]}`} /><span>{option.label}</span>
        </button>)}</div>
      </fieldset>
      <Button variant="primary" block onClick={makeGif} disabled={busy || selected.length < 2 || !onSaveGif}>
        <span role="status" aria-live="polite">{busy ? `만드는 중 ${progress}%` : 'GIF 만들기'}</span>
      </Button>
    </> : <>
      <input ref={fileInput} type="file" className={styles.fileInput} accept="image/gif,.gif" onChange={attach} disabled={busy} aria-label="GIF 첨부" />
      <Button variant="primary" block disabled={busy} onClick={() => { setCreating(true); setPreset('original'); setSelectedIds([]); setAttachment(null); setError(''); }}>새 GIF 만들기</Button>
      {attachment && <section className={styles.section} aria-label="GIF 용량 확인">
        <p className={styles.hint}>4.7MB를 넘어요. 용량을 줄여서 담을까요?</p>
        <div className={`modal-actions ${styles.actions}`}>
          <Button variant="primary" disabled={busy} onClick={() => perform(async options => save(await reduceGifAttachment(attachment, options), options), true)}>
            {busy && reducing ? `줄이는 중 ${progress}%` : '줄여서 담기'}
          </Button>
          <Button variant="ghost" disabled={busy} onClick={() => perform(async options => save(await prepareGifAttachment(attachment, options), options, true))}>그대로 담기</Button>
        </div>
        <p className={styles.hint}>4.7MB를 넘어 카페24에 올라가지 않을 수 있어요</p>
      </section>}
      {busy && <p role="status" className={styles.hint}>담는 중 {progress}%</p>}
      <section className={styles.section} aria-label="보관한 GIF">
        <h3 className={styles.label}>보관한 GIF <span>{madeGifs.length}</span></h3>
        <div className="wardrobe-grid">
          <button type="button" className={`sb-addslot ${styles.addTile}`} disabled={busy} onClick={() => fileInput.current?.click()}>＋ GIF 첨부</button>
          {madeGifs.map((image, index) => {
            const used = Boolean(isImageUsed?.(image));
            return <div className={`ward-cell ${styles.savedTile}${image.fresh ? ' fresh' : ''}`} key={image.id}
              onAnimationEnd={image.fresh ? () => onFreshSeen?.(image.id) : undefined}
              onMouseEnter={() => setHoveredGif(image.id)} onMouseLeave={() => setHoveredGif(null)}>
              <button type="button" className={styles.savedInsert} aria-label={`보관한 GIF ${index + 1} 캔버스에 넣기`}
                onFocus={() => setHoveredGif(image.id)} onBlur={() => setHoveredGif(null)} onClick={() => onInsert?.(image)}>
                <img src={hoveredGif === image.id ? image.src : thumbUrl(image.posterSrc, 240)} alt="" loading="lazy" draggable={false} />
                <span className={styles.gifBadge}>GIF · {(image.gifMeta.bytes / 1_000_000).toFixed(1)}MB</span>
              </button>
              {image.gifMeta.bytes > GIF_HARD_BYTES && <span className="sr-only">4.7MB를 넘어 카페24에 올라가지 않을 수 있어요</span>}
              <button type="button" className={`ward-rm ${styles.download}`} aria-label="GIF 받기" title="GIF 받기" onClick={() => onDownloadGif?.(image)}><Icon name="download" size={12} /></button>
              <button type="button" className={`ward-rm${used ? ' disabled' : ''}`} aria-disabled={used}
                aria-label={used ? '현재 에디팅에 사용 중인 GIF' : 'GIF 삭제'} onClick={() => onDeleteImage?.(image)}><Icon name="x" size={12} /></button>
            </div>;
          })}
        </div>
      </section>
    </>}
    {error && <p role="alert" className={styles.error}>{error}</p>}
  </div>;
}
