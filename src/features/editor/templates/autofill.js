/* =============================================================
   templates/autofill.js — 상세페이지 템플릿 프레임의 빈 이미지 슬롯을
   프로젝트의 생성 착장컷으로 역할(content_role) 맞춰 자동 채운다.

   - 슬롯의 기대 역할 = elements 의 image frameSlot 에 저작된 `roleHint`(editorLibrary).
   - 컷의 역할 = wardrobe 컷의 sourceBlockId 로 storyboard 블록을 조인해 복원(contentRole).
   - 매칭(3티어): ① roleHint 정확 → ② 같은 섹션역할 완화 → ③ any 생성컷(최후, 채움 우선).
     컷은 재사용 허용(라운드로빈으로 변화). 컷이 0개면 빈 슬롯 그대로(폴백).
   - 순수 함수. 서버 page_assembler 는 채워진 src 이미지를 그대로 조립하므로 서버 변경 불필요.
   역할 어휘·섹션 매핑은 storyboardTaxonomy 단일 소스에서 가져온다(중복 정의 금지).
   ============================================================= */
import { CONTENT_TEMPLATES } from '../../../lib/storyboardTaxonomy.js';

// content_role → sectionRole (hero→hooking, detail→product ...). 단일 소스 파생.
const SECTION_OF_ROLE = new Map(CONTENT_TEMPLATES.map((template) => [template.value, template.sectionRole]));

function pushInto(map, key, value) {
  if (!key) return;
  const list = map.get(key);
  if (list) list.push(value);
  else map.set(key, [value]);
}

/**
 * grouped wardrobe({group:[img]}) + storyboard 블록으로 역할 붙은 컷 풀을 만든다.
 * 생성 컷(generated)이고 src 있는 것만. 같은 src 는 한 번만.
 * @returns {{id,src,role,sectionRole,cutType,sourceBlockId,wardrobeGroup}[]}
 */
export function buildRoledCutPool(wardrobe, storyboard) {
  const sourceById = new Map((storyboard || []).filter(Boolean).map((block) => [block.id, block]));
  const seen = new Set();
  const pool = [];
  for (const group of Object.keys(wardrobe || {})) {
    for (const img of wardrobe[group] || []) {
      const src = typeof img?.src === 'string' ? img.src.trim() : '';
      if (!src || !img.generated) continue;
      if (seen.has(src)) continue;
      seen.add(src);
      const block = img.sourceBlockId ? sourceById.get(img.sourceBlockId) : null;
      const role = block?.contentRole || null;
      pool.push({
        id: img.id || null,
        src,
        role,
        sectionRole: role ? (SECTION_OF_ROLE.get(role) || null) : null,
        cutType: img.cutType || null,
        sourceBlockId: img.sourceBlockId || null,
        wardrobeGroup: img.wardrobeGroup || group || null,
      });
    }
  }
  return pool;
}

/**
 * blocks 의 빈 frameSlot(roleHint 있는 것)에 컷을 채운다. 불변 — 새 blocks 반환.
 * 이미 src 있는 슬롯·roleHint 없는 슬롯·frameSlot 아닌 요소는 건드리지 않는다.
 */
export function autofillBlocks(blocks, cuts) {
  const pool = (cuts || []).filter((cut) => cut && cut.src);
  if (!pool.length) return blocks;

  const byRole = new Map();
  const bySection = new Map();
  for (const cut of pool) {
    pushInto(byRole, cut.role, cut);
    pushInto(bySection, cut.sectionRole, cut);
  }

  const cursor = Object.create(null);
  // roleHint 있으면 3티어(정확 role → 같은 섹션 → any), 없으면 any 컷 라운드로빈.
  const pick = (roleHint) => {
    const section = roleHint ? (SECTION_OF_ROLE.get(roleHint) || null) : null;
    const tiers = [
      roleHint ? ['role:' + roleHint, byRole.get(roleHint)] : null,
      section ? ['sec:' + section, bySection.get(section)] : null,
      ['all', pool],
    ].filter((tier) => tier && tier[1] && tier[1].length);
    if (!tiers.length) return null;
    const [key, list] = tiers[0];
    const index = cursor[key] || 0;
    cursor[key] = index + 1;
    return list[index % list.length];
  };

  return blocks.map((block) => {
    let touched = false;
    const elements = (block.elements || []).map((el) => {
      if (el.type !== 'image' || !el.frameSlot || el.src) return el;
      const cut = pick(el.roleHint);
      if (!cut) return el;
      touched = true;
      return {
        ...el,
        src: cut.src,
        ...(cut.cutType ? { cutType: cut.cutType } : {}),
        ...(cut.wardrobeGroup ? { wardrobeGroup: cut.wardrobeGroup } : {}),
        ...(cut.sourceBlockId ? { sourceBlockId: cut.sourceBlockId } : {}),
      };
    });
    return touched ? { ...block, elements } : block;
  });
}
