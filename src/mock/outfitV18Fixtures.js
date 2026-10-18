import { buildMockOutfitRecommendation } from './matchingRecommendation.js';
import { catalog1018Garments } from './outfitCatalogFixtures.js';

// Explicit QA catalog, never a source for production or the default mock catalog.
// Owner-supplied bag/accessory photos are still pending. SVGs below are mock illustrations.
export const MOCK_TIDY_KNIT = '/assets/sponsorship-knit-ivory-ghost.webp';
export const mockQuery = () => new URLSearchParams(globalThis.window?.location?.search || globalThis.location?.search || '');

export function outfitScenarioFromQuery() {
  const mode = mockQuery().get('outfitDemo');
  if (!['ready', 'pending', 'late', 'fallback', 'unavailable', 'oneSet', 'noShoes'].includes(mode)) return null;
  return { fixture: 'v18', status: ['pending', 'late'].includes(mode) ? 'pending'
    : ['fallback', 'unavailable'].includes(mode) ? mode : 'ready',
    ...(['pending', 'late'].includes(mode) ? { settleTo: 'ready', delayMs: mode === 'late' ? 18000 : 12000 } : {}),
    ...(mode === 'oneSet' ? { setCount: 1 } : {}),
    ...(mode === 'noShoes' ? { unavailableRoles: ['footwear'] } : {}),
  };
}

function fixtureImage(role, color, index) {
  const paths = {
    footwear: [
      "<path d='M32 131 46 86 79 90 109 115 150 125Q171 129 171 147L29 147Z'/><path d='M31 147H171M74 103 98 108M69 114 107 119' fill='none'/>",
      "<path d='M27 127 43 97Q61 88 81 100L105 121 153 123Q175 128 174 147H26Z'/><path d='M52 103 84 119 116 124M27 146H174' fill='none'/>",
      "<path d='M24 135 49 103 79 116 134 125Q168 130 172 147H23Z'/><path d='M49 103Q34 82 60 78L84 112M29 147V160M36 147V160' fill='none'/>",
    ],
    bag: [
      "<rect x='47' y='91' width='106' height='89' rx='12'/><path d='M72 92V77A28 28 0 0 1 128 77V92' fill='none' stroke-width='8'/><path d='M48 115H152' fill='none'/>",
      "<path d='M49 103Q100 87 151 103L158 174Q99 187 42 174Z'/><path d='M61 103Q72 31 137 99' fill='none' stroke-width='8'/>",
      "<rect x='40' y='104' width='120' height='69' rx='18'/><path d='M57 106Q100 11 145 106M42 125 99 149 158 125' fill='none'/>",
    ],
    accessory: [
      "<circle cx='67' cy='115' r='24' fill='none' stroke-width='10'/><circle cx='133' cy='115' r='24' fill='none' stroke-width='10'/><circle cx='67' cy='83' r='7'/><circle cx='133' cy='83' r='7'/>",
      "<path d='M57 80Q35 159 100 168Q165 159 143 80' fill='none' stroke-width='8'/><circle cx='100' cy='167' r='12'/>",
      "<rect x='33' y='107' width='59' height='38' rx='17'/><rect x='108' y='107' width='59' height='38' rx='17'/><path d='M92 119H108M32 117 20 102M168 117 180 102' fill='none'/>",
    ],
  };
  const canvas = role === 'footwear' ? "width='210' height='100' viewBox='12 74 189 90'"
    : "width='200' height='240' viewBox='0 0 200 240'";
  const svg = `<svg xmlns='http://www.w3.org/2000/svg' ${canvas}><title>Mock QA ${role} fixture</title><g fill='${color}' stroke='#77717a' stroke-width='2.5' stroke-linejoin='round' stroke-linecap='round'>${paths[role][index % 3]}</g></svg>`;
  return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
}

const fixtureRows = {
  footwear: [
    ['화이트 스니커즈', 'white', '#f5f2ee'], ['브라운 로퍼', 'brown', '#866349'], ['블랙 슬링백', 'black', '#302d32'],
    ['아이보리 스니커즈', 'ivory', '#ece7dc'], ['카멜 로퍼', 'brown', '#b6936f'], ['블랙 스니커즈', 'black', '#49464c'],
  ],
  bag: [
    ['아이보리 토트백', 'ivory', '#e9e4da'], ['브라운 숄더백', 'brown', '#8e6a51'], ['블랙 크로스백', 'black', '#373239'],
    ['그레이 토트백', 'gray', '#96939a'], ['카멜 숄더백', 'brown', '#b68c67'], ['블랙 미니백', 'black', '#4b454f'],
  ],
  accessory: [
    ['골드 이어링', 'gold', '#d2b069'], ['아이보리 목걸이', 'ivory', '#eee9df'], ['블랙 선글라스', 'black', '#39343e'],
    ['실버 이어링', 'silver', '#b4b4ba'], ['골드 목걸이', 'gold', '#c5a369'], ['브라운 선글라스', 'brown', '#8a6953'],
  ],
};

export function v18FixtureCandidates(analysis) {
  const props = Object.fromEntries(Object.entries(fixtureRows).map(([role, rows]) => [role,
    rows.map(([name, colorGroup, color], index) => {
      const image = fixtureImage(role, color, index);
      return { id: `mock_v18_${role}_${index + 1}`, name: `데모 ${name}`, role, colorGroup, length: 'na', gender: 'unisex',
        thumb: image, thumbnailUrl: image, imageUrl: image, boardUrl: index % 2 === 0 ? image : null,
        isMockFixture: true, isCompatible: true,
        ...(role === 'footwear' ? { cm: 16, ar: 2.1 } : {}) };
    })]));
  if (analysis.clothingType === 'outer') {
    props.inner = [{ id: 'mock_v18_inner_knit', name: '데모 아이보리 니트', clothingType: 'inner', length: 'regular', gender: 'unisex',
      role: 'inner', colorGroup: 'ivory', thumb: MOCK_TIDY_KNIT, thumbnailUrl: MOCK_TIDY_KNIT,
      imageUrl: MOCK_TIDY_KNIT, boardUrl: null, isMockFixture: true, isCompatible: true, cm: 64, ar: .95 },
    ...catalog1018Garments.filter(item => item.clothingType === 'inner').map(item => ({ ...item,
      role: 'inner', thumb: item.thumbnailUrl, boardUrl: item.imageUrl, isCompatible: true }))];
  }
  return props;
}

export function v18FixtureRecommendation(candidates, { status, contextKey, setCount = 3, settled = false }) {
  const recommendation = buildMockOutfitRecommendation(candidates, { status, contextKey });
  if (!recommendation.sets.length) return recommendation;
  const roles = Object.keys(candidates);
  const pools = Object.fromEntries(roles.map(role => [role, candidates[role].filter(item => !item.isCustom)]));
  const sets = Array.from({ length: Math.min(3, Math.max(1, setCount)) }, (_, index) => ({
    slots: Object.fromEntries(roles.map(role => [role,
      pools[role][(index + (settled ? 1 : 0)) % Math.min(3, pools[role].length)]?.id ?? null])),
    reason: 'v18 화면 검사용 모의 코디예요.',
  })).filter((set, index, all) => all.findIndex(other => JSON.stringify(other.slots) === JSON.stringify(set.slots)) === index);
  return { ...recommendation, sets, candidates: Object.fromEntries(roles.map(role => [role,
    candidates[role].map(item => ({ ...item, inSets: sets.some(set => set.slots[role] === item.id) }))])) };
}
