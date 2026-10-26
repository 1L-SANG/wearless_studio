import { http } from './httpAdapter.js';

export function createDetailPublication(projectId, { preset, width, productName, files }) {
  return http(`/v1/projects/${encodeURIComponent(projectId)}/detail-publications`, {
    method: 'POST', body: { preset, width, productName, files },
  });
}

export function completeDetailPublication(publicationId, { uploads }) {
  return http(`/v1/detail-publications/${encodeURIComponent(publicationId)}/complete`, {
    method: 'POST', body: { uploads },
  });
}

export async function listDetailPublications(projectId) {
  try {
    return await http(`/v1/projects/${encodeURIComponent(projectId)}/detail-publications`, { suppressErrorLog: true });
  } catch (error) {
    if (error?.status === 404) return { items: [], disabled: true };
    throw error;
  }
}

export function deleteDetailPublication(publicationId) {
  return http(`/v1/detail-publications/${encodeURIComponent(publicationId)}`, { method: 'DELETE' });
}

// 목 게시 주소도 공개 URL 계약을 유지하되 바이트는 현재 탭 안에서만 옮긴다.
export async function publicationFetch(url, options) {
  if ((import.meta.env.VITE_API_MODE ?? 'mock') !== 'http'
    && String(url).startsWith('https://images.example.test/pub/')) {
    const { api } = await import('../../mock/api.js');
    return api.transferDetailPublication(url, options);
  }
  return fetch(url, options);
}
