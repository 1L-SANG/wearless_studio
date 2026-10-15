import test from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import { toUploadableImage } from '../../src/lib/imageTranscode.js';
import { stripJpegMetadata } from '../../src/lib/jpegMetadata.js';

// Real 8x6 RGB JPEGs and sRGB profile, generated locally with Pillow (no user photos).
const baseline = Buffer.from('/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAMCAgMCAgMDAwMEAwMEBQgFBQQEBQoHBwYIDAoMDAsKCwsNDhIQDQ4RDgsLEBYQERMUFRUVDA8XGBYUGBIUFRT/2wBDAQMEBAUEBQkFBQkUDQsNFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBT/wAARCAAGAAgDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAj/xAAdEAAABgMBAAAAAAAAAAAAAAAAAQIFERIDE2Ex/8QAFAEBAAAAAAAAAAAAAAAAAAAAB//EAB0RAAICAgMBAAAAAAAAAAAAAAECBBEDBQAxkaH/2gAMAwEAAhEDEQA/AJzcHTK5a9qUJpMUIy9jvAAA9RImCBhWPGQKi3QHQs2fp4W7DYy9rJeZOyF8jVbHs0AB4ABz/9k=', 'base64');
const progressive = Buffer.from('/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAMCAgMCAgMDAwMEAwMEBQgFBQQEBQoHBwYIDAoMDAsKCwsNDhIQDQ4RDgsLEBYQERMUFRUVDA8XGBYUGBIUFRT/2wBDAQMEBAUEBQkFBQkUDQsNFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBT/wgARCAAGAAgDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAf/xAAUAQEAAAAAAAAAAAAAAAAAAAAG/9oADAMBAAIQAxAAAAGch6W//8QAFhABAQEAAAAAAAAAAAAAAAAAAwAB/9oACAEBAAEFAkXUv//EABoRAAICAwAAAAAAAAAAAAAAAAMEAQIAEUH/2gAIAQMBAT8BYYK0STHtu09z/8QAGBEAAwEBAAAAAAAAAAAAAAAAAQIDABH/2gAIAQIBAT8BlJIIJzHAN//EABYQAAMAAAAAAAAAAAAAAAAAAAABEf/aAAgBAQAGPwJU/8QAGBAAAgMAAAAAAAAAAAAAAAAAAAERIVH/2gAIAQEAAT8hoRKMP//aAAwDAQACAAMAAAAQA//EABgRAQEAAwAAAAAAAAAAAAAAAAERADGR/9oACAEDAQE/EF8SVbYAcAM//8QAGBEBAQADAAAAAAAAAAAAAAAAAREAMaH/2gAIAQIBAT8QPHVA0Vr1z//EABkQAAIDAQAAAAAAAAAAAAAAAAERADFhQf/aAAgBAQABPxDpKpSLW5P/2Q==', 'base64');
const icc = Buffer.from('AAACTGxjbXMEQAAAbW50clJHQiBYWVogB+oACgAPAAYAMQA0YWNzcEFQUEwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAPbWAAEAAAAA0y1sY21zAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAALZGVzYwAAAQgAAAA2Y3BydAAAAUAAAABMd3RwdAAAAYwAAAAUY2hhZAAAAaAAAAAsclhZWgAAAcwAAAAUYlhZWgAAAeAAAAAUZ1hZWgAAAfQAAAAUclRSQwAAAggAAAAgZ1RSQwAAAggAAAAgYlRSQwAAAggAAAAgY2hybQAAAigAAAAkbWx1YwAAAAAAAAABAAAADGVuVVMAAAAaAAAAHABzAFIARwBCACAAYgB1AGkAbAB0AC0AaQBuAABtbHVjAAAAAAAAAAEAAAAMZW5VUwAAADAAAAAcAE4AbwAgAGMAbwBwAHkAcgBpAGcAaAB0ACwAIAB1AHMAZQAgAGYAcgBlAGUAbAB5WFlaIAAAAAAAAPbWAAEAAAAA0y1zZjMyAAAAAAABDEIAAAXe///zJQAAB5MAAP2Q///7of///aIAAAPcAADAblhZWiAAAAAAAABvoAAAOPUAAAOQWFlaIAAAAAAAACSfAAAPhAAAtsNYWVogAAAAAAAAYpcAALeHAAAY2XBhcmEAAAAAAAMAAAACZmYAAPKnAAANWQAAE9AAAApbY2hybQAAAAAAAwAAAACj1wAAVHsAAEzNAACZmgAAJmYAAA9c', 'base64');

function segment(marker, payload) {
  const header = Buffer.from([0xff, marker, 0, 0]);
  header.writeUInt16BE(payload.length + 2, 2);
  return Buffer.concat([header, payload]);
}

function privateExif(orientation, littleEndian = true) {
  const tiff = Buffer.alloc(100);
  const view = new DataView(tiff.buffer, tiff.byteOffset, tiff.byteLength);
  tiff.write(littleEndian ? 'II' : 'MM');
  const u16 = (offset, value) => view.setUint16(offset, value, littleEndian);
  const u32 = (offset, value) => view.setUint32(offset, value, littleEndian);
  u16(2, 42); u32(4, 8); u16(8, 3);
  // IFD0: camera make, orientation, GPS IFD pointer.
  u16(10, 0x010f); u16(12, 2); u32(14, 8); u32(18, 50);
  u16(22, 0x0112); u16(24, 3); u32(26, 1); u16(30, orientation);
  u16(34, 0x8825); u16(36, 4); u32(38, 1); u32(42, 58);
  tiff.write('PRIVATE\0', 50);
  u16(58, 1); u16(60, 2); u16(62, 5); u32(64, 3); u32(68, 76);
  for (let i = 0; i < 3; i += 1) { u32(76 + i * 8, 37 + i); u32(80 + i * 8, 1); }
  return segment(0xe1, Buffer.concat([Buffer.from('Exif\0\0'), tiff]));
}

function orientationExif(value) {
  const bytes = Buffer.from('ffe1002245786966000049492a0008000000010012010300010000000100000000000000', 'hex');
  bytes.writeUInt16LE(value, 28);
  return bytes;
}

const colorSegment = segment(0xe2, Buffer.concat([Buffer.from('ICC_PROFILE\0\x01\x01'), icc]));
const xmp = segment(0xe1, Buffer.from('http://ns.adobe.com/xap/1.0/\0<gps>PRIVATE</gps>'));
function photoshopResource(id, data) {
  const header = Buffer.from('3842494d0000000000000000', 'hex');
  header.writeUInt16BE(id, 4);
  header.writeUInt32BE(data.length, 8);
  return Buffer.concat([header, data, Buffer.alloc(data.length % 2)]);
}
const iptcResource = photoshopResource(0x0404, Buffer.from('PRIVATE location'));
const iptc = segment(0xed, Buffer.concat([Buffer.from('Photoshop 3.0\0'), iptcResource]));
const comment = segment(0xfe, Buffer.from('PRIVATE camera location'));
const annotate = (jpeg, orientation = 6, littleEndian = true) => Buffer.concat([
  jpeg.subarray(0, 2), privateExif(orientation, littleEndian), xmp, iptc, comment,
  colorSegment, jpeg.subarray(2),
]);
const expected = (jpeg, orientation = 6) => Buffer.concat([
  jpeg.subarray(0, 2), orientationExif(orientation), colorSegment, jpeg.subarray(2),
]);

test('small product JPEG loses GPS/camera metadata without reencoding or losing orientation/ICC', async (t) => {
  const originalBitmap = globalThis.createImageBitmap;
  globalThis.createImageBitmap = async () => ({ width: 8, height: 6, close() {} });
  t.after(() => { globalThis.createImageBitmap = originalBitmap; });
  const file = new File([annotate(baseline)], '상품.jpeg', { type: 'image/jpeg', lastModified: 123 });
  const result = await toUploadableImage(file);
  assert.deepEqual(Buffer.from(await result.arrayBuffer()), expected(baseline));
  assert.equal(result.name, file.name);
  assert.equal(result.lastModified, file.lastModified);
  assert.equal(result.type, file.type);
});

test('all EXIF orientations survive both TIFF byte orders without camera/GPS fields', async () => {
  for (const little of [true, false]) {
    for (let orientation = 1; orientation <= 8; orientation += 1) {
      const result = await stripJpegMetadata(new Blob([annotate(baseline, orientation, little)]));
      assert.deepEqual(Buffer.from(await result.arrayBuffer()), expected(baseline, orientation));
    }
  }
});

test('progressive scans and metadata between scans are handled without touching compressed data', async () => {
  const afterFirstScan = progressive.indexOf(Buffer.from([0xff, 0xc4]), progressive.indexOf(Buffer.from([0xff, 0xda])));
  const extraXmp = segment(0xe1, Buffer.from('http://ns.adobe.com/xmp/extension/\0PRIVATE'));
  const input = Buffer.concat([progressive.subarray(0, afterFirstScan), extraXmp, comment, progressive.subarray(afterFirstScan)]);
  const result = await stripJpegMetadata(new Blob([annotate(input)]));
  assert.deepEqual(Buffer.from(await result.arrayBuffer()), expected(progressive));
});

test('stuffed bytes, restart markers and marker fill bytes are not mistaken for metadata', async () => {
  const scan = baseline.indexOf(Buffer.from([0xff, 0xda]));
  const entropy = scan + 2 + baseline.readUInt16BE(scan + 2);
  const scanBytes = Buffer.from([1, 0xff, 0, 0xe1, 2, 0xff, 0xd0, 3, 0xff, 0xff, 0xd7, 4]);
  const jpeg = Buffer.concat([baseline.subarray(0, entropy), scanBytes, baseline.subarray(entropy)]);
  const result = await stripJpegMetadata(new Blob([annotate(jpeg)]));
  assert.deepEqual(Buffer.from(await result.arrayBuffer()), expected(jpeg));
});

test('a JPEG without an orientation drops all EXIF rather than inventing a rotation', async () => {
  const exif = privateExif(6);
  exif.writeUInt16LE(0x013b, 32); // Replace IFD0 Orientation tag with Artist.
  const input = new Blob([baseline.subarray(0, 2), exif, baseline.subarray(2)]);
  const output = await stripJpegMetadata(input);
  assert.deepEqual(Buffer.from(await output.arrayBuffer()), baseline);
});

test('no new File/Blob when metadata is absent or already reduced to display information', async () => {
  for (const bytes of [baseline, progressive, expected(baseline)]) {
    const file = new File([bytes], 'original.jpg', { type: 'image/jpeg', lastModified: 99 });
    assert.equal(await stripJpegMetadata(file), file);
  }
});

test('non-JPEG types and unreadable data pass through unchanged even if labeled JPEG', async () => {
  for (const bytes of [Buffer.from('text'), Buffer.from('89504e470d0a1a0a', 'hex'),
    Buffer.from('GIF89a'), Buffer.from('00000018667479706865696300000000', 'hex')]) {
    const file = new File([bytes], 'original.jpg', { type: 'image/jpeg' });
    assert.equal(await stripJpegMetadata(file), file);
  }
  const unreadable = { slice() { throw new Error('read failed'); } };
  assert.equal(await stripJpegMetadata(unreadable), unreadable);
});

test('malformed/ambiguous JPEG or EXIF returns the original instead of risking display changes', async () => {
  const invalidOrientation = annotate(baseline, 9);
  const truncatedExif = Buffer.concat([baseline.subarray(0, 2), segment(0xe1, Buffer.from('Exif\0\0II')), baseline.subarray(2)]);
  const duplicateExif = Buffer.concat([baseline.subarray(0, 2), privateExif(6), privateExif(3), baseline.subarray(2)]);
  const lengthOverrun = Buffer.from(annotate(baseline));
  lengthOverrun.writeUInt16BE(65535, 4);
  for (const bytes of [invalidOrientation, truncatedExif, duplicateExif, lengthOverrun,
    annotate(baseline).subarray(0, -1), Buffer.concat([annotate(baseline), Buffer.from('tail')])]) {
    const file = new Blob([bytes]);
    assert.equal(await stripJpegMetadata(file), file);
  }
});

test('signed JUMBF/C2PA and multi-picture JPEG containers remain byte-for-byte unchanged', async () => {
  for (const extra of [segment(0xeb, Buffer.from('JP\0\x01c2pa')), segment(0xe2, Buffer.from('MPF\0offsets'))]) {
    const file = new Blob([baseline.subarray(0, 2), extra, annotate(baseline).subarray(2)]);
    assert.equal(await stripJpegMetadata(file), file);
  }
});

test('Photoshop IPTC removal preserves other image resources and rejects malformed resource bounds', async () => {
  const resolution = photoshopResource(0x03ed, Buffer.alloc(16, 1));
  const app13 = segment(0xed, Buffer.concat([Buffer.from('Photoshop 3.0\0'), resolution, iptcResource]));
  const input = new Blob([baseline.subarray(0, 2), app13, baseline.subarray(2)]);
  const output = await stripJpegMetadata(input);
  assert.deepEqual(Buffer.from(await output.arrayBuffer()), Buffer.concat([
    baseline.subarray(0, 2), segment(0xed, Buffer.concat([Buffer.from('Photoshop 3.0\0'), resolution])), baseline.subarray(2),
  ]));
  const malformed = new Blob([baseline.subarray(0, 2), segment(0xed, Buffer.from('Photoshop 3.0\0broken')), baseline.subarray(2)]);
  assert.equal(await stripJpegMetadata(malformed), malformed);
});

test('invalid frame/scan component counts stay unchanged', async () => {
  for (const [marker, index] of [[0xc0, 9], [0xda, 4]]) {
    const bytes = Buffer.from(annotate(baseline));
    const offset = bytes.indexOf(Buffer.from([0xff, marker]));
    bytes[offset + index] = 0;
    const input = new Blob([bytes]);
    assert.equal(await stripJpegMetadata(input), input);
  }
});

test('ordinary adapter upload cleans a restored JPEG before presigning and uploads matching byte size', async (t) => {
  const root = fileURLToPath(new URL('../..', import.meta.url));
  globalThis.__jpegTestAuth = { auth: { getSession: async () => ({ data: { session: {
    user: { id: 'jpeg-test-user' }, access_token: 'test-token',
  } } }) } };
  const vite = await createServer({ root, configFile: false, logLevel: 'silent',
    optimizeDeps: { noDiscovery: true, include: [] }, resolve: { alias: { '@': `${root}/src` } },
    define: { 'import.meta.env.VITE_API_BASE_URL': JSON.stringify('https://api.test'),
      'import.meta.env.DEV': 'false' }, server: { middlewareMode: true, watch: null },
    plugins: [{ name: 'jpeg-test-auth', enforce: 'pre',
      resolveId(id) { if (id.endsWith('/lib/supabase.js')) return '\0jpeg-test-auth'; },
      load(id) { if (id === '\0jpeg-test-auth') return 'export const supabase = globalThis.__jpegTestAuth;'; },
    }],
  });
  t.after(async () => { await vite.close(); delete globalThis.__jpegTestAuth; });
  const { uploadPhoto } = await vite.ssrLoadModule('/src/lib/api/httpAdapter.js');
  let presign;
  let uploaded;
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    if (url === 'https://api.test/v1/assets/upload-url') {
      presign = JSON.parse(options.body);
      return Response.json({ assetId: 'photo', uploadUrl: 'https://r2.test/put' });
    }
    if (url === 'https://r2.test/put') { uploaded = options.body; return new Response(''); }
    assert.equal(url, 'https://api.test/v1/assets/photo/complete');
    return Response.json({});
  });
  await uploadPhoto('project', { filename: 'original.jpg', mime: 'image/jpeg', blob: new Blob([annotate(baseline)]) });
  assert.deepEqual(Buffer.from(await uploaded.arrayBuffer()), expected(baseline));
  assert.equal(presign.size, expected(baseline).length);
  assert.equal(presign.filename, 'original.jpg');
  assert.equal(presign.mime, 'image/jpeg');
});
