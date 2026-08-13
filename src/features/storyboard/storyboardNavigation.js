export const STORYBOARD_SAVE_FAILURE_MESSAGE = '변경 내용을 저장하지 못했어요';

export async function continueAfterStoryboardFlush({ flush, navigate, onFailure }) {
  try {
    await flush();
  } catch (error) {
    onFailure(error?.message || STORYBOARD_SAVE_FAILURE_MESSAGE);
    return false;
  }
  navigate();
  return true;
}
