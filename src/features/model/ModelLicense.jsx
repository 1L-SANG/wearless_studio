/* =============================================================
   features/model — 얼굴 라이선스 (/model/license)
   검증 모델이 얼굴 + 사용 조건을 라이선스로 등록한다. 얼굴은 비공개 R2 에만
   저장되고, 목록 카드의 얼굴은 인증 게이트(fetchLicenseFaceUrl)로만 받아 표시한다
   (<img src> 로 공개 URL 을 만들지 않는다 — 생체 PII 보호).
   ============================================================= */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { Button, Chips, Field, Icon, useToast } from '@/components/ui.jsx';
import { createLicense, fetchLicenseFaceUrl, listLicenses, revokeLicense } from '@/lib/api/facemarket.js';
import s from './ModelLicense.module.css';

// 브랜드 유형(카테고리) 기준 — 모델이 자기 얼굴이 쓰일 브랜드 종류를 허용/금지로 통제.
const ALLOWED_PRESETS = ['일반 여성 의류', '남성 의류', '캐주얼·스트릿', '스포츠·애슬레저', '뷰티·화장품', '액세서리·잡화'];
const FORBIDDEN_PRESETS = ['속옷·란제리', '수영복·비키니', '성인용품', '주류·담배', '의료·성형', '정치·종교'];
const VALIDITY = [
  { value: 90, label: '90일' },
  { value: 365, label: '1년' },
  { value: 730, label: '2년' },
];

const won = (n) => `₩${Number(n || 0).toLocaleString('ko-KR')}`;
const fmtDate = (iso) => { try { return new Date(iso).toLocaleDateString('ko-KR'); } catch { return iso; } };

// 라이선스 카드 — 자기 얼굴을 게이트로 인증해 objectURL 로 표시(언마운트 시 해제).
// 활성 라이선스는 '라이선스 해지'로 폐기 가능 — 해지 즉시 셀러의 생성 게이트가 차단된다(장면⑤).
function LicenseCard({ license, onRevoked, push }) {
  const [faceUrl, setFaceUrl] = useState(null);
  const [revoking, setRevoking] = useState(false);
  useEffect(() => {
    let url;
    let alive = true;
    fetchLicenseFaceUrl(license.faceImageUri)
      .then((u) => {
        // 언마운트가 fetch 완료보다 먼저면 여기서 바로 해제(cleanup 은 url 을 못 봐서 누수됨).
        if (!alive) { URL.revokeObjectURL(u); return; }
        url = u;
        setFaceUrl(u);
      })
      .catch(() => { /* 표시 실패 — 플레이스홀더 유지 */ });
    return () => { alive = false; if (url) URL.revokeObjectURL(url); };
  }, [license.faceImageUri]);

  const expired = license.status === 'expired'
    || (license.licenseValidUntil && new Date(license.licenseValidUntil) <= new Date());
  const statusLabel = license.status === 'revoked' ? '해지됨' : expired ? '만료' : '활성';
  const statusCls = license.status === 'active' && !expired ? s.stActive : s.stOff;
  const isActive = license.status === 'active' && !expired;

  const onRevoke = async () => {
    // 해지는 되돌릴 수 없는 표준 조치 — 셀러가 더는 이 얼굴을 쓸 수 없게 된다. 오조작 방지로 확인받는다.
    if (!window.confirm('이 라이선스를 해지하면 셀러가 더 이상 사용할 수 없어요. 해지할까요?')) return;
    setRevoking(true);
    try {
      await revokeLicense(license.id);
      push?.('라이선스를 해지했어요.', { icon: 'check' });
      onRevoked?.();
    } catch (e) {
      push?.(e.message || '라이선스 해지에 실패했어요.', { icon: 'alertCircle' });
    } finally {
      setRevoking(false);
    }
  };

  return (
    <div className={s.card}>
      <div className={s.thumb}>
        {faceUrl
          ? <img src={faceUrl} alt="라이선스 얼굴" />
          : <div className={s.thumbEmpty}><Icon name="person" size={26} /></div>}
        <span className={`${s.status} ${statusCls}`}>{statusLabel}</span>
      </div>
      <div className={s.cardBody}>
        <div className={s.price}>{won(license.unitPrice)}<span>/건</span></div>
        {license.allowedUse?.length > 0 && (
          <div className={s.badges}>
            {license.allowedUse.map((u) => <span key={u} className={`${s.badge} ${s.bAllow}`}>{u}</span>)}
          </div>
        )}
        {license.forbiddenUse?.length > 0 && (
          <div className={s.badges}>
            {license.forbiddenUse.map((u) => <span key={u} className={`${s.badge} ${s.bDeny}`}><Icon name="ban" size={11} />{u}</span>)}
          </div>
        )}
        <div className={s.meta}>
          <Icon name="lock" size={12} /> 유효기간 {fmtDate(license.licenseValidUntil)}까지
        </div>
        {isActive && (
          <button type="button" className={s.revoke} onClick={onRevoke} disabled={revoking}>
            {revoking ? '해지 중…' : '라이선스 해지'}
          </button>
        )}
      </div>
    </div>
  );
}

export function ModelLicense() {
  const { push } = useToast();  // 안정 useCallback 만 구조분해 — 불안정한 toast 객체 의존 배제(리로드 루프 방지)
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState(null);
  const [allowed, setAllowed] = useState([ALLOWED_PRESETS[0]]);
  const [forbidden, setForbidden] = useState([FORBIDDEN_PRESETS[0]]);
  const [unitPrice, setUnitPrice] = useState(10000);
  const [validDays, setValidDays] = useState(365);
  const [submitting, setSubmitting] = useState(false);

  const [licenses, setLicenses] = useState([]);
  const [loading, setLoading] = useState(true);
  const fileInput = useRef(null);

  const reload = useCallback(async () => {
    setLoading(true);
    try { setLicenses(await listLicenses()); }
    catch (e) { push(e.message, { icon: 'alertCircle' }); }
    finally { setLoading(false); }
  }, [push]);

  useEffect(() => { reload(); }, [reload]);
  // 미리보기 objectURL 정리
  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview); }, [preview]);

  const onPick = (e) => {
    const f = e.target.files?.[0];
    if (!f) return;
    if (!f.type.startsWith('image/')) { push('이미지 파일만 올릴 수 있어요.', { icon: 'alertCircle' }); return; }
    if (preview) URL.revokeObjectURL(preview);
    setFile(f);
    setPreview(URL.createObjectURL(f));
  };

  const onSubmit = async () => {
    if (!file) { push('얼굴 이미지를 올려 주세요.', { icon: 'alertCircle' }); return; }
    setSubmitting(true);
    try {
      await createLicense({
        faceBlob: file, filename: file.name,
        allowedUse: allowed, forbiddenUse: forbidden,
        unitPrice: Number(unitPrice) || 0, validDays,
      });
      push('라이선스가 등록됐어요.', { icon: 'check' });
      if (preview) URL.revokeObjectURL(preview);
      setFile(null); setPreview(null);
      if (fileInput.current) fileInput.current.value = '';
      await reload();
    } catch (e) {
      push(e.message, { icon: 'alertCircle' });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="wizard narrow">
      <div className="page-head">
        <h1>얼굴 라이선스</h1>
        <p>얼굴과 사용 조건을 등록하면 셀러가 상세페이지 제작에 사용할 수 있어요.</p>
      </div>

      <div className="surface">
        <div className={s.sectionLabel}>라이선스 얼굴</div>
        <button type="button" className={`${s.upload}${preview ? ' ' + s.uploadHas : ''}`}
          onClick={() => fileInput.current?.click()}>
          {preview
            ? <img src={preview} alt="미리보기" className={s.uploadImg} />
            : (
              <div className={s.uploadEmpty}>
                <Icon name="upload" size={22} />
                <span>얼굴 이미지 올리기</span>
                <small>PNG·JPG·WEBP · 최대 15MB</small>
              </div>
            )}
        </button>
        <input ref={fileInput} type="file" accept="image/*" hidden onChange={onPick} />

        <div className={s.sectionLabel}>허용 브랜드 유형</div>
        <Chips options={ALLOWED_PRESETS} value={allowed} onChange={setAllowed} multi />

        <div className={s.sectionLabel}>금지 브랜드 유형</div>
        <Chips options={FORBIDDEN_PRESETS} value={forbidden} onChange={setForbidden} multi />

        <div className={s.row2}>
          <div>
            <div className={s.sectionLabel}>건당 단가</div>
            <Field type="number" min={0} step={1000} value={unitPrice}
              onChange={(e) => setUnitPrice(e.target.value)} hint="셀러가 1회 사용할 때마다 지불" />
          </div>
          <div>
            <div className={s.sectionLabel}>유효기간</div>
            <Chips options={VALIDITY} value={validDays} onChange={(v) => v && setValidDays(v)} />
          </div>
        </div>

        <Button variant="primary" block onClick={onSubmit} disabled={submitting} iconRight="arrowRight">
          {submitting ? '등록 중…' : '라이선스 등록'}
        </Button>

        <div className={s.privacy}>
          <Icon name="lock" size={15} />
          <span>얼굴 이미지는 비공개로 저장되고, 검증된 본인만 열람할 수 있어요.</span>
        </div>
      </div>

      <div className={s.listHead}>
        <h2>내 라이선스</h2>
        <Link to="/model/register" className={s.backLink}>본인확인으로 <Icon name="chevRight" size={14} /></Link>
      </div>

      {loading
        ? <div className={s.empty}>불러오는 중…</div>
        : licenses.length === 0
          ? <div className={s.empty}><Icon name="image" size={22} /><span>아직 등록한 라이선스가 없어요.</span></div>
          : (
            <div className={s.grid}>
              {licenses.map((lic) => <LicenseCard key={lic.id} license={lic} onRevoked={reload} push={push} />)}
            </div>
          )}
    </div>
  );
}
