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
  const frameCounts = [...new Set(frames.map(design => design.framePhotos))].sort((a, b) => a - b);
  const currentFrameCount = current?.decoration.id === 'frame' ? currentDesign.framePhotos : null;
  const frameVariant = count => {
    const variants = frames.filter(design => design.framePhotos === count);
    return variants.find(design => design.choiceAxes.layout.id === active?.layout.id)
      || variants.find(design => design.choiceAxes.layout.id === 'column');
  };
  // Choose a count before a layout, including when entering frame from basic.
  const entryFrameCount = Math.min(currentFrameCount ?? 2, frameCounts.at(-1) || 0);
  const options = (axis, other) => {
    const groups = new Map();
    for (const design of choices) {
      const axes = design.choiceAxes, value = axes?.[axis];
      if (!value) continue;
      const frameDecoration = axis === 'decoration' && value.id === 'frame';
      if (!frameDecoration && axes[other]?.id !== active?.[other]?.id) continue;
      // A layout option never borrows a different frame count to become valid.
      if (axis === 'layout' && axes.decoration.id === 'frame' && design.framePhotos !== currentFrameCount) continue;
      const list = groups.get(value.id) || [];
      list.push(design); groups.set(value.id, list);
    }
    return [...groups.values()].map(list => {
      const design = axis === 'decoration' && list[0].choiceAxes.decoration.id === 'frame'
        ? frameVariant(entryFrameCount) : list[0];
      const value = design.choiceAxes[axis];
      return { id: value.id, name: value.name, designId: design.id,
        selected: Boolean(current && current[axis].id === value.id), isDefault: defaults?.[axis]?.id === value.id };
    }).sort((a, b) => Number(b.isDefault) - Number(a.isDefault));
  };
  return { hasAxes: choices.some(design => Boolean(design.choiceAxes)),
    layouts: options('layout', 'decoration'), decorations: options('decoration', 'layout'),
    // A legacy invalid layout remains current until an explicit selection. Its
    // count stays selected while that option resolves to a valid column design.
    framePhotoChoices: currentFrameCount == null ? [] : frameCounts.map(count => ({
      count, designId: frameVariant(count).id, selected: count === currentFrameCount,
    })),
    currentLayoutId: current?.layout.id || null, currentDecorationId: current?.decoration.id || null };
}
