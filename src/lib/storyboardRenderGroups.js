import { deriveSections } from './sections.js';

export const renderGroupKey = block => block?.pageSectionKey || block?.sectionId || null;

export function renderGroups(blocks) {
  return deriveSections(blocks).map((section, index) => ({
    key: section.pageSectionKey, title: `Section ${index + 1}`, label: section.title,
    items: section.items.map(({ b, i }) => ({ block: b, index: i + 1 })),
  }));
}
