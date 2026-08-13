export const storyboardNotFoundError = () => ({
  kind: 'notFound',
  message: '작업을 찾을 수 없어요',
});

export const classifyStoryboardLoadError = (
  error,
  networkMessage = '생성예시 카탈로그를 불러오지 못했어요',
) => (
  error?.status === 404
    ? storyboardNotFoundError()
    : { kind: 'network', message: networkMessage }
);
