const USER_IMAGE_SOURCES = new Set(['mine', 'upload', 'uploaded', 'user']);

function normalizedSrc(value) {
  return typeof value === 'string' ? value.trim() : '';
}

function isDirectUploadSource(source) {
  return USER_IMAGE_SOURCES.has(String(source || '').toLowerCase());
}

/**
 * Wardrobe tiles keep their source id, but canvas insertions receive a new element id.
 * Compare both id and source URL so copies and frame fills cannot delete their source
 * out from under the document that is currently being edited.
 */
export function isWardrobeImageUsed(blocks = [], image = null) {
  const imageId = String(image?.id || '').trim();
  const imageSrc = normalizedSrc(image?.src);
  if (!imageId && !imageSrc) return false;

  return (blocks || []).some((block) => (block?.elements || []).some((element) => (
    element?.type === 'image'
    && ((imageId && String(element.id || '').trim() === imageId)
      || (imageSrc && normalizedSrc(element.src) === imageSrc))
  )));
}

function wardrobeImageFromElement(element, group) {
  return {
    id: element.id || `editor-image-${group}-${normalizedSrc(element.src)}`,
    src: normalizedSrc(element.src),
    ...(element.sourceBlockId ? { sourceBlockId: element.sourceBlockId } : {}),
    cutType: element.cutType || null,
    width: Number(element.width || element.w) || undefined,
    height: Number(element.height || element.h) || undefined,
    ...(group === 'misc'
      ? { userUploaded: true, wardrobeGroup: 'misc' }
      : { generated: true, wardrobeGroup: group }),
  };
}

/**
 * The wardrobe API stores editor-generated variants, while the finished detail-page
 * photos live inside editor blocks. Build one library view from both sources without
 * duplicating an image that is already present in the API response.
 */
export function mergeEditorImagesIntoWardrobe({
  wardrobe = {},
  blocks = [],
  storyboard = [],
  colorIds = [],
  fallbackColorId = null,
} = {}) {
  const orderedColorIds = [...new Set((colorIds || []).filter((id) => id && id !== 'misc'))];
  const knownGroups = Object.keys(wardrobe || {}).filter((group) => group !== 'misc');
  const groupOrder = [...new Set([...orderedColorIds, ...knownGroups])];
  const output = Object.fromEntries(groupOrder.map((group) => [group, []]));
  output.misc = [];

  const sourceById = new Map((storyboard || []).filter(Boolean).map((item) => [item.id, item]));
  const defaultColorId = fallbackColorId || orderedColorIds[0] || knownGroups[0] || 'misc';
  const editorImages = [];

  for (const block of blocks || []) {
    const customUploadBlock = block?.contentRole === 'custom' && block?.name === '내 이미지';
    for (const element of block?.elements || []) {
      if (element?.type !== 'image' || !normalizedSrc(element.src)) continue;
      const source = element.sourceBlockId ? sourceById.get(element.sourceBlockId) : null;
      const directUpload = Boolean(element.userUploaded)
        || element.wardrobeGroup === 'misc'
        || customUploadBlock
        || isDirectUploadSource(source?.source);
      if (!directUpload && !element.sourceBlockId) continue;

      const group = directUpload ? 'misc' : (source?.colorId || defaultColorId);
      editorImages.push({ group, image: wardrobeImageFromElement(element, group) });
    }
  }

  // 생성 중 provider preview와 완료 후 안정 asset URL은 같은 캔버스 요소다. 이전 병합에서
  // 의류 탭에 파생시킨 preview를 먼저 버려야 URL이 바뀌어도 12개가 24개로 불어나지 않는다.
  const currentEditorIds = new Set(editorImages.map(({ image }) => image.id).filter(Boolean));
  const currentSourceBlockIds = new Set(editorImages.map(({ image }) => image.sourceBlockId).filter(Boolean));
  const isSupersededEditorImage = (image) => Boolean(image?.generated) && (
    currentEditorIds.has(image.id)
    || (image.sourceBlockId && currentSourceBlockIds.has(image.sourceBlockId))
  );

  const seenSrc = new Set();
  const seenPendingId = new Set();
  const append = (group, image) => {
    const src = normalizedSrc(image?.src);
    /* 생성 중·'조금 더 걸려요' 타일은 아직 src 가 없다. 여기서 떨구면 셀러가 뭐라도
       편집하는 순간(블록이 바뀌면 이 함수가 다시 돈다) 표시가 사라져, 실패한 줄 알고
       다시 생성한다 = 같은 컷에 크레딧이 두 번 나간다. 이 표시가 존재하는 이유가
       바로 그 이중 결제를 막는 것이다(2026-08-17 리뷰). */
    if (!src) {
      const pendingId = (image?.loading || image?.slow) && image?.id;
      if (!pendingId || seenPendingId.has(pendingId)) return;
      if (!output[group]) output[group] = [];
      output[group].push({ ...image });
      seenPendingId.add(pendingId);
      return;
    }
    if (seenSrc.has(src)) return;
    if (!output[group]) output[group] = [];
    output[group].push({ ...image, src });
    seenSrc.add(src);
  };

  for (const group of [...groupOrder, 'misc']) {
    for (const image of wardrobe?.[group] || []) {
      if (!isSupersededEditorImage(image)) append(group, { ...image, wardrobeGroup: group });
    }
  }

  for (const { group, image } of editorImages) append(group, image);

  return Object.fromEntries(Object.entries(output).filter(([, images]) => images.length));
}
