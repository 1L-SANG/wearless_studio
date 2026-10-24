import JSZip from 'jszip';
import { getFontEmbedCSS } from 'html-to-image';
import { EXPORT_PRESETS, MAX_SPLIT_HEIGHT, MAX_JPEG_BYTES, captureBlockCanvasSlices, renderStorefrontFiles } from './editorExport.js';
import { publicationFetch } from '../../lib/api/detailPublications.js';

export const PUBLISH_PRESETS = Object.freeze(EXPORT_PRESETS.filter((preset) => !preset.original));
const FAILURE = '지금은 올리지 못했어요. 대신 ZIP으로 받아 두세요';
const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);

export function buildPublicationHtml(files, { productName, width }) {
  return `<div style="text-align:center;">\n${files.map((file, i) => `<img src="${escapeHtml(file.url)}" alt="${escapeHtml(productName)} 상세 ${i + 1}" style="display:block;width:100%;max-width:${Number(width)}px;margin:0 auto;">`).join('\n')}\n</div>`;
}

export function buildPublicationUrlList(files) {
  return files.map((file) => file.url).join('\n');
}

function withTimeout(promise, ms) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('응답이 없어 시간이 초과됐어요. 다시 시도해 주세요.')), ms);
    Promise.resolve(promise).then((value) => { clearTimeout(timer); resolve(value); }, (error) => { clearTimeout(timer); reject(error); });
  });
}

export async function uploadPublicationFiles(blobs, uploads, { fetchImpl = publicationFetch, concurrency = 3, onProgress, timeoutMs = 60000 } = {}) {
  const failed = [];
  let cursor = 0, done = 0;
  const worker = async () => {
    while (cursor < uploads.length) {
      const upload = uploads[cursor++];
      try {
        const response = await withTimeout(fetchImpl(upload.uploadUrl, {
          method: 'PUT', body: blobs[upload.index - 1], headers: { 'Content-Type': 'image/jpeg' },
        }), timeoutMs);
        if (!response.ok) failed.push(upload.index);
      } catch { failed.push(upload.index); }
      onProgress?.(++done, uploads.length, 'upload');
    }
  };
  await Promise.all(Array.from({ length: Math.min(uploads.length, Math.max(1, concurrency)) }, worker));
  return { failed: failed.sort((a, b) => a - b) };
}

async function readDimensions(blob) {
  const url = URL.createObjectURL(blob);
  try {
    return await withTimeout(new Promise((resolve, reject) => {
      const image = new Image();
      image.onload = () => resolve({ width: image.naturalWidth, height: image.naturalHeight });
      image.onerror = () => reject(new Error('이미지 크기를 확인하지 못했어요. 다시 시도해 주세요.'));
      image.src = url;
    }), 8000);
  } finally { URL.revokeObjectURL(url); }
}

export async function publishDetailPage(blockNodes, { projectId, productName, preset }, deps = {}) {
  const api = deps.api || (await import('../../lib/api/index.js')).api;
  const selected = PUBLISH_PRESETS.find((value) => value.id === preset) || PUBLISH_PRESETS[0];
  const timeouts = { network: 8000, complete: 30000, transfer: 60000, ...deps.timeouts };
  const imageCache = new Map();
  let files;
  try {
    const render = deps.renderStorefrontFiles || renderStorefrontFiles;
    let captureBlock = deps.captureBlock;
    if (!deps.renderStorefrontFiles && !captureBlock) {
      const fontEmbedCSS = await getFontEmbedCSS(blockNodes[0]);
      captureBlock = (node, ratio) => captureBlockCanvasSlices(node, ratio, fontEmbedCSS, MAX_SPLIT_HEIGHT, imageCache);
    }
    const rendered = await render(blockNodes, { preset: selected.id, format: 'jpeg', delivery: 'split' }, {
      ...deps, captureBlock,
      onProgress: (done, total) => deps.onProgress?.(done + 1, total, 'capture'),
    });
    files = rendered.files;
    deps.onRendered?.();
    if (!files.length) throw new Error('올릴 블록이 없어요. 상세페이지에 블록을 추가해 주세요.');
    if (files.length > 40 || files.some((blob) => blob.size > MAX_JPEG_BYTES)
      || files.reduce((sum, blob) => sum + blob.size, 0) > 60 * 1024 * 1024) throw new Error(FAILURE);
    const metadata = await Promise.all(files.map(async (blob, i) => ({
      index: i + 1, byteSize: blob.size, ...await (deps.readDimensions || readDimensions)(blob),
    })));
    deps.assertCurrent?.();
    const created = await withTimeout(api.createDetailPublication(projectId, {
      preset: selected.id, width: selected.width, productName, files: metadata,
    }), timeouts.network);
    const upload = async (indexes) => {
      let pending = created.uploads.filter((item) => indexes.includes(item.index));
      for (let attempt = 0; pending.length && attempt < 3; attempt++) {
        deps.assertCurrent?.();
        const { failed } = await uploadPublicationFiles(files, pending, {
          fetchImpl: deps.fetchImpl, timeoutMs: timeouts.transfer, onProgress: deps.onProgress,
        });
        pending = pending.filter((item) => failed.includes(item.index));
      }
      if (pending.length) throw new Error(FAILURE);
    };
    await upload(created.uploads.map((item) => item.index));
    const complete = () => {
      deps.assertCurrent?.();
      return withTimeout(api.completeDetailPublication(created.publicationId, {
        uploads: created.uploads.map(({ index, uploadToken }) => ({ index, uploadToken })),
      }), timeouts.complete);
    };
    let result;
    try { result = await complete(); }
    catch (error) {
      if (error?.status !== 409 || error?.code !== 'files_missing' || !Array.isArray(error.missing)) throw error;
      await upload(error.missing);
      result = await complete();
    }
    return { ...result, files: result.files.map((file, i) => ({ ...file, blob: files[i] })),
      html: buildPublicationHtml(result.files, { productName, width: selected.width }) };
  } catch (error) {
    if (files?.length) error.files = files;
    throw error;
  } finally { imageCache.clear(); }
}

export async function downloadPublicationZip(files, productName, { fetchImpl = publicationFetch } = {}) {
  const zip = new JSZip();
  for (let i = 0; i < files.length; i++) {
    let blob = files[i] instanceof Blob ? files[i] : files[i].blob;
    if (!blob) {
      const response = await withTimeout(fetchImpl(files[i].url), 60000);
      if (!response.ok) throw new Error('이미지를 받지 못했어요. 다시 시도해 주세요.');
      blob = await withTimeout(response.blob(), 60000);
    }
    zip.file(`${String(i + 1).padStart(2, '0')}.jpg`, await blob.arrayBuffer());
  }
  return zip.generateAsync({ type: 'blob' });
}
