import { Placeholder } from '../mock/placeholders.js';
import { referenceScopeForBlock } from './referenceMode.js';
import { preserveDetailTargetBinding, isProductDetail } from './detailRecommendations.js';
import { directionSpecFromExample } from './storyboardExampleSelection.js';

const exampleThumbFor = (catalogs, exampleId, cut) => (catalogs.genExamples || []).find(example => example.id === exampleId)?.thumb
  || Placeholder.photo(exampleId, cut === 'product' ? 'product' : cut, 240, 320);
export function referenceFeedbackPatch(block, changes, catalogs) {
  if (!block) return changes;
  const exampleId = Object.prototype.hasOwnProperty.call(changes, 'exampleId') ? changes.exampleId : block.exampleId;
  const effScope = referenceScopeForBlock({ ...block, ...changes });
  const next = { ...preserveDetailTargetBinding(block, changes), refScope: effScope };
  if (isProductDetail({ ...block, ...changes })) return next;
  const example = (catalogs.genExamples || []).find((item) => item.id === exampleId);
  if ((changes.cutType ?? block.cutType) !== 'product' && example?.direction) {
    Object.assign(next, directionSpecFromExample(example));
  }
  if (exampleId && effScope === 'all') {
    if (block.baseThumb == null) next.baseThumb = block.thumb;
    next.thumb = exampleThumbFor(catalogs, exampleId, changes.cutType ?? block.cutType);
  } else if (block.baseThumb != null) {
    next.thumb = block.baseThumb;
  }
  return next;
}


