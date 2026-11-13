import { useEffect } from 'react';

// The top navigation is a sibling of the page, while drawers/actions are body
// portals. Share only v5's named tokens for the active v4 board's lifetime.
export function useStoryboardTheme(active) {
  useEffect(() => {
    if (!active) return undefined;
    document.body.classList.add('sb-v5-theme');
    return () => document.body.classList.remove('sb-v5-theme');
  }, [active]);
}
