import { useState, useMemo, useRef, useEffect, useCallback } from 'react';
import { Button } from '@/components/ui.jsx';
import { GIF_PRESETS, GIF_HARD_BYTES, GIF_SOFT_BYTES } from '@/lib/gif/gifLadder.js';
import { encodeGif, loadGifVideo, releaseGifVideo, VIDEO_FORMAT_MESSAGE } from '@/lib/gif/encodeGif.js';
import styles from './GifThumbnailPanel.module.css';

function wardrobePhotos(wardrobe, detailColorOpts) {
  const seen = new Set();
  return Object.entries(wardrobe || {}).flatMap(([group, images]) => images.flatMap((image) => {
    if (image.loading || !image.id || !image.src || seen.has(image.id)) return [];
    seen.add(image.id);
    const groupLabel = detailColorOpts.find((color) => color.id === group)?.label || '보관함';
    return [{ ...image, groupLabel }];
  })).sort((a, b) => Number(b.cutType === 'styling') - Number(a.cutType === 'styling'));
}

export function GifThumbnailPanel({ wardrobe, detailColorOpts = [] }) {
  const [selectedIds, setSelectedIds] = useState([]);
  const [mode, setMode] = useState('photo');
  const [videoSource, setVideoSource] = useState(null);
  const [preset, setPreset] = useState('ably');
  const [delayMs, setDelayMs] = useState(1500);
  const [start, setStart] = useState(0);
  const [duration, setDuration] = useState(3);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [loadingVideo, setLoadingVideo] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState(null);
  const fileInput = useRef(null);
  const encoding = useRef(null);
  const videoLoading = useRef(null);
  const photos = useMemo(() => wardrobePhotos(wardrobe, detailColorOpts), [wardrobe, detailColorOpts]);
  const selected = selectedIds.map((id) => photos.find((photo) => photo.id === id)).filter(Boolean);
  const selectedSources = JSON.stringify(selected.map(({ id, src }) => [id, src]));

  const invalidate = useCallback(() => {
    encoding.current?.abort();
    encoding.current = null;
    setBusy(false);
    setProgress(0);
    setResult(null);
    setError('');
  }, []);

  useEffect(() => {
    setSelectedIds((ids) => {
      const remaining = ids.filter((id) => photos.some((photo) => photo.id === id));
      return remaining.length === ids.length ? ids : remaining;
    });
  }, [photos]);
  useEffect(invalidate, [mode, preset, delayMs, start, duration, videoSource, selectedSources, invalidate]);
  useEffect(() => () => {
    encoding.current?.abort();
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
    setError('');
    setProgress(0);
    setBusy(true);
    try {
      const encoded = await encodeGif({ mode, sources: selected.map((photo) => photo.src), video: videoSource?.video,
        preset, delayMs, start, duration, signal: controller.signal,
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

  const locked = busy || loadingVideo;
  const maxDuration = Math.min(4, videoSource?.video.duration || 4);
  const maxStart = Math.max(0, (videoSource?.video.duration || 0) - duration);
  return (
    <div className={styles.panel}>
      <input ref={fileInput} type="file" className={styles.fileInput} accept="video/mp4,video/webm,video/quicktime"
        onChange={pickVideo} disabled={locked} aria-label="GIF로 만들 영상 선택" />
      {mode === 'photo' ? (
        <section aria-label="보관함 사진" className={styles.section}>
          <h3 className={styles.label}>사진 2~8장 <span>{selected.length}/8</span></h3>
          <p className={styles.hint}>선택한 순서대로 돌아가요</p>
          {photos.length ? (
            <div className="wardrobe-grid">
              {photos.map((photo, index) => {
                const order = selectedIds.indexOf(photo.id);
                return (
                  <button type="button" key={photo.id} className={`ward-cell ${styles.tile}${order >= 0 ? ` ${styles.selected}` : ''}`}
                    aria-pressed={order >= 0} aria-label={`${photo.groupLabel} 사진 ${index + 1}${order >= 0 ? `, 선택 순서 ${order + 1}` : ''}`}
                    disabled={locked || (order < 0 && selectedIds.length >= 8)} onClick={() => selectPhoto(photo.id)}>
                    <img src={photo.src} alt="" loading="lazy" decoding="async" />
                    {order >= 0 && <span className={styles.order}>{order + 1}</span>}
                  </button>
                );
              })}
            </div>
          ) : <p className={styles.hint}>보관함에 사진을 추가해 주세요</p>}
          <Button variant="ghost" block onClick={() => fileInput.current?.click()} disabled={locked} className={styles.videoButton}>
            {loadingVideo ? '영상을 읽는 중' : '영상으로 만들기'}
          </Button>
        </section>
      ) : (
        <section aria-label="영상 구간" className={styles.section}>
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
          <button type="button" className={styles.backLink} onClick={() => { setMode('photo'); setVideoSource(null); }} disabled={locked}>사진으로 만들기</button>
        </section>
      )}
      <fieldset className={styles.options} disabled={locked}>
        <legend className={styles.label}>규격</legend>
        <div className={styles.chips}>
          {GIF_PRESETS.map((option) => <button type="button" className={styles.chip} key={option.id}
            aria-pressed={preset === option.id} onClick={() => setPreset(option.id)}>{option.label}</button>)}
        </div>
      </fieldset>
      {mode === 'photo' && (
        <fieldset className={styles.options} disabled={locked}>
          <legend className={styles.label}>장당 시간</legend>
          <div className={styles.chips}>
            {[1000, 1500, 2000].map((value) => <button type="button" className={styles.chip} key={value}
              aria-pressed={delayMs === value} onClick={() => setDelayMs(value)}>{(value / 1000).toFixed(1)}초</button>)}
          </div>
        </fieldset>
      )}
      {error && <p role="alert" className={styles.error}>{error}</p>}
      <Button variant="primary" block onClick={makeGif} disabled={locked || (mode === 'photo' ? selected.length < 2 : !videoSource)}>
        <span role="status" aria-live="polite">{busy ? `만드는 중 ${progress}%` : 'GIF 만들기'}</span>
      </Button>
      {result && (
        <section className={styles.result} aria-label="GIF 결과">
          <img className={styles.preview} src={result.gifUrl} width={result.width} height={result.height} alt="움직이는 썸네일 미리보기" />
          <p className={styles.summary}>{result.width}×{result.height} · {result.frames}장 · {(result.bytes / 1_000_000).toFixed(1)}MB</p>
          {result.bytes > GIF_SOFT_BYTES && <p className={styles.hint}>용량이 커서 상품 목록이 느려질 수 있어요</p>}
          {result.bytes > GIF_HARD_BYTES && <p className={styles.error}>4.7MB를 넘어 카페24에 올라가지 않을 수 있어요</p>}
          <div className={`modal-actions ${styles.actions}`}>
            <a className="btn btn-primary" href={result.gifUrl} download="thumbnail.gif">GIF 받기</a>
            <a className="btn btn-ghost" href={result.jpgUrl} download="thumbnail.jpg">첫 컷 JPG 받기</a>
          </div>
        </section>
      )}
    </div>
  );
}
