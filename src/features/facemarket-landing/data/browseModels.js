/* =============================================================
   facemarket-landing/data/browseModels.js
   '모델 둘러보기'(/models)가 보여 주는 목록.

   ⚠️ 전부 **가상 모델 예시**다. 실제 등록 모델은 여기 들어올 수 없다 — 얼굴 사진은 공개
   주소를 갖지 않는다(PRD §10 하드룰 1). 이 파일이 참조하는 건 public/models 의 예시 이미지뿐,
   서버를 부르지 않는다. 실데이터를 붙이게 되면 그때 필요한 건 '대표 이미지를 올린 모델'에
   한정한 목록과 1시간짜리 서명 주소이고, 등록 사실 자체를 공개할지부터 결정해야 한다.

   이름·키·몸무게·단가·기간은 **지어낸 값**이다. 화면 곳곳에 '예시' 고지를 함께 붙이는 걸
   전제로만 성립한다(BrowseSection 의 고지, 카드 배지, 상세 창의 배지). 고지를 떼면 이
   페이지는 실재 인물 명단으로 읽힌다 — 그러면 이 파일부터 지워야 한다.

   이름은 성만 남기고 마스킹한다(김○○). 제품의 공개 검증 화면이 이미 마스킹된 이름을
   보여 주므로 같은 규칙이고, 지어낸 풀네임이 실재 인물과 겹치는 일도 없다.
   ============================================================= */

/* 전신 예시 이미지. **그 모델 본인의 사진이 있을 때만** 보여 준다 — 이 소재는 원래 등록
   위저드의 체형 안내용이라 w2·m3 기준으로만 찍혀 있다. 없는 모델에게 남의 전신 사진을
   돌려 쓰면, 카드에 적힌 이름 밑에 다른 얼굴이 서는 셈이라 데모라도 데이터 오류로 읽힌다.
   그래서 나머지 모델은 이 칸 자체를 안 그린다(상세 창이 빈 배열을 보고 접는다). */
const OWN_EXAMPLES = {
  w2: ['slim', 'delicate', 'average', 'glamorous', 'plump']
    .slice(0, 3)
    .map((name) => `/models/women/w2-body-types/${name}.webp`),
  m3: ['thin', 'lean-muscular', 'solid-build']
    .map((name) => `/models/men/m3-body-types/${name}.webp`),
};

/* 허용 품목은 brandUseCategories.js 의 ALLOWED 에서 그대로 골라 쓴다 — 지어낸 품목명이
   섞이면 등록 화면의 선택지와 어긋난다. 금지 품목(속옷·수영복)은 애초에 넣지 않는다. */
const USE_SETS = [
  ['상의', '아우터', '데님'],
  ['원피스', '스커트', '니트·스웨터'],
  ['상의', '트레이닝·애슬레저'],
  ['셋업·수트', '아우터'],
  ['잡화·액세서리', '뷰티·화장품'],
  ['상의', '하의', '데님', '아우터'],
];

const VALID_DAYS = [90, 365, 730];

const SURNAMES = ['김', '이', '박', '최', '정', '강', '조', '윤', '장', '임', '한', '오', '서', '신'];

/* [파일 이름, 성별, 키(cm), 몸무게(kg)] — 키·몸무게는 지어낸 예시값이다. */
const ROWS = [
  ['women/w1', 'female', 168, 49],
  ['women/w2', 'female', 172, 52],
  ['women/w3', 'female', 165, 47],
  ['women/w4', 'female', 174, 55],
  ['women/w5', 'female', 170, 51],
  ['women/w6', 'female', 167, 48],
  ['women/w7', 'female', 175, 56],
  ['women/w8', 'female', 169, 50],
  ['women/w9', 'female', 171, 53],
  ['women/w10', 'female', 166, 47],
  ['women/w11', 'female', 173, 54],
  ['men/m1', 'male', 182, 70],
  ['men/m2', 'male', 178, 66],
  ['men/m3', 'male', 186, 75],
];

/* -face 변형이 있는 모델만 상세 창에서 얼굴을 두 장 보여 준다. 없는 모델(w1·m1·m2)은
   한 장이고, 상세 창이 장수에 맞춰 배치한다 — 없는 파일을 억지로 채우지 않는다. */
const HAS_FACE = new Set([
  'women/w2', 'women/w3', 'women/w4', 'women/w5', 'women/w6', 'women/w7',
  'women/w8', 'women/w9', 'women/w10', 'women/w11', 'men/m3',
]);

export const BROWSE_MODELS = Object.freeze(
  ROWS.map(([file, gender, height, weight], index) => {
    const id = file.replace(/^.*\//, '');
    const portrait = `/models/${file}.webp`;
    return Object.freeze({
      id,
      gender,
      name: `${SURNAMES[index % SURNAMES.length]}○○`,
      height,
      weight,
      portrait,
      alt: `가상 모델 예시 ${index + 1}`,
      faces: Object.freeze(HAS_FACE.has(file) ? [portrait, `/models/${file}-face.webp`] : [portrait]),
      examples: Object.freeze(OWN_EXAMPLES[id] || []),
      license: Object.freeze({
        uses: USE_SETS[index % USE_SETS.length],
        unitPrice: 2000 + (index % 5) * 500,
        validDays: VALID_DAYS[index % VALID_DAYS.length],
      }),
    });
  }),
);
