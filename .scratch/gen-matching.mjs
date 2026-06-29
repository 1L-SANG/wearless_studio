/* one-shot generator: rebuild matching-clothing assets + seed from
   outputs/coor_matching/generated_v2/{women,men}_{top,bottom}.
   - clears old public/assets/matching/{top,bottom} pngs
   - copies real images with clean ascii names + sips thumbnails
   - emits src/mock/seedMatchingItems.js (64 items, gender-tagged) */
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const SRC_BASE = path.join(ROOT, 'outputs/coor_matching/generated_v2');
const PUB_BASE = path.join(ROOT, 'public/assets/matching');

const GROUPS = [
  { dir: 'women_top', gender: 'women', type: 'top', sortBase: 100 },
  { dir: 'women_bottom', gender: 'women', type: 'bottom', sortBase: 200 },
  { dir: 'men_top', gender: 'men', type: 'top', sortBase: 300 },
  { dir: 'men_bottom', gender: 'men', type: 'bottom', sortBase: 400 },
];

// brightness 0(dark)..100(light) keyed by exact color label from info.md
const BRIGHTNESS = {
  '화이트': 100, '아이보리': 95, '라이트베이지': 88, '샌드베이지': 84, '베이지': 82,
  '라이트그레이': 76, '아이스블루': 74, '라이트블루': 70, '멜란지그레이': 64, '샌드그레이': 62,
  '블루그레이': 58, '그레이': 54, '블루체크': 50, '블루': 46, '워시드블루': 44,
  '브라운': 40, '워시드카키': 38, '워시드그레이': 36, '차콜그레이': 28, '다크그레이': 26,
  '네이비': 22, '다크네이비': 18, '워시드블랙': 10, '블랙': 0,
};

function colorGroup(c) {
  if (c.includes('화이트')) return 'white';
  if (c.includes('아이보리')) return 'ivory';
  if (c.includes('베이지')) return 'beige';
  if (c.includes('카키')) return 'khaki';
  if (c.includes('브라운')) return 'brown';
  if (c.includes('네이비')) return 'navy';
  if (c.includes('블랙')) return 'black';
  if (c.includes('그레이')) return 'gray';      // before blue: 블루그레이 → gray
  if (c.includes('블루')) return 'blue';
  return 'other';
}

const STYLE_TOKEN = { '무난': 'basic', '트렌디': 'trendy', '폴로': 'polo', '치마': 'skirt' };

function parseInfo(dir) {
  const md = fs.readFileSync(path.join(SRC_BASE, dir, 'info.md'), 'utf8');
  const m = md.match(/```json\s*([\s\S]*?)```/);
  if (!m) throw new Error('no json block in ' + dir);
  return JSON.parse(m[1]);
}

function lengthOf(type, category, name) {
  if (category.includes('쇼츠')) return 'short';
  if (category.includes('스커트')) return name.includes('롱') ? 'long' : 'midi';
  return type === 'top' ? 'regular' : 'full';
}

// reset dest folders
for (const t of ['top', 'bottom']) {
  const dir = path.join(PUB_BASE, t);
  const thumbs = path.join(dir, 'thumbs');
  fs.mkdirSync(thumbs, { recursive: true });
  for (const f of fs.readdirSync(dir)) {
    if (f.endsWith('.png')) fs.rmSync(path.join(dir, f));
  }
  for (const f of fs.readdirSync(thumbs)) {
    if (f.endsWith('.png')) fs.rmSync(path.join(thumbs, f));
  }
}

const seed = [];
for (const g of GROUPS) {
  const items = parseInfo(g.dir);
  items.forEach((it, i) => {
    const nn = String(i + 1).padStart(2, '0');
    const file = `${g.gender}-${g.type}-${nn}.png`;
    const srcImg = path.join(SRC_BASE, g.dir, it.image);
    const destImg = path.join(PUB_BASE, g.type, file);
    const destThumb = path.join(PUB_BASE, g.type, 'thumbs', file);
    fs.copyFileSync(srcImg, destImg);
    execFileSync('sips', ['-Z', '560', srcImg, '--out', destThumb], { stdio: 'ignore' });

    const color = it.color || '';
    const category = (it.category || '').replace(/\(.*?\)/g, '').trim();
    const name = `${color} ${category}`.trim();
    const token = STYLE_TOKEN[it.image.split('_')[1]] || 'basic';
    const styleTags = [...new Set([token, 'daily', ...(it.features || []).map((f) => f.trim())])];

    seed.push({
      id: `match_${g.gender}_${g.type}_${nn}`,
      name,
      clothingType: g.type,
      gender: g.gender,
      category: category || g.type,
      colorName: color,
      colorGroup: colorGroup(color),
      colorBrightness: color in BRIGHTNESS ? BRIGHTNESS[color] : 50,
      styleTags,
      fit: it.fit || 'regular',
      length: lengthOf(g.type, category, it.image),
      imageUrl: `/assets/matching/${g.type}/${file}`,
      thumbnailUrl: `/assets/matching/${g.type}/thumbs/${file}`,
      isActive: true,
      sortOrder: g.sortBase + i + 1,
    });
  });
}

const header = `/* =============================================================
   mock/seedMatchingItems.js — Supabase-ready matching clothing seed.
   AUTO-GENERATED from outputs/coor_matching/generated_v2 by
   .scratch/gen-matching.mjs (women+men × top+bottom = 64 items).
   imageUrl/thumbnailUrl 는 public/assets/matching/ 의 실제 에셋 경로.
   colorBrightness: 100(밝음)→0(어두움), 색상 정렬용.

   ⚠️ 에셋은 의도적으로 git 제외(.gitignore: public/assets/matching/).
   실제 브랜드 평면컷이라 IP 리스크 → 레포에 커밋 안 함. 따라서 이 경로들은
   "로컬 dev 전용"이며, 다른 머신/배포(Vercel)에선 404 가 정상 동작이다.
   - 로컬 재생성: node .scratch/gen-matching.mjs (원본 outputs/ 필요)
   - 운영/배포: 라이선스 거친 R2 에셋 서빙으로 대체 예정(backend §3).
   ============================================================= */

export const seedMatchingItems = ${JSON.stringify(seed, null, 2)};

export default seedMatchingItems;
`;

fs.writeFileSync(path.join(ROOT, 'src/mock/seedMatchingItems.js'), header);

// 시더(server/scripts/seed_matching.py) 입력용 정본 데이터 JSON (이미지 없음 · 메타만).
const seedJsonPath = path.join(ROOT, 'server/seed/matching_items.json');
fs.mkdirSync(path.dirname(seedJsonPath), { recursive: true });
fs.writeFileSync(seedJsonPath, JSON.stringify(seed, null, 2));

console.log(`done: ${seed.length} items, thumbs generated.`);
