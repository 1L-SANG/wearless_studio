/* =============================================================
   facemarket-landing/data/publicModels.js
   '모델 둘러보기'(/models)가 실제 등록 모델을 가져와 카드·상세 창이 쓰는 한 가지 모양으로
   맞추는 어댑터. 가상 예시(browseModels.js)도 같은 모양으로 바꿔 한 목록에 섞는다.

   실모델의 출처는 GET /v1/facemarket/public/models (인증 없음). 모델이 /model/confirm 에서
   확대샷·전신샷을 고르고 공개에 동의한 것만 온다 — 명세 documents/facemarket_testcuts_profile_spec_v1.md §3.3.
   서버가 내보내는 건 활동명·성별·나이대·키·체형·라이선스 조건·공개 카탈로그 이미지뿐이고, 여기서도
   그 이상을 화면에 올리지 않는다(toBrowseModel 이 필드를 골라 담는다 — 서버가 실수로 더 보내도 새지 않게).

   fetch 를 lib/api/facemarket.js 에 두지 않은 이유: 그 파일은 로그인 세션을 전제로 한 셀러·모델용
   경계라서, 비로그인 방문자가 보는 공개 목록은 따로 두는 게 맞다.
   ============================================================= */
// 상대 경로인 이유: node --test 가 '@/' 별칭을 모른다(tests/frontend 의 다른 어댑터와 같은 규칙).
import { bodyTypeLabel, heightBucketLabel } from '../../../lib/facemarketPhysique.js';

/** 월정액 = 건당 × 2.5, 한 쇼핑몰 30일 무제한(2026-09-04 오너 확정, 구현 지시서 §1).
    서버가 월정액을 내려주기 시작하면 그 값을 쓰고 이 상수는 지운다. */
export const MONTHLY_MULTIPLIER = 2.5;

const GENDER_LABEL = Object.freeze({ male: '남성', female: '여성' });

/** 유효기간(일)을 사람이 읽는 말로. 라이선스 폼 선택지(90일·1년·2년)와 '영구'(3650일 이상)를 덮는다. */
export function formatValidity(days) {
  const n = Number(days);
  if (!Number.isFinite(n) || n <= 0) return null;
  if (n >= 3650) return '영구';
  if (n % 365 === 0) return `${n / 365}년`;
  return `${n}일`;
}

/** 만료 시각 → "2027년 9월 7일까지". 잘못된 값이면 null. */
export function formatValidUntil(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return `${d.getFullYear()}년 ${d.getMonth() + 1}월 ${d.getDate()}일까지`;
}

/** 키 한 줄. 실측 cm 가 있으면 그것, 없으면 등록 위저드의 키 구간 라벨, 둘 다 없으면 null. */
export function heightText({ heightCm, heightBucket } = {}) {
  const cm = Number(heightCm);
  if (Number.isFinite(cm) && cm > 0) return `${Math.round(cm)}cm`;
  if (heightBucket) return heightBucketLabel(heightBucket);
  return null;
}

/** 체형 라벨. 서버 enum 값이 아니면 null — 값 원문('toned')이 화면에 새지 않게. */
export function bodyTypeText(bodyType) {
  return bodyType ? bodyTypeLabel(bodyType) : null;
}

/** 몸무게 → "62kg". 없으면 null. */
export function weightText(weightKg) {
  const kg = Number(weightKg);
  return Number.isFinite(kg) && kg > 0 ? `${Math.round(kg)}kg` : null;
}

/** 착용 사이즈 한 줄, 단위 표시(2026-09-08 오너 지시). "상의 M · 하의 30인치 · 신발 270mm".
    하의는 숫자면 인치, 글자(S/M/L)면 그대로. 하나도 없으면 null. */
export function sizesText({ topSize, bottomSize, shoeSize } = {}) {
  const parts = [];
  if (topSize) parts.push(`상의 ${String(topSize).toUpperCase()}`);
  if (bottomSize) {
    const n = Number(bottomSize);
    parts.push(Number.isFinite(n) ? `하의 ${n}인치` : `하의 ${String(bottomSize).toUpperCase()}`);
  }
  const shoe = Number(shoeSize);
  if (Number.isFinite(shoe) && shoe > 0) parts.push(`신발 ${shoe}mm`);
  return parts.length ? parts.join(' · ') : null;
}

/** 카드 보조 줄. "키 178cm · 잔잔한 근육" / 한쪽만 있으면 그것만 / 둘 다 없으면 null. */
export function physiqueLine(profile = {}) {
  const parts = [];
  const height = heightText(profile);
  if (height) parts.push(`키 ${height}`);
  const body = bodyTypeText(profile.bodyType);
  if (body) parts.push(body);
  return parts.length ? parts.join(' · ') : null;
}

function licenseView(license) {
  if (!license) return null;
  const clean = (list) => (Array.isArray(list) ? list.filter(Boolean) : []);
  const unitPrice = Number(license.unitPrice);
  const hasPrice = Number.isFinite(unitPrice) && unitPrice > 0;
  return {
    uses: clean(license.allowedUse),
    excluded: clean(license.forbiddenUse),
    unitPrice: hasPrice ? unitPrice : null,
    monthlyPrice: hasPrice ? Math.round(unitPrice * MONTHLY_MULTIPLIER) : null,
    validity: formatValidity(license.validDays),
    validUntilText: formatValidUntil(license.validUntil),
  };
}

/**
 * 서버 PublicModelItem → 화면 모델. 키를 골라 담는다(화이트리스트).
 * 화면 모델 모양(카드·상세 창이 같이 쓴다):
 *   { id, kind: 'real'|'example', name, alt, closeup, fullbody|null,
 *     gender, ageBand, height, weight, sizes, spec,
 *     license: { uses, excluded, unitPrice, monthlyPrice, validity, validUntilText }, verified }
 */
export function toBrowseModel(item) {
  if (!item || !item.id || !item.closeupImageUrl) return null;
  const name = String(item.displayName || '').trim() || '모델';
  return Object.freeze({
    id: String(item.id),
    kind: 'real',
    name,
    alt: `${name} 확대샷`,
    closeup: item.closeupImageUrl,
    fullbody: item.fullbodyImageUrl || null,
    gender: GENDER_LABEL[item.gender] || null,
    ageBand: typeof item.ageBand === 'string' && item.ageBand ? item.ageBand : null,
    height: heightText(item),
    weight: weightText(item.weightKg),
    sizes: sizesText(item),
    spec: physiqueLine(item),
    license: licenseView(item.license),
    // 공개 목록 조건(§3.3)이 본인확인·증서 발급을 이미 요구하므로, 여기 온 모델은 둘 다 참이다.
    verified: true,
  });
}

/** 가상 예시(browseModels.js)도 같은 모양으로. 확대샷 = 초상, 전신샷 = 본인 전신 예시의 첫 장(없으면 null). */
export function fromExampleModel(model) {
  return Object.freeze({
    id: model.id,
    kind: 'example',
    name: model.name,
    alt: model.alt,
    closeup: model.portrait,
    fullbody: model.examples?.[0] || null,
    gender: GENDER_LABEL[model.gender] || null,
    ageBand: null,
    height: `${model.height}cm`,
    weight: `${model.weight}kg`,
    sizes: null,
    spec: `${model.height}cm · ${model.weight}kg`,
    license: {
      uses: model.license.uses,
      excluded: [],
      unitPrice: model.license.unitPrice,
      monthlyPrice: Math.round(model.license.unitPrice * MONTHLY_MULTIPLIER),
      validity: formatValidity(model.license.validDays),
      validUntilText: null,
    },
    verified: false,
  });
}

export const PUBLIC_MODELS_PATH = '/v1/facemarket/public/models';

function apiBase() {
  // node 테스트에서는 import.meta.env 가 없다 — 그때는 빈 문자열(상대 경로).
  return (typeof import.meta !== 'undefined' && import.meta.env?.VITE_API_BASE_URL) || '';
}

/**
 * 공개 모델 목록. 로그인 없이 부른다. 실패하면 던진다 — 부르는 쪽(BrowseSection)이 조용히
 * 예시만 남기는 걸로 처리한다(공개 페이지가 서버 사정으로 비어 보이면 안 된다).
 */
export async function fetchPublicModels({ signal, fetchImpl = globalThis.fetch } = {}) {
  const res = await fetchImpl(`${apiBase()}${PUBLIC_MODELS_PATH}`, { signal, headers: { Accept: 'application/json' } });
  if (!res.ok) throw new Error(`public models ${res.status}`);
  const payload = await res.json();
  const items = Array.isArray(payload?.items) ? payload.items : [];
  return items.map(toBrowseModel).filter(Boolean);
}
