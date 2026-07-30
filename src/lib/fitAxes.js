/* =============================================================
   lib/fitAxes.js — 핏 프로필 축 카탈로그.
   프론트·백엔드 미러 정본: documents/fit_profile_spec.md §2.
   ============================================================= */

export const FIT_AXES = Object.freeze({
  top: {
    fit: {
      women: [
        { value: 'tight', label: '타이트', promptEn: 'tight/bodycon — clings to the torso like a second skin' },
        { value: 'slim', label: '슬림', promptEn: 'close to the body with a narrow clean torso line, not skin-tight' },
        { value: 'regular', label: '레귤러', promptEn: 'natural regular fit with light ease around chest and waist' },
        { value: 'semi_over', label: '세미오버', promptEn: 'semi-oversized, relaxed volume' },
        { value: 'over', label: '오버', promptEn: 'oversized volume, dropped shoulders, roomy chest and wide sleeves' },
      ],
      men: [
        { value: 'slim', label: '슬림', promptEn: 'close to the body with a narrow clean torso line, not skin-tight' },
        { value: 'regular', label: '레귤러', promptEn: 'natural regular fit with light ease around chest and waist' },
        { value: 'semi_over', label: '세미오버', promptEn: 'semi-oversized, relaxed volume' },
        { value: 'over', label: '오버', promptEn: 'oversized volume, dropped shoulders, roomy chest and wide sleeves' },
      ],
    },
    length: {
      women: [
        { value: 'ultra_crop', label: '울트라크롭', promptEn: 'hem ends well above the navel, midriff exposed' },
        { value: 'crop', label: '크롭', promptEn: 'short cropped hem ending around the high waist' },
        { value: 'basic', label: '기본', promptEn: 'standard hem ending around the hip line' },
        { value: 'long', label: '롱', promptEn: 'long hem extending below the hips' },
      ],
      men: [
        { value: 'crop', label: '크롭', promptEn: 'short cropped hem ending around the high waist' },
        { value: 'basic', label: '기본', promptEn: 'standard hem ending around the hip line' },
        { value: 'long', label: '롱', promptEn: 'long hem extending below the hips' },
      ],
    },
  },
  pants: {
    cut: {
      women: [
        { value: 'skinny', label: '스키니', promptEn: 'clings tightly from hip to ankle, second-skin leg line' },
        { value: 'slim', label: '슬림', promptEn: 'narrow fitted leg from hip to ankle without clinging' },
        { value: 'straight', label: '일자', promptEn: 'straight leg line with consistent width from thigh to hem' },
        { value: 'bootcut', label: '부츠컷', promptEn: 'fitted through hip and thigh, then flares out gently from the knee over the foot' },
        { value: 'wide', label: '와이드', promptEn: 'a full, voluminous wide-leg silhouette; the legs drape as broad swinging columns from hip to hem, hem covering most of the shoes' },
      ],
      men: [
        { value: 'slim', label: '슬림', promptEn: 'narrow fitted leg from hip to ankle without clinging' },
        { value: 'straight', label: '일자', promptEn: 'straight leg line with consistent width from thigh to hem' },
        { value: 'tapered', label: '테이퍼드', promptEn: 'roomy thigh tapering gradually to a narrower hem' },
        { value: 'relaxed', label: '릴렉스', promptEn: 'roomy and easy through hip and thigh, falling in a gently straight, slightly tapered leg; hem rests on the shoe top' },
        { value: 'semi_wide', label: '세미와이드', promptEn: 'a moderately wide, clean straight column from the knee down; noticeably wider than a regular straight leg, shoe mostly visible' },
        { value: 'wide', label: '와이드', promptEn: 'a full, voluminous wide-leg silhouette; the legs drape as broad swinging columns from hip to hem, hem covering most of the shoes' },
      ],
    },
    length: {
      women: [
        { value: 'above_ankle', label: '발목 위', promptEn: 'hem ends just above the ankle bone, ankle visible' },
        { value: 'ankle', label: '발목', promptEn: 'hem ends at the ankle bone, no break' },
        { value: 'below_ankle', label: '발목 덮음', promptEn: 'a lengthened version of the same trousers whose hem falls just past the ankle and rests lightly on the top of the foot with one soft break; if the photographed garment stops at or above the ankle bone, visibly re-tailor only its length proportions by extending the leg hems down over the instep until a single soft break forms; if it already satisfies this target, preserve those proportions' },
      ],
      men: [
        { value: 'above_ankle', label: '발목 위', promptEn: 'hem ends just above the ankle bone, ankle visible' },
        { value: 'ankle', label: '발목', promptEn: 'hem ends at the ankle bone, no break' },
        { value: 'below_ankle', label: '발목 덮음', promptEn: 'a lengthened version of the same trousers whose hem falls just past the ankle and rests lightly on the top of the foot with one soft break; if the photographed garment stops at or above the ankle bone, visibly re-tailor only its length proportions by extending the leg hems down over the instep until a single soft break forms; if it already satisfies this target, preserve those proportions' },
      ],
    },
  },
  skirt: {
    length: {
      women: [
        { value: 'mini', label: '미니', promptEn: 'short skirt hem ending above mid-thigh' },
        { value: 'midi', label: '미디', promptEn: 'skirt hem falls between the knee and mid-calf' },
        { value: 'long', label: '롱', promptEn: 'long skirt hem reaching the lower calf to ankle' },
      ],
      men: [],
    },
    silhouette: {
      women: [
        { value: 'h_line', label: 'H라인', promptEn: 'an H-line / pencil version of the same skirt — a straight narrow column with parallel side seams and no flare; if the photographed garment flares outward toward the hem, visibly re-tailor only its silhouette by straightening the side seams into parallel lines from hip to hem; if it already satisfies this target, preserve those proportions' },
        { value: 'a_line', label: 'A라인', promptEn: 'fitted at the waist then flares out steadily to a wide hem' },
        { value: 'mermaid', label: '머메이드', promptEn: 'tight through hip and thigh, then flares out sharply near the hem' },
      ],
      men: [],
    },
  },
  dress: {
    length: {
      women: [
        { value: 'mini', label: '미니', promptEn: 'short dress hem ending above mid-thigh' },
        { value: 'midi', label: '미디', promptEn: 'dress hem falls between the knee and mid-calf' },
        { value: 'long', label: '롱', promptEn: 'long dress hem reaching the lower calf to ankle' },
      ],
      men: [],
    },
    silhouette: {
      women: [
        { value: 'h_line', label: 'H라인', promptEn: 'straight narrow dress column from shoulder to hem, no flare' },
        { value: 'a_line', label: 'A라인', promptEn: 'fitted upper body then widens steadily toward the hem' },
        { value: 'fit_and_flare', label: '핏앤플레어', promptEn: 'fitted bodice through the waist, skirt flares out from the waist' },
        { value: 'mermaid', label: '머메이드', promptEn: 'tight through hip and thigh, then flares out sharply near the hem' },
      ],
      men: [],
    },
  },
  outer: {
    fit: {
      women: [
        { value: 'slim', label: '슬림', promptEn: 'close outerwear fit with narrow sleeves and minimal ease' },
        { value: 'regular', label: '레귤러', promptEn: 'regular outerwear fit with natural room for layering' },
        { value: 'semi_over', label: '세미오버', promptEn: 'semi-oversized outerwear with relaxed shoulders and extra body room' },
        { value: 'over', label: '오버', promptEn: 'an oversized version of the same outerwear; if the photographed garment sits at the natural shoulder point or is close-fitting, visibly re-tailor only its fit proportions by dropping the shoulder seams clearly below the shoulder points and adding broad air volume through the body and sleeves; if it already satisfies this target, preserve those proportions' },
      ],
      men: [
        { value: 'slim', label: '슬림', promptEn: 'close outerwear fit with narrow sleeves and minimal ease' },
        { value: 'regular', label: '레귤러', promptEn: 'regular outerwear fit with natural room for layering' },
        { value: 'semi_over', label: '세미오버', promptEn: 'semi-oversized outerwear with relaxed shoulders and extra body room' },
        { value: 'over', label: '오버', promptEn: 'an oversized version of the same outerwear; if the photographed garment sits at the natural shoulder point or is close-fitting, visibly re-tailor only its fit proportions by dropping the shoulder seams clearly below the shoulder points and adding broad air volume through the body and sleeves; if it already satisfies this target, preserve those proportions' },
      ],
    },
    length: {
      women: [
        { value: 'crop_short', label: '크롭·숏', promptEn: 'short outerwear hem ending around the waist or high hip' },
        { value: 'basic', label: '기본', promptEn: 'standard outerwear hem ending around the hip' },
        { value: 'long', label: '롱', promptEn: 'a lengthened version of the same outerwear whose hem extends to mid-thigh or lower; if the photographed garment ends above mid-thigh, visibly re-tailor only its length proportions by proportionally extending the front and back body panels downward past mid-thigh; if it already satisfies this target, preserve those proportions' },
      ],
      men: [
        { value: 'crop_short', label: '크롭·숏', promptEn: 'short outerwear hem ending around the waist or high hip' },
        { value: 'basic', label: '기본', promptEn: 'standard outerwear hem ending around the hip' },
        { value: 'long', label: '롱', promptEn: 'a lengthened version of the same outerwear whose hem extends to mid-thigh or lower; if the photographed garment ends above mid-thigh, visibly re-tailor only its length proportions by proportionally extending the front and back body panels downward past mid-thigh; if it already satisfies this target, preserve those proportions' },
      ],
    },
  },
});

export function fitProfileCategory(clothingType, subCategory) {
  const type = String(clothingType || '').toLowerCase();
  const sub = String(subCategory || '').toLowerCase();
  if (type === 'top') return 'top';
  if (type === 'bottom') {
    return (sub.includes('스커트') || sub.includes('치마') || sub.includes('skirt')) ? 'skirt' : 'pants';
  }
  if (type === 'dress') return 'dress';
  if (type === 'outer') return 'outer';
  return null;
}

export function axesFor(category, gender) {
  const byAxis = FIT_AXES[category] || {};
  const genderKey = gender === 'men' ? 'men' : 'women';
  return Object.fromEntries(
    Object.entries(byAxis)
      .map(([axis, byGender]) => [axis, byGender[genderKey] || []])
      .filter(([, values]) => values.length)
  );
}
