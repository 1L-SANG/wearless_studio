import * as taxonomy from '../../../src/lib/storyboardTaxonomy.js';
import * as examples from '../../../src/lib/generationExamples.js';
import * as details from '../../../src/lib/detailRecommendations.js';
import * as closure from '../../../src/lib/outerClosure.js';
import * as mine from '../../../src/lib/storyboardMineImages.js';
import { signatureExamplesForBlock } from '../../../src/lib/signatureCutPool.js';
import { photoSlotForBlock } from '../../../src/lib/pageTemplates/slots.js';
import { productSectionState } from '../../../src/lib/pageTemplates/productPresentation.js';
import { productSourceColorPatch } from '../../../src/lib/productSourcePhotos.js';
import { garmentColorSelectionPatch } from '../../../src/lib/garmentColors.js';
import { horizonBackgroundAvailability } from '../../../src/lib/horizonBackgroundAvailability.js';
import { generationExampleSelectionPatch } from '../../../src/lib/storyboardExampleSelection.js';
import { referenceFeedbackPatch } from '../../../src/lib/storyboardExampleFeedback.js';
import { hookHarness, loadStoryboardComponent } from './storyboardComponents.mjs';

export async function storyboardPhotoPanelHarness(initialProps, api = {}) {
  const h = hookHarness(), changes = [];
  let props = initialProps;
  const change = value => {
    const patch = typeof value === 'function' ? value(props.block) : value;
    changes.push(patch); props = { ...props, block: { ...props.block, ...patch } };
  };
  const Component = await loadStoryboardComponent('StoryboardPhotoPanel.jsx', 'StoryboardPhotoPanel', { ...h.hooks,
    ...taxonomy, ...examples, ...details, ...closure, ...mine, photoSlotForBlock, productSectionState, productSourceColorPatch, garmentColorSelectionPatch,
    signatureExamplesForBlock, horizonBackgroundAvailability, generationExampleSelectionPatch, referenceFeedbackPatch, api,
    Icon: 'svg', OuterClosureField: 'closure-field', DetailTargetPicker: 'detail-target', ProductSourcePicker: 'product-source',
    HorizonBackgroundControl: 'horizon-background', UnderlineTabs: 'underline-tabs', GarmentColorField: 'color-dots',
    ShotSegment: 'shot-segment', MineImageTab: 'mine-images', MoodGuide: 'mood-guide', MINE_SHOT_OPTION: { value: 'mine' },
  });
  return { changes,
    render(next = {}) { props = { catalogs: { genExamples: [], shotTypes: [{ value: 'full' }, { value: 'medium' }], productShotTypes: [{ value: 'ghost' }, { value: 'detail' }] },
      colorOpts: [], detailColorOpts: [], clothingType: 'top', onChange: change, onAtomicChange: async value => change(value), ...props, ...next };
      return h.render(Component, props); },
    get block() { return props.block; }, close: () => h.close(),
  };
}
