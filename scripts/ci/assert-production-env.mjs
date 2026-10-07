// 운영 빌드에서만 검사한다. 파일이나 process.env를 직접 읽지 않는 순수 함수다.
export function assertProductionEnv(env) {
  if (env.VERCEL_ENV !== 'production') return;

  const problems = [];
  if (env.VITE_API_MODE !== 'http') {
    problems.push('VITE_API_MODE: 운영에서는 정확히 http여야 합니다.');
  }

  const baseUrl = env.VITE_API_BASE_URL;
  let validHttpsUrl = false;
  if (typeof baseUrl === 'string' && baseUrl.startsWith('https://')) {
    try {
      const url = new URL(baseUrl);
      validHttpsUrl = url.protocol === 'https:' && Boolean(url.hostname);
    } catch { /* 잘못된 URL은 아래에서 알린다. */ }
  }
  if (!validHttpsUrl) {
    problems.push('VITE_API_BASE_URL: https://로 시작하는 올바른 API URL이 필요합니다.');
  }

  for (const [name, value] of Object.entries(env)) {
    if (name.startsWith('VITE_') && value === '[SENSITIVE]') {
      problems.push(`${name}: [SENSITIVE] 자리표시는 빌드할 수 없습니다. Vercel에서 해당 VITE_ 변수를 Sensitive가 아닌 값으로 다시 저장하세요.`);
    }
  }

  if (problems.length) {
    throw new Error(`운영 빌드 환경변수가 올바르지 않습니다.\n${problems.join('\n')}`);
  }
}
