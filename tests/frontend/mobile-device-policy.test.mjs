import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { classifyDevice, HANDOFF_POLICY } from '../../src/lib/deviceClass.js';
import { getDraftSlotDeviceLabel } from '../../src/lib/draftSlot.js';

const cases = [
  ['iPhone Safari', 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Mobile/15E148 Safari/604.1', 5, undefined, 'phone'],
  ['iPod', 'Mozilla/5.0 (iPod touch) Mobile Safari/604.1', 5, undefined, 'phone'],
  ['Android Chrome phone', 'Mozilla/5.0 (Linux; Android 15) Chrome/130.0 Mobile Safari/537.36', 5, undefined, 'phone'],
  ['iPad desktop UA', 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) Version/18.0 Safari/605.1', 5, undefined, 'tablet'],
  ['iPad UA', 'Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X) Mobile Safari/604.1', 5, undefined, 'tablet'],
  ['Android tablet', 'Mozilla/5.0 (Linux; Android 15) Chrome/130.0 Safari/537.36', 5, undefined, 'tablet'],
  ['Mac Chrome', 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) Chrome/130.0 Safari/537.36', 0, undefined, 'desktop'],
  ['Windows Chrome', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130.0 Safari/537.36', 0, undefined, 'desktop'],
  ['Windows touch laptop', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130.0 Safari/537.36', 10, undefined, 'desktop'],
  ['Galaxy Tab Samsung Internet desktop mode', 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/26.0 Chrome/122.0.0.0 Safari/537.36', 5, false, 'tablet'],
  ['Linux desktop without touch', 'Mozilla/5.0 (X11; Linux x86_64) Chrome/130.0 Safari/537.36', 0, false, 'desktop'],
  ['Chromebook with touch', 'Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) Chrome/130.0 Safari/537.36', 10, false, 'desktop'],
  ['UA data mobile', 'unknown', 0, true, 'phone'],
  ['UA data non-mobile iPad', 'Macintosh', 5, false, 'tablet'],
];
for (const [name, userAgent, maxTouchPoints, userAgentDataMobile, expected] of cases) {
  test(`device policy: ${name}`, () => {
    const result = classifyDevice({ userAgent, maxTouchPoints, userAgentDataMobile });
    assert.equal(result, expected);
    assert.equal(HANDOFF_POLICY[result], expected === 'desktop' ? 'full' : 'handoff');
  });
}

test('device policy reads navigator only once and survives missing navigator', () => {
  const source = readFileSync(new URL('../../src/lib/deviceClass.js', import.meta.url), 'utf8').replace(/^export /gm, '');
  const load = (context) => new Function('globalThis', `${source}; return isMobileDevice;`)(context);
  const server = load({});
  assert.equal(server(), false);
  let reads = 0;
  const browser = load({ get navigator() {
    reads += 1;
    return { userAgent: reads === 1 ? 'iPhone' : 'Windows', maxTouchPoints: 5 };
  } });
  assert.equal(browser(), true);
  assert.equal(browser(), true);
  assert.equal(reads, 1);
});

test('draft labels identify iPads and Android tablets while preserving browser suffixes', () => {
  assert.equal(getDraftSlotDeviceLabel('Macintosh Version/18.0 Safari/605.1', 5), 'iPad Safari');
  assert.equal(getDraftSlotDeviceLabel('iPad CPU OS 18_0 like Mac OS X Safari/604.1', 5), 'iPad Safari');
  assert.equal(getDraftSlotDeviceLabel('Android Chrome/130.0 Safari/537.36', 5), 'Android 태블릿 Chrome');
  assert.equal(getDraftSlotDeviceLabel('Android Mobile Chrome/130.0', 5), 'Android Chrome');
  assert.equal(getDraftSlotDeviceLabel('Macintosh Chrome/130.0', 0), 'Mac Chrome');
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'navigator');
  try {
    Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { userAgent: 'Macintosh Safari/605.1', maxTouchPoints: 5 } });
    assert.equal(getDraftSlotDeviceLabel(), 'iPad Safari');
    assert.equal(getDraftSlotDeviceLabel('Macintosh Chrome/130.0'), 'iPad Chrome');
  } finally {
    if (descriptor) Object.defineProperty(globalThis, 'navigator', descriptor);
    else delete globalThis.navigator;
  }
});

test('Galaxy Tab desktop-mode label reads as a tablet', () => {
  assert.equal(getDraftSlotDeviceLabel('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/26.0 Chrome/122.0.0.0 Safari/537.36', 5), 'Android 태블릿 Chrome');
  assert.equal(getDraftSlotDeviceLabel('Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) Chrome/130.0 Safari/537.36', 10), '기기 Chrome');
});
