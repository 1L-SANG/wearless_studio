/* 신분증 가이드 촬영.

   파일 선택을 카메라로 바꾸는 이유는 둘이다. 첫째, 맥 사진앱 HEIC 처럼 브라우저가
   못 그리는 포맷이 구조적으로 사라진다(우리가 canvas.toBlob 으로 만든다). 둘째,
   카드가 가이드를 채우면 주민등록번호 위치가 규격으로 계산돼 사용자가 박스를
   끌 필요가 없어진다(설계 §2).

   자동 셔터는 편의이지 관문이 아니다 — 조명·배경에 따라 안 잡힐 수 있으므로
   수동 셔터를 항상 노출하고, 15초가 지나면 그렇게 안내한다(설계 §6).

   마스킹은 이 컴포넌트가 직접 한다 — burnGuideMask 는 video 엘리먼트가 필요한데
   그건 여기 안에만 있다. onCaptured 는 이미 주민번호가 칠해진 JPEG blob 하나만
   내보낸다: 마스킹 전 blob 이 부모 state 를 한 틱이라도 거치면 그 자체가 이 기능이
   없애려는 노출이다. 내부적으로 burnGuideMask 가 drawImage → 주민번호 사각형 fillRect
   → canvas.toBlob('image/jpeg', quality) 순서로 처리하고, 그 결과 blob 만 여기로
   돌아온다 — 원본 프레임은 절대 onCaptured 인자로 나가지 않는다.

   캡처는 항상 video.videoWidth/videoHeight(실제 프레임 픽셀)를 쓴다. 화면 표시
   크기(clientWidth 등 CSS 크기)로 찍으면 burnGuideMask 가 계산하는 주민번호 사각형이
   실제 카드 위치와 어긋난다 — 배율이 다른 두 좌표계를 섞는 것이기 때문이다. 같은
   이유로 화면 가이드 박스도 CSS 로 따로 그리지 않고 idCardGeometry.guideRectPercent
   에서 뽑는다: 비디오를 width:100%; height:auto 로 두면 표시 박스가 곧 프레임이라
   이 백분율이 어떤 배율에서도 정확히 겹친다. */
import { useCallback, useEffect, useRef, useState } from 'react';
import { guideRectPercent } from './idCardGeometry.js';
import { createShutterGate, scoreFrame } from './idCardDetector.js';
import { burnGuideMask } from './idDocumentMasking.js';
import s from './ModelRegister.module.css';

const SAMPLE_WIDTH = 320;     // 판정용 다운스케일 — 전체 해상도로 돌릴 이유가 없다
const HINT_AFTER_MS = 15000;
const SAMPLE_INTERVAL_MS = 100;   // ~10fps

export default function IdCameraCapture({ onCaptured, onUnavailable, busy }) {
  const videoRef = useRef(null);
  const sampleRef = useRef(null);   // 판정용 작은 캔버스
  const shotRef = useRef(null);     // 촬영용 전체 해상도 캔버스
  const streamRef = useRef(null);
  const gateRef = useRef(createShutterGate({ needed: 5 }));
  const capturingRef = useRef(false);   // burnGuideMask 가 비동기라 진행 중 중복 촬영을 막는다
  const mountedRef = useRef(true);
  const [ready, setReady] = useState(false);
  const [frameSize, setFrameSize] = useState(null);   // { width, height } — 오버레이 좌표계
  const [showManualHint, setShowManualHint] = useState(false);

  // 카메라 시작 + 언마운트에서 트랙 정지(안 하면 카메라 표시등이 계속 켜져 있다)
  useEffect(() => {
    let cancelled = false;
    const video = videoRef.current;
    // play() 가 풀리는 시점에 videoWidth/Height 가 아직 0일 수 있다(메타데이터
    // 로딩이 살짝 늦는 기기가 있다) — loadedmetadata 에서 한 번 더 잡아 둔다.
    const captureFrameSize = () => {
      if (video?.videoWidth && video?.videoHeight) {
        setFrameSize({ width: video.videoWidth, height: video.videoHeight });
      }
    };
    video?.addEventListener('loadedmetadata', captureFrameSize);
    (async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: 'environment' }, width: { ideal: 1920 } },
          audio: false,
        });
        if (cancelled) { stream.getTracks().forEach((t) => t.stop()); return; }
        streamRef.current = stream;
        if (video) {
          video.srcObject = stream;
          await video.play();
          // play() 는 언마운트 이후에도 풀릴 수 있다 — 여기서 cancelled 를 안 보면
          // 클린업이 리스너를 떼어낸 뒤에도 이 직접 호출은 setFrameSize 를 부른다.
          if (cancelled) return;
          captureFrameSize();
        }
        if (cancelled) return;
        setReady(true);
      } catch (error) {
        onUnavailable?.(error?.name === 'NotAllowedError' ? 'permission' : 'unsupported');
      }
    })();
    return () => {
      cancelled = true;
      video?.removeEventListener('loadedmetadata', captureFrameSize);
      streamRef.current?.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    };
  }, [onUnavailable]);

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  // 실제 프레임 픽셀(videoWidth/videoHeight)로만 찍는다 — CSS 표시 크기로 찍으면
  // burnGuideMask 가 계산하는 주민번호 사각형이 실제 카드 위치와 어긋난다.
  const capture = useCallback(() => {
    const video = videoRef.current;
    const canvas = shotRef.current;
    if (!video || !canvas || capturingRef.current) return;
    const width = video.videoWidth;
    const height = video.videoHeight;
    if (!width || !height) return;
    capturingRef.current = true;
    gateRef.current.reset();   // 게이트는 레벨 트리거라 리셋 안 하면 다음 프레임에도 계속 찍힌다
    burnGuideMask(canvas, video, width, height)
      .then((blob) => {
        if (!mountedRef.current) return;
        onCaptured?.(blob);
      })
      .finally(() => { capturingRef.current = false; });
  }, [onCaptured]);

  // ~10fps 판정 루프: video → 작은 캔버스 → 그레이스케일 → scoreFrame → gate
  useEffect(() => {
    if (!ready) return undefined;
    const video = videoRef.current;
    const sampleCanvas = sampleRef.current;
    if (!video || !sampleCanvas) return undefined;
    const ctx = sampleCanvas.getContext('2d', { willReadFrequently: true });
    const timer = setInterval(() => {
      if (capturingRef.current) return;
      const vw = video.videoWidth;
      const vh = video.videoHeight;
      if (!vw || !vh) return;
      const sampleHeight = Math.round(SAMPLE_WIDTH * (vh / vw));
      sampleCanvas.width = SAMPLE_WIDTH;
      sampleCanvas.height = sampleHeight;
      ctx.drawImage(video, 0, 0, SAMPLE_WIDTH, sampleHeight);
      const { data } = ctx.getImageData(0, 0, SAMPLE_WIDTH, sampleHeight);
      const gray = new Uint8ClampedArray(SAMPLE_WIDTH * sampleHeight);
      for (let i = 0; i < gray.length; i++) {
        const o = i * 4;
        gray[i] = (data[o] * 0.299 + data[o + 1] * 0.587 + data[o + 2] * 0.114);
      }
      const score = scoreFrame(gray, SAMPLE_WIDTH, sampleHeight);
      if (gateRef.current.push(score)) capture();
    }, SAMPLE_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [ready, capture]);

  useEffect(() => {
    const timer = setTimeout(() => setShowManualHint(true), HINT_AFTER_MS);
    return () => clearTimeout(timer);
  }, []);

  const guide = frameSize ? guideRectPercent(frameSize.width, frameSize.height) : null;

  return (
    <div className={s.idCameraStage}>
      <video ref={videoRef} playsInline muted className={s.idCameraVideo} />
      {guide && (
        <div
          className={s.idCameraGuide}
          aria-hidden="true"
          style={{
            left: `${guide.left}%`,
            top: `${guide.top}%`,
            width: `${guide.width}%`,
            height: `${guide.height}%`,
          }}
        />
      )}
      <canvas ref={sampleRef} width={SAMPLE_WIDTH} hidden />
      <canvas ref={shotRef} hidden />
      <p className={s.idCameraHint} role="status">
        신분증을 네모 안에 맞춰 주세요. 잘 맞으면 자동으로 찍혀요.
      </p>
      <button type="button" className={s.primary} disabled={busy} onClick={capture}>
        직접 찍기
      </button>
      {showManualHint && <p className={s.description}>잘 안 잡히면 “직접 찍기”를 눌러도 괜찮아요.</p>}
    </div>
  );
}
