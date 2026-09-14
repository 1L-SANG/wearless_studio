/* =============================================================
   IdDocumentStep — 간편인증(simple_auth) 경로 전용: 사용자가 신분증을 직접 촬영/선택하고,
   화면에서 주민등록번호 자리를 가린 뒤 올린다.
   왜 마스킹이 클라이언트 책임인가: 개인정보보호법상 주민등록번호는 법적 근거 없이 수집할
   수 없는데 신분증 사진엔 그 번호가 그대로 보인다. 서버는 업로드된 사진만 보고는 "정말
   가려졌는지" 검증할 수 없다 — 그래서 서버로 가기 전에 클라이언트가 픽셀을 실제로
   덮어써야 한다(아래 submit 의 buildMaskedBlob). 관리자 심사(Task 12)가 육안으로 다시
   확인하는 건 최후 방어선이지, 1차 방어선이 아니다.
   원본 File 은 pickFile 안에서 object URL 을 만드는 데 딱 한 번 쓰이고 버려진다 — 그 뒤로는
   어떤 state/ref 에도 원본이 남지 않는다. 서버로 가는 건 항상 buildMaskedBlob 이 캔버스에서
   뽑아낸 blob 뿐이다.
   훅 호출 순서 고정(테스트가 인덱스로 ref 를 직접 찌른다 — biometricEnrollment 테스트의
   portraitRef=refs[2] 관례와 같다): useState 7개(documentType, imageUrl, imageLoaded,
   maskRatio, maskedConfirmed, busy, localError) → useRef 3개(imageRef=refs[0],
   canvasRef=refs[1], dragRef=refs[2]). 이 순서를 바꾸면
   tests/frontend/facemarket-id-capture.test.mjs 가 깨진다.
   ============================================================= */
import { useCallback, useEffect, useRef, useState } from 'react';
import { uploadIdDocument } from '@/lib/api/facemarket.js';
import { Button, Icon } from '@/components/ui.jsx';
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

export default function IdDocumentStep({ enrollmentId, onUploaded, onError, onStale }) {
  const [documentType, setDocumentType] = useState(null);
  const [imageUrl, setImageUrl] = useState(null);
  const [imageLoaded, setImageLoaded] = useState(false);
  const [maskRatio, setMaskRatio] = useState(null);
  const [maskedConfirmed, setMaskedConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [localError, setLocalError] = useState('');

  // imageRef: drawImage 소스이자 naturalWidth/Height 출처. canvasRef: 화면에 안 보이는
  // 스크래치 캔버스 — submit 시점에만 실제로 그려지고 그 즉시 blob 으로 뽑힌다. dragRef:
  // 드래그 진행 상태(렌더를 유발하면 안 되므로 state 가 아니라 ref).
  const imageRef = useRef(null);
  const canvasRef = useRef(null);
  const dragRef = useRef(null);

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

  const startDrag = useCallback((mode) => (event) => {
    if (!maskRatio) return;
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget?.setPointerCapture?.(event.pointerId);
    dragRef.current = { mode, startX: event.clientX, startY: event.clientY, ratio: maskRatio };
  }, [maskRatio]);

  const endDrag = useCallback(() => { dragRef.current = null; }, []);

  const submit = useCallback(async () => {
    if (!enrollmentId || !documentType || !imageUrl || !maskRatio || !maskedConfirmed) return;
    const image = imageRef.current;
    const canvas = canvasRef.current;
    if (!image || !canvas) return;
    setBusy(true);
    setLocalError('');
    try {
      canvas.width = image.naturalWidth || image.width;
      canvas.height = image.naturalHeight || image.height;
      // maskRatio 는 화면 <img> **엘리먼트 박스** 기준 비율이다(드래그가
      // getBoundingClientRect 로 정규화하고 오버레이도 그 박스의 %로 앉는다). 그런데
      // .idPreviewImage 는 `object-fit: contain` + `max-height: 60vh` 라 세로로 긴 사진은
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
      await uploadIdDocument(enrollmentId, {
        file: maskedBlob,
        documentType,
        maskedConfirmed: true,
      });
      onUploaded?.();
    } catch (error) {
      // 409 = 이 등록은 더 이상 신분증을 받을 단계가 아니다(다른 탭에서 이미 올렸거나
      // 만료됐거나, 서버가 간편인증 경로를 껐다 — invalid_enrollment_state /
      // identity_method_unavailable). 이 스텝은 **성공으로만** 빠져나가므로 그냥 에러만
      // 띄우면 사용자는 재촬영만 반복하며 영원히 여기 갇힌다. 부모의 재조회 경로로
      // 되돌려 서버가 말하는 현재 단계로 보낸다(최종리뷰 I7).
      if (error?.status === 409) {
        onStale?.(error);
        return;
      }
      setLocalError(error?.message || '신분증 업로드에 실패했어요. 잠시 후 다시 시도해 주세요.');
      onError?.(error);
    } finally {
      setBusy(false);
    }
  }, [documentType, enrollmentId, imageUrl, maskRatio, maskedConfirmed, onError, onStale, onUploaded]);

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
        <p className={s.idNotice}>
          주민등록번호 뒷자리를 가린 뒤 올려 주세요. 신분증 사진은 본인 확인 심사에만 쓰고
          {' '}<strong>심사가 끝나면 바로 지웁니다.</strong> 원본은 서버로 전송되지 않습니다.
        </p>
      </div>

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
    </div>
  );
}
