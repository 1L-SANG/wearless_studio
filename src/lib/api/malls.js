import { http } from './httpAdapter.js';

export async function listMalls() {
  try {
    return await http('/v1/malls', { suppressErrorLog: true });
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
