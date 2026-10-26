import { http } from './httpAdapter.js';

export async function listMalls() {
  try {
    const response = await http('/v1/malls', { suppressErrorLog: true });
    if (!response.items.some((item) => item.mall === 'cafe24')) {
      return { ...response, items: [...response.items, { mall: 'cafe24', status: 'unavailable' }] };
    }
    return response;
  } catch (error) {
    if (error?.status === 404) return { items: [], disabled: true };
    throw error;
  }
}

export function connectZigzag({ accessKey, secretKey }) {
  return http('/v1/malls/zigzag/connect', { method: 'POST', body: { accessKey, secretKey }, suppressErrorLog: true });
}

export function rotateZigzag({ accessKey, secretKey }) {
  return http('/v1/malls/zigzag/rotate', { method: 'POST', body: { accessKey, secretKey }, suppressErrorLog: true });
}

export function disconnectZigzag() {
  return http('/v1/malls/zigzag', { method: 'DELETE' });
}

export function listZigzagAddresses() {
  return http('/v1/malls/zigzag/addresses');
}

export function saveZigzagDefaults(payload) {
  return http('/v1/malls/zigzag/defaults', { method: 'PUT', body: payload });
}

export function startCafe24Install({ mallId, returnTo }) {
  const normalized = String(mallId ?? '').trim().toLowerCase().replace(/^https?:\/\//, '')
    .split(/[/?#]/, 1)[0].replace(/\.(cafe24|cafe24api)\.com$/, '');
  return http('/v1/malls/cafe24/install', {
    method: 'POST', body: { mallId: normalized, returnTo }, credentials: 'include', suppressErrorLog: true,
  });
}

export function disconnectCafe24() {
  return http('/v1/malls/cafe24', { method: 'DELETE' });
}

export function listCafe24Categories() {
  return http('/v1/malls/cafe24/categories');
}

export function createCafe24Registration(payload) {
  return http('/v1/malls/cafe24/registrations', { method: 'POST', body: payload });
}

export function getCafe24Registration(jobId) {
  return http(`/v1/malls/cafe24/registrations/${jobId}`);
}

export function verifyCafe24Registration(jobId) {
  return http(`/v1/malls/cafe24/registrations/${jobId}/verify`, { method: 'POST' });
}

export function listMallRegistrations(projectId) {
  return http(`/v1/projects/${projectId}/mall-registrations`);
}
