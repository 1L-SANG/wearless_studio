/* =============================================================
   IdDocumentStep — 간편인증(simple_auth) 경로 전용: 신분증을 촬영/선택하고,
   주민등록번호 자리를 가린 뒤 올린다.
   왜 마스킹이 클라이언트 책임인가: 개인정보보호법상 주민등록번호는 법적 근거 없이 수집할
   수 없는데 신분증 사진엔 그 번호가 그대로 보인다. 서버는 업로드된 사진만 보고는 "정말
   가려졌는지" 검증할 수 없다 — 그래서 서버로 가기 전에 클라이언트가 픽셀을 실제로
   덮어써야 한다.

   세 가지 모드(mode) — 카메라가 기본이고, 나머지 둘은 카메라를 벗어나는 길이다.
     · camera — <IdCameraCapture>. 카드가 화면 가이드를 채우면 idCardGeometry 의 고정
       규격으로 주민등록번호 위치가 계산되고, IdCameraCapture 내부의 burnGuideMask 가
       그 자리를 실제로 칠한 뒤(fillRect) blob 하나만 onCaptured 로 돌려준다. 이미 칠해진
       채로 오므로 여기서는 미리보기도, "가렸어요" 확인 체크도 필요 없다 — 확인할 게
       없다(사람이 아니라 좌표 계산이 마스킹했다). file 로 내려가는 길은 셋이다:
       onUnavailable('permission'|'unsupported')(권한 거부·카메라 미지원), 3연속
       id_mask_not_applied(서버 거절), 그리고 switchToFile(사용자가 직접 누르는 버튼,
       최종리뷰 I2a — 카메라는 켜지지만 렌즈가 흐리거나 전면 카메라를 잡는 등 "작동은
       하지만 못 쓰겠는" 경우를 위한 것으로, shadow/off 에선 서버 거절 코드가 오지 않아
       이게 사실상 유일한 자발적 탈출구다).
     · file — v1 의 파일 선택 + 드래그 마스킹 화면 그대로다.
     · manual — 서버의 기하 검증(FM_ID_MASK_VERIFY, 지금은 shadow)이 id_mask_not_applied 를
       MANUAL_MASK_AFTER 회 연속 돌려주면 내려간다. file 과 화면은 동일하다(어차피 v1
       화면이 이미 드래그로 직접 옮기는 기능이다) — 다른 건 배너 문구뿐이다.
   file·manual 은 화면과 로직이 같다: 카드 종류가 늘어날 가능성을 열어 둔 v1 그대로,
   확인 체크가 있어야 제출할 수 있다(사람이 직접 마스킹 위치를 정했으니 사람이 확인해야
   한다). 두 모드로 나눈 건 "왜 여기 왔는지"를 사용자에게 다르게 설명하기 위해서다.

   id_mask_not_applied 연속 횟수는 useRef(maskFailStreakRef)로 센다 — 그 값 자체는 화면에
   아무것도 그리지 않으므로(횟수 배지 같은 걸 안 보여준다) state 로 만들 이유가 없다.
   렌더를 유발해야 하는 건 "3회째에 mode 가 바뀐다"는 결과뿐이다(dragRef 와 같은 이유).
   리마운트되면 이 ref 도 초기화된다 — 등록 자체가 다른 단계로 넘어갔거나 사용자가 이
   화면을 벗어났다 돌아온 경우이므로, 새로 3번을 다시 셀 여지를 주는 쪽이 더 안전하다
   (사용자가 실수로 예전 실패 횟수 때문에 곧장 수동 모드에 갇히면 안 된다).

   원본 File/Frame 은 이 컴포넌트의 어떤 state/ref 에도 남지 않는다. camera 경로는
   IdCameraCapture 가 이미 마스킹한 blob 만 넘기고, file·manual 경로는 pickFile 안에서
   object URL 을 만드는 데만 원본을 쓰고 그 뒤로는 buildMaskedBlob 이 캔버스에서 뽑은
   blob 만 쓴다.

   훅 호출 순서 고정(테스트가 인덱스로 state/ref 를 직접 찌른다):
     useState 10개: documentType(0) → imageUrl(1) → imageLoaded(2) → maskRatio(3) →
       maskedConfirmed(4) → busy(5) → localError(6) → mode(7) → cameraUnavailableReason(8) →
       autoPaused(9).
     useRef 4개: imageRef(0) → canvasRef(1) → dragRef(2) → maskFailStreakRef(3).
   앞의 7 state·3 ref는 v1 과 순서가 같다(기존 테스트가 그 인덱스를 그대로 쓴다) — 새로
   추가한 것들은 뒤에만 붙인다. 이 순서를 바꾸면 tests/frontend/facemarket-id-capture.test.mjs
   가 깨진다.

   autoPaused(최종리뷰 I2b): camera 모드 자동 촬영이 서버를 계속 두드리지 않게 막는
   일시정지 플래그다. 업로드가 실패하면(마스킹 실패든 qc_unavailable 같은 일시 오류든)
   true 가 되어 IdCameraCapture 의 ~10fps 판정 루프를 멈춘다 — 안 멈추면 사용자가 카드를
   계속 들고 있는 것만으로 ~1초마다 얼굴 인식(YuNet+SFace)이 서버에서 다시 돈다
   (2026-08-26 이벤트루프 정지의 부하 프로파일과 같다). 수동 셔터는 이 플래그를 보지
   않는다(disabled 는 여전히 busy 로만 결정) — 자동은 편의이지 관문이 아니라는 원칙이라,
   막힌 건 자동뿐이어야 한다. 새 시도가 시작되면(자동이든 수동이든 uploadMasked 진입
   시점에) 다시 false 로 풀린다 — "사용자가 뭔가 했다"는 신호이기 때문이다.
   ============================================================= */
import { useCallback, useEffect, useRef, useState } from 'react';
import { uploadIdDocument } from '@/lib/api/facemarket.js';
import { Button, Icon } from '@/components/ui.jsx';
import IdCameraCapture from './IdCameraCapture.jsx';
import {
  ID_DOCUMENT_TYPES,
  buildMaskedBlob,
  clampMaskRatio,
  defaultMaskRatio,
  elementRatioToImagePixels,
} from './idDocumentMasking.js';
import s from './ModelRegister.module.css';

// 서버가 받는 형식과 같은 집합이어야 한다(facemarket_id_document.ALLOWED_ID_MIME).
// image/* 로 열어두면 맥 사진앱 기본인 HEIC 까지 통과하는데, 브라우저는 HEIC 를 <img> 로
// 못 그린다 — 미리보기가 깨진 채 아무 안내 없이 제출 버튼만 잠긴다(2026-09-14 프로덕션).
const ACCEPTED_IMAGE_TYPES = ['image/jpeg', 'image/png', 'image/webp'];
const ACCEPT_ATTR = ACCEPTED_IMAGE_TYPES.join(',');

// v1 은 이 하나뿐이다(idDocumentMasking.ID_DOCUMENT_TYPES) — camera 모드는 종류를 고를
// 화면이 없으므로(카드 규격 자체가 이 종류를 전제한다) 이 값을 그대로 쓴다.
const AUTO_DOCUMENT_TYPE = ID_DOCUMENT_TYPES[0].value;

// 기하 검증(FM_ID_MASK_VERIFY)이 id_mask_not_applied 를 이 횟수만큼 연속으로 돌려주면
// 자동/드래그 어느 쪽이든 계속 실패하고 있다는 뜻이다 — 계속 재촬영만 시키면 사용자가
// 갇힌다(최종리뷰 I7 과 같은 성격의 문제). 세 번째에 드래그로 직접 옮기는 v1 화면으로
// 내려준다.
const MANUAL_MASK_AFTER = 3;

const CAMERA_UNAVAILABLE_MESSAGES = {
  permission: '카메라 권한이 없어서 촬영할 수 없어요. 사진을 선택해서 올려 주세요.',
  unsupported: '이 브라우저에서는 카메라 촬영을 지원하지 않아요. 사진을 선택해서 올려 주세요.',
  // getUserMedia 는 성공했지만 사용자가 스스로 파일 선택으로 옮긴 경우(최종리뷰 I2a) —
  // "권한이 없어서"·"지원하지 않아서" 라고 하면 거짓 원인을 알려주는 셈이라 따로 둔다.
  choice: '사진을 선택해서 올려 주세요.',
};
const MANUAL_FALLBACK_MESSAGE = '자동 마스킹이 계속 인식되지 않았어요. 사진 위에서 가릴 위치를 직접 옮겨 주세요.';

export default function IdDocumentStep({ enrollmentId, onUploaded, onError, onStale }) {
  const [documentType, setDocumentType] = useState(null);
  const [imageUrl, setImageUrl] = useState(null);
  const [imageLoaded, setImageLoaded] = useState(false);
  const [maskRatio, setMaskRatio] = useState(null);
  const [maskedConfirmed, setMaskedConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [localError, setLocalError] = useState('');
  const [mode, setMode] = useState('camera');
  const [cameraUnavailableReason, setCameraUnavailableReason] = useState(null);
  // 자동 촬영 일시정지(최종리뷰 I2b) — 위 파일 top 주석 참고.
  const [autoPaused, setAutoPaused] = useState(false);

  // imageRef: drawImage 소스이자 naturalWidth/Height 출처(file·manual 전용). canvasRef:
  // 화면에 안 보이는 스크래치 캔버스 — submit 시점에만 실제로 그려지고 그 즉시 blob 으로
  // 뽑힌다. dragRef: 드래그 진행 상태(렌더를 유발하면 안 되므로 state 가 아니라 ref).
  // maskFailStreakRef: id_mask_not_applied 연속 횟수 — 위 파일 top 주석 참고.
  const imageRef = useRef(null);
  const canvasRef = useRef(null);
  const dragRef = useRef(null);
  const maskFailStreakRef = useRef(0);

  // 새 사진을 고르면 그 시점의 종류로 기본 마스킹 박스를 다시 잡고 확인 체크를 되돌린다 —
  // 이전 사진에서 확인했다는 사실이 새 사진에 이어붙으면, 마스킹 안 된 새 사진이 확인된
  // 것처럼 올라갈 수 있다.
  useEffect(() => {
    if (!documentType || !imageUrl) return;
    setMaskRatio(defaultMaskRatio(documentType));
    setMaskedConfirmed(false);
  }, [documentType, imageUrl]);

  // object URL 은 이 컴포넌트가 들고 있는 동안만 필요하다 — 새 사진으로 바뀌거나
  // 언마운트되면 이전 것을 해제한다.
  useEffect(() => {
    if (!imageUrl) return undefined;
    return () => URL.revokeObjectURL(imageUrl);
  }, [imageUrl]);

  const pickFile = useCallback((file) => {
    if (!file) return;
    // accept 는 브라우저마다 무시될 수 있고 드래그드롭도 뚫린다 — 여기서 한 번 더 막는다.
    // 서버까지 갔다 415 로 돌아오는 것보다 고른 즉시 알려주는 편이 낫다.
    if (file.type && !ACCEPTED_IMAGE_TYPES.includes(file.type)) {
      setLocalError('JPG · PNG · WebP 만 올릴 수 있어요. 아이폰 사진(HEIC)이면 JPG 로 바꿔 주세요.');
      return;
    }
    setLocalError('');
    setImageLoaded(false);
    // file 은 여기서 object URL 을 만드는 데만 쓰이고 이 함수를 벗어나지 않는다 — 어떤
    // state/ref 로도 원본 File 을 넘기지 않는다. 서버로 가는 건 항상 submit 이 캔버스에서
    // 새로 뽑아내는 마스킹된 blob 뿐이다.
    setImageUrl(URL.createObjectURL(file));
  }, []);

  const retake = useCallback(() => {
    setImageUrl(null);
    setImageLoaded(false);
    setMaskRatio(null);
    setMaskedConfirmed(false);
  }, []);

  const moveMask = useCallback((event) => {
    const drag = dragRef.current;
    const stage = imageRef.current;
    if (!drag || !stage) return;
    const rect = stage.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const dxr = (event.clientX - drag.startX) / rect.width;
    const dyr = (event.clientY - drag.startY) / rect.height;
    setMaskRatio(drag.mode === 'resize'
      ? clampMaskRatio({ ...drag.ratio, wr: drag.ratio.wr + dxr, hr: drag.ratio.hr + dyr })
      : clampMaskRatio({ ...drag.ratio, xr: drag.ratio.xr + dxr, yr: drag.ratio.yr + dyr }));
  }, []);

  const startDrag = useCallback((mode2) => (event) => {
    if (!maskRatio) return;
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget?.setPointerCapture?.(event.pointerId);
    dragRef.current = { mode: mode2, startX: event.clientX, startY: event.clientY, ratio: maskRatio };
  }, [maskRatio]);

  const endDrag = useCallback(() => { dragRef.current = null; }, []);

  // camera·file·manual 세 경로가 전부 여기로 모인다 — 서버 에러 코드별 분기(409/
  // id_mask_not_applied/그 외)를 한 곳에서만 관리해야 세 경로가 서로 다르게 반응하는
  // 사고를 막을 수 있다. blob 은 두 경우 다 "이미 마스킹된" 상태로 들어온다(camera 는
  // burnGuideMask 가, file·manual 은 아래 submit 의 buildMaskedBlob 이 채운다) — 여기서는
  // 그 결과만 올린다.
  const uploadMasked = useCallback(async (blob, docType) => {
    setLocalError('');
    // 새 시도가 시작됐다 — 자동이든(게이트가 열려 있었다는 뜻) 수동이든(사용자가 직접
    // 셔터를 눌렀다는 뜻) "사용자가 뭔가 했다"는 신호이므로 이전 실패로 걸린 일시정지를
    // 먼저 푼다(최종리뷰 I2b). 이번 시도도 실패하면 아래 catch 가 다시 세운다.
    setAutoPaused(false);
    // burnGuideMask 도 buildMaskedBlob 도 canvas.toBlob(resolve, …) 으로 끝난다 — toBlob 은
    // 인코더가 실패하면 reject 가 아니라 null 을 resolve 한다(MDN 명세). null 을 그대로
    // 올리면 이 시도 자체가 낭비되고 서버는 "빈 파일"이라는 알아듣기 힘든 에러만 돌려준다.
    if (!blob || !blob.size) {
      setLocalError('사진을 저장하지 못했어요. 다시 찍어 주세요.');
      return;
    }
    if (!enrollmentId) return;
    setBusy(true);
    try {
      await uploadIdDocument(enrollmentId, {
        file: blob,
        documentType: docType,
        maskedConfirmed: true,
      });
      maskFailStreakRef.current = 0;
      onUploaded?.();
    } catch (error) {
      // 409 = 이 등록은 더 이상 신분증을 받을 단계가 아니다(다른 탭에서 이미 올렸거나
      // 만료됐거나, 서버가 간편인증 경로를 껐다 — invalid_enrollment_state /
      // identity_method_unavailable). 이 스텝은 **성공으로만** 빠져나가므로 그냥 에러만
      // 띄우면 사용자는 재촬영만 반복하며 영원히 여기 갇힌다. 부모의 재조회 경로로
      // 되돌려 서버가 말하는 현재 단계로 보낸다(최종리뷰 I7). 스트라이크에도 안 센다 —
      // 이건 마스킹 실패가 아니라 이 화면 자체가 더 이상 유효하지 않다는 뜻이다.
      if (error?.status === 409) {
        onStale?.(error);
        return;
      }
      if (error?.code === 'id_mask_not_applied') {
        // 서버 기하 검증(FM_ID_MASK_VERIFY, 지금은 shadow)이 마스킹이 안 덮였다고 본
        // 것 — 자동이든 드래그든 계속 이 자리에서 실패하면 재촬영만 반복시키게 된다.
        // 연속 MANUAL_MASK_AFTER 번째엔 드래그로 직접 옮기는 v1 화면으로 내려준다.
        maskFailStreakRef.current += 1;
        if (maskFailStreakRef.current >= MANUAL_MASK_AFTER) setMode('manual');
      }
      // 그 외 코드(unsupported_type·file_too_large·empty_upload·storage_unavailable·
      // qc_unavailable·얼굴 인식 사유 등)는 마스킹 문제가 아니므로 스트라이크에 안 세고
      // 메시지만 보여준다 — 재촬영한다고 나아지지 않는 실패를 마스킹 실패로 취급하면
      // 엉뚱하게 수동 모드로 떨어진다.
      //
      // 409(위에서 이미 return)를 뺀 모든 실패에서 자동 촬영을 멈춘다(최종리뷰 I2b) —
      // 마스킹 실패든 storage_unavailable·qc_unavailable 같은 일시 오류든, 사용자가
      // 에러 문구를 읽는 동안에도 카드를 든 손은 그대로라 자동 판정 게이트가 ~1초
      // 안에 다시 열린다. 그대로 두면 실패마다 얼굴 인식(YuNet+SFace)이 서버에서 계속
      // 돈다(2026-08-26 이벤트루프 정지와 같은 부하 프로파일). 수동 셔터는 이 플래그의
      // 영향을 받지 않는다 — 자동만 멈추고 수동은 항상 살아 있어야 한다.
      setAutoPaused(true);
      setLocalError(error?.message || '신분증 업로드에 실패했어요. 잠시 후 다시 시도해 주세요.');
      onError?.(error);
    } finally {
      setBusy(false);
    }
  }, [enrollmentId, onError, onStale, onUploaded]);

  // camera 모드: IdCameraCapture 가 이미 마스킹까지 끝낸 blob 하나만 준다 — 여기서는
  // 그대로 올릴 뿐이다. maskedConfirmed 는 true 로 고정한다: 마스킹은 사람이 드래그로
  // "가렸어요" 라고 체크한 게 아니라 카드 규격 좌표로 계산해 태운 것이라, 체크박스로
  // 재확인시키는 게 오히려 사람이 안 했다는 사실을 감추는 셈이 된다(더 강한 보증이다).
  const handleCameraCaptured = useCallback((blob) => uploadMasked(blob, AUTO_DOCUMENT_TYPE), [uploadMasked]);

  // 권한 거부·미지원 — 카메라 자체를 못 쓴다. file 화면(v1)으로 내려준다. reason 은 배너
  // 문구를 고르는 데만 쓴다(그 외 동작 차이는 없다 — 둘 다 결국 "사진을 선택해 주세요").
  const handleCameraUnavailable = useCallback((reason) => {
    setCameraUnavailableReason(reason);
    setMode('file');
  }, []);

  // 카메라가 멀쩡히 켜져도 결과물을 못 쓰는 경우가 있다 — 렌즈 얼룩, 기기가 후면
  // facingMode 힌트를 무시하고 전면 카메라를 잡는 경우, 이미 스캔본을 갖고 있는 경우
  // (최종리뷰 I2a). 지금까지 file 모드로 가는 길은 handleCameraUnavailable(기술적
  // 실패) 아니면 3연속 id_mask_not_applied(shadow/off 에선 서버가 절대 안 준다) 뿐이라,
  // 카메라가 "작동은 하지만 못 쓰겠는" 사용자에겐 사실상 탈출구가 없었다. 사용자가
  // 직접 파일 선택으로 넘어갈 수 있는 버튼을 하나 둔다.
  const switchToFile = useCallback(() => {
    setCameraUnavailableReason('choice');
    setMode('file');
  }, []);

  const submit = useCallback(async () => {
    if (!enrollmentId || !documentType || !imageUrl || !maskRatio || !maskedConfirmed) return;
    const image = imageRef.current;
    const canvas = canvasRef.current;
    if (!image || !canvas) return;
    canvas.width = image.naturalWidth || image.width;
    canvas.height = image.naturalHeight || image.height;
    // maskRatio 는 화면 <img> **엘리먼트 박스** 기준 비율이다(드래그가
    // getBoundingClientRect 로 정규화하고 오버레이도 그 박스의 %로 앉는다). 그런데
    // .idPreviewImage 는 `object-fit: contain` + `max-height: 60dvh` 라 세로로 긴 사진은
    // 레터박스된다 — 그때 엘리먼트 박스 비율을 그대로 자연 픽셀에 곱하면 마스크가 엉뚱한
    // 곳에 찍히고 주민등록번호가 그대로 올라간다(최종리뷰 C3). 실제로 그려진 내용
    // 영역(contain 사각형)을 거쳐 자연 좌표로 옮긴다.
    const rect = image.getBoundingClientRect?.() || null;
    const pixelMask = elementRatioToImagePixels(
      maskRatio,
      rect ? { width: rect.width, height: rect.height } : null,
      { width: canvas.width, height: canvas.height },
    );
    if (!pixelMask) {
      // 변환 결과가 이미지 밖이면 칠할 게 없다 — 원본 그대로 올리면 안 된다.
      setLocalError('마스킹 영역이 사진 밖에 있어요. 박스를 사진 위로 옮겨 주세요.');
      return;
    }
    // 전송 전 캔버스에 실제로 채운다 — 이 blob 만 서버로 간다. 원본 File 은 pickFile 에서
    // 이미 버려졌으므로 여기 등장조차 하지 않는다.
    const maskedBlob = await buildMaskedBlob({ canvas, image, mask: pixelMask });
    await uploadMasked(maskedBlob, documentType);
  }, [documentType, enrollmentId, imageUrl, maskRatio, maskedConfirmed, uploadMasked]);

  const canSubmit = Boolean(
    documentType && imageUrl && imageLoaded && maskRatio && maskedConfirmed && !busy,
  );

  return (
    <div className="surface">
      <div className={s.stepHead}>
        <div className={s.medallion}><Icon name="image" size={22} /></div>
        <div>
          <div className={s.stepEyebrow}>본인 확인</div>
          <h2 className={s.stateTitle}>신분증을 찍어 올려 주세요</h2>
        </div>
      </div>

      <div className={s.purposeNotice}>
        <div className={s.purposeNoticeHead}><Icon name="info" size={15} /> 꼭 확인해 주세요</div>
        {mode === 'camera' ? (
          <p className={s.idNotice}>
            가이드 안에 맞추면 주민등록번호가 자동으로 가려져요. 가린 사진만 전송하며,
            본인확인 <strong>심사가 끝나면 바로 지워요.</strong>
          </p>
        ) : (
          <p className={s.idNotice}>
            주민등록번호 뒷자리를 가려 주세요. 가린 사진만 전송하며,
            본인확인 <strong>심사가 끝나면 바로 지워요.</strong>
          </p>
        )}
      </div>

      {mode === 'camera' ? (
        <>
          {/* onCaptured 가 받는 blob 은 IdCameraCapture 내부에서 burnGuideMask 가 이미
              drawImage → fillRect(주민번호 자리) → canvas.toBlob 순서로 만들어 낸 것이다 —
              여기서는 마스킹 로직을 다시 부를 필요가 없다. busy 를 넘겨야 업로드 중에
              자동 판정기·수동 셔터가 또 찍지 않는다. */}
          <IdCameraCapture
            onCaptured={handleCameraCaptured}
            onUnavailable={handleCameraUnavailable}
            busy={busy}
            paused={autoPaused}
          />
          {localError && <p className={s.error} role="alert"><Icon name="alertCircle" size={15} /> {localError}</p>}
          {/* 카메라가 작동은 하지만 못 쓰겠는 사용자를 위한 탈출구(최종리뷰 I2a) —
              지금까지는 getUserMedia 실패나 3연속 서버 거절(shadow/off 에선 오지 않는다)
              뿐이라 사실상 닫혀 있었다. */}
          <button type="button" className={s.backLink} disabled={busy} onClick={switchToFile}>
            사진을 선택해서 올릴게요
          </button>
        </>
      ) : (
        <>
          <p className={s.description} role="status">
            {mode === 'manual'
              ? MANUAL_FALLBACK_MESSAGE
              : (CAMERA_UNAVAILABLE_MESSAGES[cameraUnavailableReason] || CAMERA_UNAVAILABLE_MESSAGES.unsupported)}
          </p>

          <div className={s.physiqueGroup}>
            <div className={s.physiqueLabel}>신분증 종류</div>
            <div className="chips">
              {ID_DOCUMENT_TYPES.map((doc) => (
                <button
                  key={doc.value}
                  type="button"
                  className={`chip${documentType === doc.value ? ' on' : ''}`}
                  disabled={busy}
                  onClick={() => setDocumentType(doc.value)}
                >
                  {doc.label}
                </button>
              ))}
            </div>
          </div>

          {!imageUrl ? (
            <label className={s.uploadZone}>
              <input
                type="file"
                accept={ACCEPT_ATTR}
                capture="environment"
                disabled={!documentType || busy}
                className={s.uploadInput}
                onChange={(event) => { pickFile(event.target.files?.[0]); event.target.value = ''; }}
              />
              <div className={s.uploadIcon}><Icon name="imagePlus" size={20} /></div>
              <div className={s.uploadText}>신분증 촬영 또는 사진 선택</div>
              <div className={s.uploadHint}>{documentType ? 'JPG · PNG' : '먼저 신분증 종류를 골라 주세요'}</div>
            </label>
          ) : (
            <>
              <div className={s.idImageStage}>
                <img
                  ref={imageRef}
                  src={imageUrl}
                  alt="촬영한 신분증"
                  className={s.idPreviewImage}
                  onLoad={() => setImageLoaded(true)}
                  // 로드 실패를 삼키면 사용자는 깨진 띠만 보고 제출 버튼이 왜 잠겼는지 모른다.
                  onError={() => {
                    setImageLoaded(false);
                    setLocalError('이 사진은 화면에 표시할 수 없어요. JPG 나 PNG 로 다시 올려 주세요.');
                  }}
                />
                {maskRatio && (
                  <div
                    className={s.idMaskBox}
                    style={{
                      left: `${maskRatio.xr * 100}%`,
                      top: `${maskRatio.yr * 100}%`,
                      width: `${maskRatio.wr * 100}%`,
                      height: `${maskRatio.hr * 100}%`,
                    }}
                    onPointerDown={startDrag('move')}
                    onPointerMove={moveMask}
                    onPointerUp={endDrag}
                    onPointerCancel={endDrag}
                  >
                    <span className={s.idMaskLabel}>이 안을 가려요 — 드래그로 옮기고 조절하세요</span>
                    <span
                      className={s.idMaskHandle}
                      onPointerDown={startDrag('resize')}
                      onPointerMove={moveMask}
                      onPointerUp={endDrag}
                      onPointerCancel={endDrag}
                    />
                  </div>
                )}
              </div>
              {/* 화면엔 안 보인다 — submit 시점에만 실제로 그려지고 그 결과(blob)만 쓰인다. */}
              <canvas ref={canvasRef} className={s.idHiddenCanvas} aria-hidden="true" />
              <button type="button" className={s.backLink} disabled={busy} onClick={retake}>
                다른 사진으로 다시 찍기
              </button>
              <label className={s.consentCheck}>
                <input
                  type="checkbox"
                  checked={maskedConfirmed}
                  disabled={busy || !imageLoaded}
                  onChange={(event) => setMaskedConfirmed(event.target.checked)}
                />
                주민등록번호 뒷자리를 가렸어요
              </label>
              {localError && <p className={s.error} role="alert"><Icon name="alertCircle" size={15} /> {localError}</p>}
              <Button variant="primary" block disabled={!canSubmit} onClick={submit}>
                {busy ? '업로드 중…' : '이 사진으로 확인 요청'}
              </Button>
            </>
          )}
        </>
      )}
    </div>
  );
}
