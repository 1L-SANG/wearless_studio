export function storyboardTransitionState(draft, mannequinStageEnabled = true) {
  if (!mannequinStageEnabled) return {
    customMatchPromotionStarted: Boolean(draft?.customMatch?.uploads?.length),
  };
  return {
    showMannequinTransition: true,
    customMatchPromotionStarted: Boolean(draft?.customMatch?.uploads?.length),
  };
}

export function invalidateStoryboardForProductPhotoEdit(projectId, invalidate) {
  if (projectId) invalidate(projectId);
}
