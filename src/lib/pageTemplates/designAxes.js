import { pageTemplatesCatalog } from './catalog.js';
import { currentSectionDesignId, defaultSectionDesignId, sectionDesignChoices } from './designChoices.js';

// The axes are a picker projection. Every option resolves to one existing
// catalog design ID, and no axis state is serialized into blocks.
export function sectionDesignAxes(run, { storyboard = run?.blocks || [], product } = {}) {
  const choices = sectionDesignChoices(run, { storyboard, product });
  const defaultId = defaultSectionDesignId(run, storyboard);
  const currentId = currentSectionDesignId(run, storyboard);
  const currentDesign = pageTemplatesCatalog.designs[currentId];
  const current = currentDesign?.choiceAxes;
  const fallback = choices.find(design => design.id === defaultId) || choices[0];
  const active = current || fallback?.choiceAxes;
  const defaults = pageTemplatesCatalog.designs[defaultId]?.choiceAxes;
  const frames = choices.filter(design => design.choiceAxes?.decoration.id === 'frame');
  // Candidate frames already use the automatic count. Keep a compatible layout
  // or fall back to column, including an explicit action from a saved variant.
  const frameVariant = frames.find(design => design.choiceAxes.layout.id === active?.layout.id)
    || frames.find(design => design.choiceAxes.layout.id === 'column');
  const options = (axis, other) => {
    const groups = new Map();
    for (const design of choices) {
      const axes = design.choiceAxes, value = axes?.[axis];
      if (!value) continue;
      const frameDecoration = axis === 'decoration' && value.id === 'frame';
      if (!frameDecoration && axes[other]?.id !== active?.[other]?.id) continue;
      const list = groups.get(value.id) || [];
      list.push(design); groups.set(value.id, list);
    }
    return [...groups.values()].map(list => {
      const design = axis === 'decoration' && list[0].choiceAxes.decoration.id === 'frame'
        ? frameVariant : list[0];
      const value = design.choiceAxes[axis];
      return { id: value.id, name: value.name, designId: design.id,
        selected: Boolean(current && current[axis].id === value.id), isDefault: defaults?.[axis]?.id === value.id };
    }).sort((a, b) => Number(b.isDefault) - Number(a.isDefault));
  };
  return { hasAxes: choices.some(design => Boolean(design.choiceAxes)),
    layouts: options('layout', 'decoration'), decorations: options('decoration', 'layout'),
    currentLayoutId: current?.layout.id || null, currentDecorationId: current?.decoration.id || null };
}
