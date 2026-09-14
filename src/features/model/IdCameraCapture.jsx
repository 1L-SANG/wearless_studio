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
   에서 뽑는다.

   그 퍼센트는 "가장 가까운 position 조상"을 기준으로 해석되므로, 비디오와 가이드를
   .idCameraViewport 전용 래퍼 안에 딱 둘만 담는다 — 힌트·에러 문구·셔터 버튼처럼
   높이가 바뀌는 형제를 같은 조상에 같이 두면, 그 형제의 유무로 조상 높이가 바뀔
   때마다 가이드가 비디오와 어긋난다(리뷰에서 실측: 15초 힌트가 뜨는 순간 가이드가
   점프한다). 이 래퍼의 렌더 크기(아래 viewportStyle)도 항상 프레임 비율 그대로
   줄고 늘어야 한다 — object-fit: cover 로 잘라내면 이 화면에 보이는 비디오와
   가이드 좌표계가 어긋나므로 절대 쓰지 않는다. */
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
  const busyRef = useRef(busy);   // 부모가 업로드 중이면(=busy) 판정 루프도 셔터를 눌러선 안 된다
  const [ready, setReady] = useState(false);
  const [frameSize, setFrameSize] = useState(null);   // { width, height } — 오버레이 좌표계
  const [showManualHint, setShowManualHint] = useState(false);
  const [captureError, setCaptureError] = useState(false);

  // onCaptured/onUnavailable/busy 를 ref 로 미러링한다 — 이 값들의 정체성에 카메라
  // 마운트 주기(아래 effect)나 capture() 의 정체성을 걸면 안 된다. 부모가 매 렌더마다
  // 새 함수를 넘기는 흔한 실수 하나로 카메라가 매번 껐다 켜진다(트랙 정지 → 재권한
  // 요청 → 화면 깜빡임).
  const onCapturedRef = useRef(onCaptured);
  useEffect(() => { onCapturedRef.current = onCaptured; });
  const onUnavailableRef = useRef(onUnavailable);
  useEffect(() => { onUnavailableRef.current = onUnavailable; });
  useEffect(() => { busyRef.current = busy; });

  // 카메라 시작 + 언마운트에서 트랙 정지(안 하면 카메라 표시등이 계속 켜져 있다)
  // 의존성 배열을 비워 둔다 — 카메라는 마운트당 한 번만 잡고, onUnavailable 의
  // 정체성이 바뀌어도(부모 리렌더) 다시 잡지 않는다(위 ref 미러링 참조).
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
        // 권한 프롬프트가 떠 있는 동안 언마운트되면 그 이후의 거부/실패는 이미 버려진
        // effect 인스턴스의 것이다 — 여기서도 cancelled 를 봐야 다른 부모에게 잘못
        // onUnavailable 이 불리지 않는다.
        if (cancelled) return;
        onUnavailableRef.current?.(error?.name === 'NotAllowedError' ? 'permission' : 'unsupported');
      }
    })();
    return () => {
      cancelled = true;
      video?.removeEventListener('loadedmetadata', captureFrameSize);
      streamRef.current?.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    };
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  // 실제 프레임 픽셀(videoWidth/videoHeight)로만 찍는다 — CSS 표시 크기로 찍으면
  // burnGuideMask 가 계산하는 주민번호 사각형이 실제 카드 위치와 어긋난다.
  // 의존성을 비워 둔다: onCaptured 는 ref 로만 읽어서, 부모가 매 렌더마다 새
  // 함수를 넘겨도 이 콜백과 그걸 물고 있는 판정 루프 effect 가 다시 돌지 않는다.
  const capture = useCallback(() => {
    const video = videoRef.current;
    const canvas = shotRef.current;
    if (!video || !canvas || capturingRef.current || busyRef.current) return;
    const width = video.videoWidth;
    const height = video.videoHeight;
    if (!width || !height) return;
    capturingRef.current = true;
    gateRef.current.reset();   // 게이트는 레벨 트리거라 리셋 안 하면 다음 프레임에도 계속 찍힌다
    setCaptureError(false);
    try {
      // burnGuideMask 는 Promise 를 만들기 전에 canvas.width 대입·drawImage 를 동기로
      // 실행한다. 거기서 던지면 이 호출 자체가 예외를 던지고 .then/.finally 에는
      // 닿지 못한다 — try/catch 로 감싸지 않으면 capturingRef 가 영영 true 로 남아
      // 자동 판정도, 수동 셔터도 다시는 못 찍는다.
      burnGuideMask(canvas, video, width, height)
        .then((blob) => {
          if (!mountedRef.current) return;
          onCapturedRef.current?.(blob);
        })
        .catch(() => {
          if (mountedRef.current) setCaptureError(true);
        })
        .finally(() => { capturingRef.current = false; });
    } catch {
      capturingRef.current = false;
      if (mountedRef.current) setCaptureError(true);
    }
  }, []);

  // ~10fps 판정 루프: video → 작은 캔버스 → 그레이스케일 → scoreFrame → gate
  useEffect(() => {
    if (!ready) return undefined;
    const video = videoRef.current;
    const sampleCanvas = sampleRef.current;
    if (!video || !sampleCanvas) return undefined;
    const ctx = sampleCanvas.getContext('2d', { willReadFrequently: true });
    const timer = setInterval(() => {
      if (capturingRef.current || busyRef.current) return;   // 업로드 중엔 자동 셔터도 쉰다
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
  // 뷰포트(비디오+가이드 전용 래퍼)의 렌더 크기를 프레임 비율 그대로 계산한다.
  // width 는 "60dvh 높이를 내는 너비"와 "부모 100%" 중 작은 쪽 — 세로가 짧은
  // 화면(가로로 눕힌 폰)에서 60dvh 가 이기면 너비도 같은 비율만큼 줄어든다.
  // object-fit 없이(=자르지 않고) 원본 비율을 지키므로 비디오는 항상 이 박스를
  // 정확히 채우고, 가이드의 퍼센트 좌표도 항상 비디오의 실제 렌더 박스와 같다.
  const viewportStyle = frameSize
    ? {
        aspectRatio: `${frameSize.width} / ${frameSize.height}`,
        width: `min(100%, 60dvh * ${(frameSize.width / frameSize.height).toFixed(4)})`,
      }
    : undefined;

  return (
    <div className={s.idCameraStage}>
      <div className={s.idCameraViewport} style={viewportStyle}>
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
      </div>
      <canvas ref={sampleRef} width={SAMPLE_WIDTH} hidden />
      <canvas ref={shotRef} hidden />
      <p className={s.idCameraHint} role="status">
        신분증을 네모 안에 맞춰 주세요. 잘 맞으면 자동으로 찍혀요.
      </p>
      {captureError && (
        <p className={s.error} role="alert">사진을 저장하지 못했어요. 다시 찍어 주세요.</p>
      )}
      <button type="button" className={s.primary} disabled={busy} onClick={capture}>
        직접 찍기
      </button>
      {showManualHint && <p className={s.description}>잘 안 잡히면 “직접 찍기”를 눌러도 괜찮아요.</p>}
    </div>
  );
}
