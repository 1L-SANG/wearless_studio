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

export function getSalesDefaults() {
  return http('/v1/malls/sales-defaults');
}

export function saveSalesDefaults({ baseRevision, ...patch }) {
  return http('/v1/malls/sales-defaults', { method: 'PUT', body: { baseRevision, ...patch } });
}

export function getMallDefaults(mall) {
  return http(`/v1/malls/${encodeURIComponent(mall)}/registration-defaults`);
}

export function saveMallDefaults(mall, { baseRevision, addresses }) {
  return http(`/v1/malls/${encodeURIComponent(mall)}/registration-defaults`, { method: 'PUT', body: { baseRevision, addresses } });
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

export function getMallRegistrationCatalog(mall) {
  return http(`/v1/malls/${encodeURIComponent(mall)}/registration-catalog`);
}

export function preflightMallRegistration(draft) {
  return http('/v1/malls/registrations/preflight', { method: 'POST', body: draft });
}

export function createMallRegistrationBatch({ batchId, draft }) {
  return http('/v1/malls/registration-batches', { method: 'POST', body: { batchId, draft } });
}

export function getMallRegistrationBatch(batchId) {
  return http(`/v1/malls/registration-batches/${encodeURIComponent(batchId)}`);
}

export function getMallRegistration(jobId) {
  return http(`/v1/malls/registrations/${encodeURIComponent(jobId)}`);
}

export function verifyMallRegistration(jobId) {
  return http(`/v1/malls/registrations/${encodeURIComponent(jobId)}/verify`, { method: 'POST' });
}

export function resumeMallRegistration(jobId, { requestKey }) {
  return http(`/v1/malls/registrations/${encodeURIComponent(jobId)}/resume`, { method: 'POST', body: { requestKey } });
}

export function listMallRegistrations(projectId) {
  return http(`/v1/projects/${projectId}/mall-registrations`);
}
