import { useQuery } from '@tanstack/react-query';
import { useAppStore } from '../../store/useAppStore.js';
import { pageTemplatesCatalog } from '../../lib/pageTemplates/catalog.js';
import { selectFrameShopName } from '../../lib/frameShopName.js';
import { storyboardShopOwner, storyboardShopQueryOptions } from './storyboardShopName.js';

export function useStoryboardShopName(blocks) {
  const accountUserId = useAppStore(state => state.accountUserId);
  const userId = storyboardShopOwner(accountUserId);
  const hasFrame = (blocks || []).some(block => block.taxonomyVersion === 4
    && pageTemplatesCatalog.designs[block.pageDesignId]?.framePhotos);
  const { data, isError } = useQuery(storyboardShopQueryOptions(userId, hasFrame));
  // A failed refresh must not leave a stale store name on the public preview.
  return userId && !isError ? selectFrameShopName(data) : null;
}
