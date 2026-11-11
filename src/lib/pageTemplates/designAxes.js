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
  const frameVariant = list => {
    const preferred = current?.decoration.id === 'frame' ? currentDesign.framePhotos : 2;
    return list.find(design => design.framePhotos === preferred)
      || [...list].sort((a, b) => (b.framePhotos || 0) - (a.framePhotos || 0))
        .find(design => design.framePhotos <= preferred) || list[0];
  };
  const options = (axis, other) => {
    const groups = new Map();
    for (const design of choices) {
      const axes = design.choiceAxes, value = axes?.[axis];
      if (!value || axes[other]?.id !== active?.[other]?.id) continue;
      const list = groups.get(value.id) || [];
      list.push(design); groups.set(value.id, list);
    }
    return [...groups.values()].map(list => {
      const design = list[0].choiceAxes.decoration.id === 'frame' ? frameVariant(list) : list[0];
      const value = design.choiceAxes[axis];
      return [{ id: value.id, name: value.name, designId: design.id,
        selected: Boolean(current && current[axis].id === value.id), isDefault: defaults?.[axis]?.id === value.id }];
    }).flat().sort((a, b) => Number(b.isDefault) - Number(a.isDefault));
  };
  const frameChoices = current?.decoration.id === 'frame' ? choices.filter(design => design.choiceAxes?.decoration.id === 'frame'
    && design.choiceAxes.layout.id === current.layout.id).sort((a, b) => a.framePhotos - b.framePhotos) : [];
  const selectedFrame = frameVariant(frameChoices);
  return { hasAxes: choices.some(design => Boolean(design.choiceAxes)),
    layouts: options('layout', 'decoration'), decorations: options('decoration', 'layout'),
    framePhotoChoices: frameChoices.map(design => ({ count: design.framePhotos, designId: design.id, selected: design.id === selectedFrame?.id })),
    currentLayoutId: current?.layout.id || null, currentDecorationId: current?.decoration.id || null };
}
