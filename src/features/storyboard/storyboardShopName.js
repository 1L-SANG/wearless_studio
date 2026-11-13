import { api, isMockMode } from '../../lib/api/index.js';

// The local seller demo can run without a Supabase session. Its cache remains
// separate from authenticated users; HTTP mode never invents an owner.
export const storyboardShopOwner = userId => userId || (isMockMode ? 'local-mock' : null);

// Shared key with connection settings and the profile-menu cache. QueryFn
// returns the full safe connection view so those consumers can reuse it too.
export function storyboardShopQueryOptions(userId, hasFrame) {
  return {
    queryKey: ['mall-connections', userId],
    queryFn: () => api.listMalls(),
    enabled: Boolean(userId && hasFrame),
    staleTime: 30_000,
    retry: false,
    refetchOnWindowFocus: false,
  };
}
