// Authored SVG illustrations only. No catalog photos or external image requests.
const COLORS = {
  white: '#f4f1eb', ivory: '#e7e0ce', beige: '#cbb99a', brown: '#88654d',
  gray: '#909096', black: '#343239', blue: '#7794b1', navy: '#404c64',
  green: '#81937b', red: '#b76a68', khaki: '#a59a7e',
};

export function mockGarmentImage({ clothingType, category, length, colorGroup }) {
  const bottom = clothingType === 'bottom';
  const height = bottom ? ({ mini: 84, short: 96, knee: 112, midi: 144, maxi: 180, long: 180 }[length] || 200)
    : length === 'cropped' ? 90 : 128;
  const width = bottom ? 104 : 130;
  const shape = !bottom
    ? `<path d='M42 3 3 29 16 57 33 48 33 ${height - 3}H97V48L114 57 127 29 88 3Q65 27 42 3Z'/>`
    : category === '스커트'
      ? `<path d='M25 3H79L101 ${height - 3}H3Z'/><path d='M25 14H79' fill='none'/>`
      : `<path d='M13 3H91L101 ${height - 3}H62L52 48 42 ${height - 3}H3Z'/><path d='M13 14H91M52 3V48' fill='none'/>`;
  const svg = `<svg xmlns='http://www.w3.org/2000/svg' width='${width}' height='${height}' viewBox='0 0 ${width} ${height}'><title>Mock garment illustration</title><g fill='${COLORS[colorGroup] || COLORS.gray}' stroke='#77717a' stroke-width='2' stroke-linejoin='round'>${shape}</g></svg>`;
  return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
}

// Supplements the legacy metadata so default mock garments and the v18 demo
// exercise every 10/18 color and length, including unisex inner candidates.
export const catalog1018Garments = [
  ['top', '베이지 크롭 셔츠', 'beige', 'cropped', '셔츠'],
  ['top', '그린 반팔 티셔츠', 'green', 'regular', '반팔 티셔츠'],
  ['top', '네이비 긴팔 티셔츠', 'navy', 'regular', '긴팔 티셔츠'],
  ['top', '레드 니트', 'red', 'regular', '니트'],
  ['bottom', '화이트 미니 스커트', 'white', 'mini', '스커트'],
  ['bottom', '브라운 버뮤다 쇼츠', 'brown', 'knee', '쇼츠'],
  ['bottom', '그린 미디 스커트', 'green', 'midi', '스커트'],
  ['bottom', '아이보리 맥시 스커트', 'ivory', 'maxi', '스커트'],
  ['inner', '블랙 민소매', 'black', 'regular', '민소매'],
  ['inner', '브라운 긴팔 티셔츠', 'brown', 'regular', '긴팔 티셔츠'],
  ['inner', '그레이 반팔 티셔츠', 'gray', 'regular', '반팔 티셔츠'],
  ['inner', '그린 민소매', 'green', 'regular', '민소매'],
  ['inner', '아이보리 반팔 티셔츠', 'ivory', 'regular', '반팔 티셔츠'],
  ['inner', '네이비 긴팔 티셔츠', 'navy', 'regular', '긴팔 티셔츠'],
  ['inner', '화이트 민소매', 'white', 'regular', '민소매'],
].map(([clothingType, name, colorGroup, length, category], index) => {
  const item = { id: `mock_1018_${clothingType}_${colorGroup}`, name: `데모 ${name}`, clothingType,
    category, colorGroup, colorName: name.split(' ')[0], length,
    gender: category === '스커트' ? 'women' : 'unisex', styleTags: ['daily'], fit: 'regular',
    colorBrightness: 50, isActive: true, isMockFixture: true, sortOrder: 500 + index };
  const image = mockGarmentImage(item);
  return { ...item, imageUrl: image, thumbnailUrl: image };
});
