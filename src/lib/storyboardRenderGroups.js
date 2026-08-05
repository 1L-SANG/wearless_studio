import { inferSectionRole } from "./storyboardTaxonomy.js";

const GROUPS = Object.freeze([
    { key: "hooking", title: "Section 1", label: "후킹" },
    { key: "styling", title: "Section 2", label: "스타일링" },
    { key: "studio", title: "Section 3", label: "스튜디오" },
    { key: "product", title: "Section 4", label: "의류 확인" },
]);

function renderGroupKey(block) {
    const sectionRole = inferSectionRole(block);
    return sectionRole && GROUPS.some((group) => group.key === sectionRole)
        ? sectionRole
        : "styling";
}

export function renderGroups(blocks) {
    const groups = GROUPS.map((group) => ({ ...group, items: [] }));
    const byKey = new Map(groups.map((group) => [group.key, group]));
    (blocks || []).forEach((block, index) => {
        byKey
            .get(renderGroupKey(block))
            .items.push({ index: index + 1, block });
    });
    return groups;
}
