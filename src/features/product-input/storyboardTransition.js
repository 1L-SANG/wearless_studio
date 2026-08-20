export function storyboardTransitionState(draft) {
  return {
    showMannequinTransition: true,
    customMatchPromotionStarted: Boolean(draft?.customMatch?.uploads?.length),
  };
}

export function invalidateStoryboardForProductPhotoEdit(projectId, invalidate) {
  if (projectId) invalidate(projectId);
}
