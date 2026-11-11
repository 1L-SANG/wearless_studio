import { pageTemplatesCatalog } from './catalog.js';
import { currentSectionDesignId, defaultSectionDesignId, sectionDesignChoices } from './designChoices.js';

// The axes are a picker projection. Every option resolves to one existing
// catalog design ID, and no axis state is serialized into blocks.
export function sectionDesignAxes(run, { storyboard = run?.blocks || [], product } = {}) {
  const choices = sectionDesignChoices(run, { storyboard, product });
  const defaultId = defaultSectionDesignId(run, storyboard);
  const currentId = currentSectionDesignId(run, storyboard);
  const current = pageTemplatesCatalog.designs[currentId]?.choiceAxes;
  const fallback = choices.find(design => design.id === defaultId) || choices[0];
  const active = current || fallback?.choiceAxes;
  const defaults = pageTemplatesCatalog.designs[defaultId]?.choiceAxes;
  const options = (axis, other) => {
    const seen = new Set();
    return choices.flatMap(design => {
      const axes = design.choiceAxes, value = axes?.[axis];
      if (!value || axes[other]?.id !== active?.[other]?.id || seen.has(value.id)) return [];
      seen.add(value.id);
      return [{ id: value.id, name: value.name, designId: design.id,
        selected: Boolean(current && current[axis].id === value.id), isDefault: defaults?.[axis]?.id === value.id }];
    }).sort((a, b) => Number(b.isDefault) - Number(a.isDefault));
  };
  return { hasAxes: choices.some(design => Boolean(design.choiceAxes)),
    layouts: options('layout', 'decoration'), decorations: options('decoration', 'layout'),
    currentLayoutId: current?.layout.id || null, currentDecorationId: current?.decoration.id || null };
}
