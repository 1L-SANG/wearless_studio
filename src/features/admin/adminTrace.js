/* 관리자 출처 추적 화면의 순수 헬퍼(2026-09-26) — 테스트가 이 파일만 불러 본다.

   신뢰도 문구가 이 화면의 전부다. '워터마크 일치'는 파일 안에 박힌 코드가 원장과 맞았다는 뜻이라
   사실상 확정이고, '유사도'는 비슷한 그림이라는 뜻일 뿐이라 사람이 눈으로 확인해야 한다.
   둘을 같은 말투로 보여 주면 담당자가 유사도 후보를 확정으로 믿는다 — 그게 이 화면의 가장 큰 사고다. */

export const TRACE_MAX_BYTES = 60 * 1024 * 1024; // 서버 MAX_TRACE_BYTES 와 같게

const CONFIDENCE = {
  high: { label: '워터마크 일치', variant: 'default', hint: '파일에 박힌 배포 코드가 원장과 맞아요. 이 배포본에서 나온 파일이에요.' },
  medium: { label: '유사도 높음', variant: 'secondary', hint: '그림이 거의 같아요. 워터마크는 확인되지 않았으니 눈으로 대조해 주세요.' },
  low: { label: '유사도 참고', variant: 'outline', hint: '비슷한 부분이 있어요. 같은 템플릿·같은 사진일 수 있으니 참고만 해 주세요.' },
};

export function confidenceView(confidence) {
  return CONFIDENCE[confidence] || CONFIDENCE.low;
}

export function watermarkView(watermark) {
  const status = watermark?.status;
  if (status === 'matched') {
    return { tone: 'ok', title: `워터마크 확인 · 코드 ${watermark.code}`, detail: `${watermark.votes}개 구간에서 같은 코드를 읽었어요.` };
  }
  if (status === 'unregistered') {
    return { tone: 'warn', title: `코드 ${watermark.code}를 읽었지만 원장에 없어요`, detail: '다른 환경(테스트 서버 등)에서 받은 파일일 수 있어요.' };
  }
  return { tone: 'none', title: '워터마크를 찾지 못했어요', detail: '강한 재압축·자르기·필터로 지워졌을 수 있어요. 아래 유사도 후보를 확인해 주세요.' };
}

export function evidenceText(evidence) {
  if (!evidence) return '';
  const parts = [];
  if (evidence.watermark) parts.push('워터마크');
  if (evidence.phashDistance != null) {
    const where = evidence.matchedKind === 'cut' ? '생성 컷'
      : evidence.matchedKind === 'cut_crop' ? '생성 컷(잘린 썸네일)'
      : evidence.matchedKind === 'strip' ? `페이지 구간 ${evidence.region?.y0 ?? 0}~${evidence.region?.y1 ?? 0}px`
        : '배포본 전체';
    parts.push(`${where} · 차이 ${evidence.phashDistance}/64`);
  }
  return parts.join(' + ');
}

export function licenseStatusLabel(status) {
  return {
    active: '유효', revoked: '철회됨', expired: '만료', suspended: '정지', deleted: '삭제됨',
  }[status] || (status ? status : '알 수 없음');
}

export function targetLabel(candidate) {
  if (candidate?.target === 'cut') return '생성 컷';
  return {
    long_png: '배포본 · 긴 PNG', block_png: '배포본 · 블록 PNG', zip: '배포본 · ZIP',
  }[candidate?.publicationKind] || '배포본';
}

// 올리기 전 거르기 — 서버도 같은 규칙으로 막지만, 60MB 를 다 올린 뒤에 거절당하면 느리다.
export function validateTraceFile(file) {
  if (!file) return '이미지 파일을 골라 주세요.';
  if (file.type && !file.type.startsWith('image/')) return '이미지 파일만 올릴 수 있어요.';
  if (file.size > TRACE_MAX_BYTES) return '이미지는 60MB 이하만 올릴 수 있어요.';
  if (file.size === 0) return '빈 파일은 사용할 수 없어요.';
  return '';
}
