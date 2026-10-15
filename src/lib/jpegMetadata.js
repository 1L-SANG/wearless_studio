// Best-effort privacy for ordinary product JPEGs, without decoding/recompressing pixels.
// Unsupported containers (including signed JUMBF/C2PA and multi-picture files) stay intact.
const startsWith = (bytes, text) => bytes.length >= text.length
  && [...text].every((char, index) => bytes[index] === char.charCodeAt(0));

function orientationOnly(exif) {
  const tiff = exif.subarray(6);
  const little = startsWith(tiff, 'II');
  if ((!little && !startsWith(tiff, 'MM')) || tiff.length < 8) throw new Error('TIFF');
  const view = new DataView(tiff.buffer, tiff.byteOffset, tiff.byteLength);
  if (view.getUint16(2, little) !== 42) throw new Error('TIFF');
  const offset = view.getUint32(4, little);
  if (offset < 8 || offset + 2 > tiff.length) throw new Error('IFD');
  const count = view.getUint16(offset, little);
  if (offset + 2 + count * 12 + 4 > tiff.length) throw new Error('IFD');
  let orientation;
  for (let i = 0; i < count; i += 1) {
    const entry = offset + 2 + i * 12;
    if (view.getUint16(entry, little) !== 0x0112) continue;
    const value = view.getUint16(entry + 8, little);
    if (orientation !== undefined || view.getUint16(entry + 2, little) !== 3
      || view.getUint32(entry + 4, little) !== 1 || value < 1 || value > 8) {
      throw new Error('Orientation');
    }
    orientation = value;
  }
  if (orientation === undefined) return new Uint8Array();
  // A single little-endian IFD0 Orientation SHORT, no linked IFDs or thumbnails.
  return new Uint8Array([
    0xff, 0xe1, 0, 34, 69, 120, 105, 102, 0, 0, 73, 73, 42, 0, 8, 0, 0, 0,
    1, 0, 18, 1, 3, 0, 1, 0, 0, 0, orientation, 0, 0, 0, 0, 0, 0, 0,
  ]);
}

function withoutIptc(payload) {
  const view = new DataView(payload.buffer, payload.byteOffset, payload.byteLength);
  const parts = [payload.subarray(0, 14)];
  let offset = 14;
  let removed = false;
  while (offset < payload.length) {
    const start = offset;
    if (!startsWith(payload.subarray(offset), '8BIM') || offset + 7 > payload.length) throw new Error('IPTC');
    const id = view.getUint16(offset + 4);
    const nameSize = 1 + payload[offset + 6];
    offset += 6 + nameSize + (nameSize % 2);
    if (offset + 4 > payload.length) throw new Error('IPTC');
    const size = view.getUint32(offset);
    offset += 4 + size + (size % 2);
    if (offset > payload.length) throw new Error('IPTC');
    if (id === 0x0404) removed = true;
    else parts.push(payload.subarray(start, offset));
  }
  if (!removed) return null;
  if (parts.length === 1) return new Uint8Array();
  const length = parts.reduce((total, part) => total + part.length, 2);
  const result = new Uint8Array(length + 2);
  result.set([0xff, 0xed, length >> 8, length & 255]);
  offset = 4;
  for (const part of parts) { result.set(part, offset); offset += part.length; }
  return result;
}

function cleanedParts(bytes) {
  if (bytes[0] !== 0xff || bytes[1] !== 0xd8) return null;
  const parts = [bytes.subarray(0, 2)];
  let offset = 2;
  let changed = false;
  let sawFrame = false;
  let sawScan = false;
  let sawExif = false;
  while (offset < bytes.length) {
    const start = offset;
    if (bytes[offset++] !== 0xff) return null;
    while (bytes[offset] === 0xff) offset += 1;
    const marker = bytes[offset++];
    if (marker === 0xd9) {
      // Extra images/trailing data may contain offsets that a rewrite would invalidate.
      if (!sawFrame || !sawScan || offset !== bytes.length) return null;
      parts.push(bytes.subarray(start, offset));
      return changed ? parts : null;
    }
    const app = marker >= 0xe0 && marker <= 0xef;
    if (!app && ![0xc0, 0xc1, 0xc2, 0xc4, 0xdb, 0xdd, 0xda, 0xfe].includes(marker)) return null;
    if (offset + 2 > bytes.length) return null;
    const length = bytes[offset] * 256 + bytes[offset + 1];
    const end = offset + length;
    if (length < 2 || end > bytes.length) return null;
    const payload = bytes.subarray(offset + 2, end);
    const original = bytes.subarray(start, end);
    let replacement = original;
    if (marker === 0xeb || (marker === 0xe2 && startsWith(payload, 'MPF\0'))) return null;
    const frame = [0xc0, 0xc1, 0xc2].includes(marker);
    if (frame && (payload.length < 6 || payload[0] !== 8 || !payload[5] || payload[5] > 4
      || payload.length !== 6 + payload[5] * 3)) return null;
    if (marker === 0xda && (!payload[0] || payload[0] > 4
      || payload.length !== 4 + payload[0] * 2)) return null;
    if (marker === 0xe1 && startsWith(payload, 'Exif\0\0')) {
      if (sawExif) return null;
      sawExif = true;
      replacement = orientationOnly(payload);
    } else if (marker === 0xed && startsWith(payload, 'Photoshop 3.0\0')) {
      replacement = withoutIptc(payload) ?? original;
    } else if (marker === 0xfe
      || (marker === 0xe1 && (startsWith(payload, 'http://ns.adobe.com/xap/1.0/\0')
        || startsWith(payload, 'http://ns.adobe.com/xmp/extension/\0')))) {
      replacement = new Uint8Array();
    }
    if (replacement.length !== original.length
      || replacement.some((value, index) => value !== original[index])) changed = true;
    parts.push(replacement);
    offset = end;
    if (frame) sawFrame = true;
    if (marker !== 0xda) continue;
    if (!sawFrame) return null;
    sawScan = true;
    const scanStart = offset;
    // Preserve entropy data, stuffed FF00 bytes and restart markers verbatim.
    while (offset < bytes.length) {
      if (bytes[offset] !== 0xff) { offset += 1; continue; }
      let next = offset + 1;
      while (bytes[next] === 0xff) next += 1;
      if (bytes[next] === 0 || (bytes[next] >= 0xd0 && bytes[next] <= 0xd7)) {
        offset = next + 1;
      } else break;
    }
    parts.push(bytes.subarray(scanStart, offset));
  }
  return null;
}

export async function stripJpegMetadata(blob) {
  try {
    const head = new Uint8Array(await blob.slice(0, 2).arrayBuffer());
    if (head[0] !== 0xff || head[1] !== 0xd8) return blob;
    const parts = cleanedParts(new Uint8Array(await blob.arrayBuffer()));
    if (!parts) return blob;
    return typeof File !== 'undefined' && blob instanceof File
      ? new File(parts, blob.name, { type: blob.type, lastModified: blob.lastModified })
      : new Blob(parts, { type: blob.type });
  } catch {
    return blob; // Malformed/unsupported metadata must not block an otherwise usable photo.
  }
}
