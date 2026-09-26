/* 대기 타일의 '완성됐어요' 자리를 생성 중 미리보기로 채운다(2026-09-26, 오너 결정 b).

   REAL(실존 모델) 얼굴 컷은 cut_done 에 주소가 없다(492cbc64 — 최종 권한 확인 전 비공개).
   서버 미리보기 라우트가 요청마다 소유·라이선스를 다시 확인하고 바이트를 주면, 여기서
   objectURL 로 그린다. 거절(403)·없음(404)·장애는 조용히 '완성됐어요' 타일로 남는다.

   미리보기는 **블록 데이터에 넣지 않는다** — 이 컴포넌트(와 아래 모듈 캐시)의 상태로만 산다.
   그래서 임시 작업본·저장본에 blob: 주소가 남지 않고, 완료 병합의 안정 주소가 그대로 이긴다
   (src 가 채워지면 이 타일 자체가 사라지고 objectURL 도 거둔다). 잡이 실패·차단으로 끝나면
   보이던 미리보기도 거둔다 — 마감 재확인에서 라이선스가 철회됐을 수 있다. */
import { useEffect, useState } from 'react';
import { api } from '@/lib/api/index.js';
import { useAppStore } from '@/store/useAppStore.js';
import {
  createDetailCutPreviewCache,
  fetchDetailCutPreview,
} from '@/lib/detailCutPreview.js';
import { GenDoneTileBody } from '@/features/editor/genDoneTile.jsx';

// 캔버스·블록 썸네일·전체 미리보기가 같은 컷을 그려도 요청은 한 번, objectURL 은 하나.
const previewCache = createDetailCutPreviewCache({
  createObjectURL: (blob) => URL.createObjectURL(blob),
  revokeObjectURL: (url) => URL.revokeObjectURL(url),
});

function useDetailCutPreviewUrl({ projectId, jobId, blockId, enabled }) {
  const [state, setState] = useState({ key: '', url: null });
  const key = enabled && projectId && jobId && blockId ? `${projectId}:${jobId}:${blockId}` : '';
  useEffect(() => {
    if (!key || typeof api.detailCutPreview !== 'function') return undefined;   // mock 모드엔 없다
    return previewCache.acquire(
      key,
      (signal) => fetchDetailCutPreview({
        fetchBlob: (_path, opts) => api.detailCutPreview(projectId, jobId, blockId, opts),
        projectId, jobId, blockId, signal,
      }),
      (url) => setState({ key, url }),
    );
  // key 가 projectId·jobId·blockId·enabled 를 모두 담는다.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  // 다른 잡·다른 자리의 옛 주소(이미 거둔 objectURL)는 보이지 않는다.
  return key && state.key === key ? state.url : null;
}

export function GenDonePreview({ sourceBlockId }) {
  const projectId = useAppStore((s) => s.detailPageJob.projectId);
  const jobId = useAppStore((s) => s.detailPageJob.jobId);
  const status = useAppStore((s) => s.detailPageJob.status);
  // running → done 사이에는 그대로 둔다(완료 병합이 곧 안정 주소로 바꾼다). error·blocked 면 거둔다.
  const enabled = status === 'running' || status === 'done';
  const previewUrl = useDetailCutPreviewUrl({ projectId, jobId, blockId: sourceBlockId, enabled });
  return <GenDoneTileBody previewUrl={previewUrl} />;
}
