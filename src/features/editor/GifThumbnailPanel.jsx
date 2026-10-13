import { useState, useMemo, useRef, useEffect, useCallback } from 'react';
import { Button, Icon } from '@/components/ui.jsx';
import { thumbUrl } from '@/lib/imageCdn.js';
import { GIF_PRESETS, GIF_HARD_BYTES, GIF_SOFT_BYTES, GIF_PHOTO_DELAY_MS } from '@/lib/gif/gifLadder.js';
import { encodeGif, loadGifVideo, releaseGifVideo, VIDEO_FORMAT_MESSAGE } from '@/lib/gif/encodeGif.js';
import { isGifImage, wardrobePhotoGroups, gifSummary } from '@/lib/gif/gifMedia.js';
import styles from './GifThumbnailPanel.module.css';

export function GifThumbnailPanel({ wardrobe, detailColorOpts = [], onSaveGif, onInsert, onDeleteImage, isImageUsed }) {
  const [selectedIds, setSelectedIds] = useState([]);
  const [mode, setMode] = useState('photo');
  const [videoSource, setVideoSource] = useState(null);
  const [preset, setPreset] = useState('original');
  const [start, setStart] = useState(0);
  const [duration, setDuration] = useState(3);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [loadingVideo, setLoadingVideo] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [hoveredGif, setHoveredGif] = useState(null);
  const saveController = useRef(null);
  const fileInput = useRef(null);
  const encoding = useRef(null);
  const videoLoading = useRef(null);
  const groups = useMemo(() => wardrobePhotoGroups(wardrobe, detailColorOpts), [wardrobe, detailColorOpts]);
  const photos = useMemo(() => groups.flatMap((group) => group.photos.map((photo) => ({ ...photo, groupLabel: group.label }))), [groups]);
  const madeGifs = Object.values(wardrobe || {}).flat().filter(isGifImage);
  const selected = selectedIds.map((id) => photos.find((photo) => photo.id === id)).filter(Boolean);
  const selectedSources = JSON.stringify(selected.map(({ id, src }) => [id, src]));

  const invalidate = useCallback(() => {
    encoding.current?.abort();
    encoding.current = null;
    setBusy(false);
    setProgress(0);
    setResult(null);
    setSaved(false);
    setError('');
  }, []);

  useEffect(() => {
    setSelectedIds((ids) => {
      const remaining = ids.filter((id) => photos.some((photo) => photo.id === id));
      return remaining.length === ids.length ? ids : remaining;
    });
  }, [photos]);
  useEffect(invalidate, [mode, preset, start, duration, videoSource, selectedSources, invalidate]);
  useEffect(() => () => {
    encoding.current?.abort();
    saveController.current?.abort();
    videoLoading.current?.abort();
    videoLoading.current = null;
  }, []);
  useEffect(() => () => {
    if (!videoSource) return;
    releaseGifVideo(videoSource.video);
    URL.revokeObjectURL(videoSource.url);
  }, [videoSource]);
  useEffect(() => () => {
    if (!result) return;
    URL.revokeObjectURL(result.gifUrl);
    URL.revokeObjectURL(result.jpgUrl);
  }, [result]);

  const selectPhoto = (id) => {
    setSelectedIds((ids) => ids.includes(id) ? ids.filter((value) => value !== id) : ids.length < 8 ? [...ids, id] : ids);
  };

  const pickVideo = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    videoLoading.current?.abort();
    invalidate();
    const controller = new AbortController();
    videoLoading.current = controller;
    setLoadingVideo(true);
    const url = URL.createObjectURL(file);
    let retained = false;
    try {
      const video = await loadGifVideo(url, { signal: controller.signal });
      if (controller.signal.aborted || videoLoading.current !== controller) { releaseGifVideo(video); return; }
      setVideoSource({ video, url });
      retained = true;
      setDuration(Math.min(3, video.duration));
      setStart(0);
      setMode('video');
    } catch (failure) {
      if (!controller.signal.aborted && videoLoading.current === controller) setError(failure.message || VIDEO_FORMAT_MESSAGE);
    } finally {
      if (!retained) URL.revokeObjectURL(url);
      if (videoLoading.current === controller) {
        videoLoading.current = null;
        setLoadingVideo(false);
      }
    }
  };

  const makeGif = async () => {
    if (encoding.current || loadingVideo || (mode === 'photo' && selected.length < 2)) return;
    const controller = new AbortController();
    encoding.current = controller;
    setResult(null);
    setSaved(false);
    setError('');
    setProgress(0);
    setBusy(true);
    try {
      const encoded = await encodeGif({ mode, sources: selected.map((photo) => photo.src), video: videoSource?.video,
        preset, delayMs: GIF_PHOTO_DELAY_MS, start, duration, signal: controller.signal,
        onProgress: (value) => { if (!controller.signal.aborted) setProgress(value); },
      });
      if (controller.signal.aborted || encoding.current !== controller) return;
      const gifUrl = URL.createObjectURL(encoded.blob);
      try {
        const jpgUrl = URL.createObjectURL(encoded.jpgBlob);
        setResult({ ...encoded, gifUrl, jpgUrl });
      } catch (failure) { URL.revokeObjectURL(gifUrl); throw failure; }
    } catch (failure) {
      if (!controller.signal.aborted && encoding.current === controller) setError(failure.message || 'GIF를 만들지 못했어요. 다시 시도해 주세요');
    } finally {
      if (encoding.current === controller) { encoding.current = null; setBusy(false); }
    }
  };

  const saveGif = async () => {
    if (!result || saved || saveController.current || result.bytes > GIF_HARD_BYTES || !onSaveGif) return;
    const controller = new AbortController();
    saveController.current = controller;
    setSaving(true);
    setError('');
    try {
      await onSaveGif(result, { signal: controller.signal });
      if (!controller.signal.aborted) setSaved(true);
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure.message || 'GIF를 저장하지 못했어요. 다시 시도해 주세요');
    } finally {
      if (saveController.current === controller) { saveController.current = null; setSaving(false); }
    }
  };

  const locked = busy || loadingVideo || saving;
  const maxDuration = Math.min(4, videoSource?.video.duration || 4);
  const maxStart = Math.max(0, (videoSource?.video.duration || 0) - duration);
  return (
    <div className={styles.panel}>
      <input ref={fileInput} type="file" className={styles.fileInput} accept="video/mp4,video/webm,video/quicktime"
        onChange={pickVideo} disabled={locked} aria-label="GIF로 만들 영상 선택" />
      <div className={`modal-actions ${styles.actions}`} aria-label="GIF 재료">
        <Button variant={mode === 'photo' ? 'primary' : 'ghost'} aria-pressed={mode === 'photo'}
          disabled={locked} onClick={() => { setMode('photo'); setVideoSource(null); }}>사진으로</Button>
        <Button variant={mode === 'video' ? 'primary' : 'ghost'} aria-pressed={mode === 'video'}
          disabled={locked} onClick={() => setMode('video')}>영상으로</Button>
      </div>
      {mode === 'photo' ? (
        <section aria-label="보관함 사진" className={styles.section}>
          <p className={styles.hint}>선택한 순서대로 돌아가요 · {selected.length}/8</p>
          {groups.map((group) => (
            <div className="wardrobe-group" key={group.id}>
              <h3 className={styles.groupHeading}>
                <svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="8" fill={group.color || 'currentColor'} /></svg>
                {group.label}<span>{group.photos.length}</span>
              </h3>
              <div className={`wardrobe-grid ${styles.photoGrid}`}>
                {group.photos.map((photo, index) => {
                  const order = selectedIds.indexOf(photo.id);
                  return (
                    <button type="button" key={photo.id} className={`ward-cell ${styles.tile}${order >= 0 ? ` ${styles.selected}` : ''}`}
                      aria-pressed={order >= 0} aria-label={`${group.label} 사진 ${index + 1}${order >= 0 ? `, 선택 순서 ${order + 1}` : ''}`}
                      disabled={locked || (order < 0 && selectedIds.length >= 8)} onClick={() => selectPhoto(photo.id)}>
                      <img src={thumbUrl(photo.src, 240)} alt="" loading="lazy" decoding="async" draggable={false} />
                      {order >= 0 && <span className={styles.order}>{order + 1}</span>}
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
          {!photos.length && <p className={styles.hint}>이미지 탭에서 사진을 추가해 주세요</p>}
        </section>
      ) : (
        <section aria-label="영상 구간" className={styles.section}>
          <Button variant="ghost" block onClick={() => fileInput.current?.click()} disabled={locked}>
            {loadingVideo ? '영상을 읽는 중' : '영상 파일 고르기'}
          </Button>
          {videoSource && <>
            <label className={styles.rangeLabel} htmlFor="gif-start">시작 지점 <span>{start.toFixed(1)}초</span></label>
            <input id="gif-start" className={styles.range} type="range" min={0} max={maxStart} step="0.1" value={start}
              onChange={(event) => setStart(Math.min(maxStart, Number(event.target.value)))} disabled={locked || maxStart === 0} />
            <label className={styles.rangeLabel} htmlFor="gif-duration">길이 <span>{duration.toFixed(1)}초</span></label>
            <input id="gif-duration" className={styles.range} type="range" min={1} max={maxDuration} step="0.1" value={duration}
              onChange={(event) => {
                const length = Math.max(1, Math.min(maxDuration, Number(event.target.value)));
                setDuration(length);
                setStart((value) => Math.min(value, Math.max(0, videoSource.video.duration - length)));
              }} disabled={locked} />
          </>}
        </section>
      )}
      <fieldset className={styles.options} disabled={locked}>
        <legend className={styles.label}>규격</legend>
        <div className={styles.ratios}>
          {GIF_PRESETS.map((option) => <button type="button" className={styles.ratio} key={option.id}
            aria-pressed={preset === option.id} onClick={() => setPreset(option.id)}>
              <span aria-hidden="true" className={`${styles.ratioFrame} ${styles[option.id]}`} />
              <span>{option.label}</span>
            </button>)}
        </div>
      </fieldset>
      {error && <p role="alert" className={styles.error}>{error}</p>}
      <Button variant="primary" block onClick={makeGif} disabled={locked || (mode === 'photo' ? selected.length < 2 : !videoSource)}>
        <span role="status" aria-live="polite">{busy ? `만드는 중 ${progress}%` : 'GIF 만들기'}</span>
      </Button>
      {result && (
        <section className={styles.result} aria-label="GIF 결과">
          <img className={styles.preview} src={result.gifUrl} width={result.width} height={result.height} alt="움직이는 썸네일 미리보기" />
          <p className={styles.summary}>{gifSummary(result)}</p>
          {result.bytes > GIF_SOFT_BYTES && <p className={styles.hint}>용량이 커서 상품 목록이 느려질 수 있어요</p>}
          {result.bytes > GIF_HARD_BYTES && <p className={styles.error}>4.7MB를 넘어 카페24에 올라가지 않을 수 있어요</p>}
          <div className={`modal-actions ${styles.actions}`}>
            <Button variant="primary" block onClick={saveGif} disabled={saving || saved || result.bytes > GIF_HARD_BYTES || !onSaveGif}>
              {saving ? '저장 중' : saved ? '저장됨' : '의류에 저장'}
            </Button>
          </div>
          <div className={`modal-actions ${styles.actions}`}>
            <a className="btn btn-ghost" href={result.gifUrl} download="thumbnail.gif">GIF 받기</a>
            <a className="btn btn-ghost" href={result.jpgUrl} download="thumbnail.jpg">첫 컷 JPG 받기</a>
          </div>
        </section>
      )}
      {madeGifs.length > 0 && <section className={styles.section} aria-label="만든 GIF">
        <h3 className={styles.label}>만든 GIF <span>{madeGifs.length}</span></h3>
        <div className="wardrobe-grid">
          {madeGifs.map((image, index) => {
            const used = Boolean(isImageUsed?.(image));
            return <div className={`ward-cell ${styles.savedTile}`} key={image.id}
              onMouseEnter={() => setHoveredGif(image.id)} onMouseLeave={() => setHoveredGif(null)}>
              <button type="button" className={styles.savedInsert} aria-label={`만든 GIF ${index + 1} 캔버스에 넣기`}
                onFocus={() => setHoveredGif(image.id)} onBlur={() => setHoveredGif(null)} onClick={() => onInsert?.(image)}>
                <img src={hoveredGif === image.id ? image.src : thumbUrl(image.posterSrc, 240)} alt="" loading="lazy" draggable={false} />
                <span className={styles.gifBadge}>GIF</span>
                <span className={styles.gifBytes}>{(image.gifMeta.bytes / 1_000_000).toFixed(1)}MB</span>
              </button>
              <button type="button" className={`ward-rm${used ? ' disabled' : ''}`} aria-disabled={used}
                aria-label={used ? '현재 에디팅에 사용 중인 GIF' : 'GIF 삭제'} onClick={() => onDeleteImage?.(image)}>
                <Icon name="x" size={12} />
              </button>
            </div>;
          })}
        </div>
      </section>}
    </div>
  );
}
