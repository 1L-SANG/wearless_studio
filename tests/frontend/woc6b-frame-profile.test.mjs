import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

let vite, api, DB, build;
before(async () => {
  process.env.MOCK_API_LATENCY_SCALE = '0';
  vite = await createServer({ configFile: false, logLevel: 'silent', optimizeDeps: { noDiscovery: true, include: [] },
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, hmr: false, watch: null }, appType: 'custom' });
  ({ api } = await vite.ssrLoadModule('/src/mock/api.js'));
  ({ DB, buildEditorBlocksFromStoryboard: build } = await vite.ssrLoadModule('/src/mock/db.js'));
});
after(async () => { await vite?.close(); });

const board = () => [{ id: 'photo', taxonomyVersion: 4, source: 'mine', ownImages: ['/seller-photo.jpg'],
  sectionId: 'wear', pageSectionKey: 'emo.wear_1', pageSectionKind: 'wear', pageSectionUsage: 'styling',
  pageDesignId: 'WEAR-STYLING-FRAME-COLUMN-01', slotKey: null }];
const footer = blocks => blocks.flatMap(block => block.elements).filter(el => el.copyRole === 'who').map(el => el.text);
function seed(connections = {}) {
  DB.project.id = `frame-shop-${Math.random()}`;
  DB.project.status = 'draft'; DB.project.copywriting = false;
  DB.project.latestJobs = { mannequin: null, detailPage: null };
  DB.account.name = '절대 공개하면 안 되는 회원 실명';
  DB.mallConnections = connections;
  DB.product = { name: '코튼 셔츠', clothingType: 'top', colors: [] };
  DB.analysis = {}; DB.storyboard = board();
}
const mall = (provider, shopName, status = 'connected') => ({ mall: provider, shopName, status });

test('mock builder accepts only an explicit store name and ignores legacy personal profile objects', () => {
  const product = { name: '코튼 셔츠', colors: [] };
  assert.deepEqual(footer(build(board(), product, false, {}, '  모먼트 상점  ')), ['모먼트 상점']);
  assert.deepEqual(footer(build(board(), product, false, {})), []);
  for (const value of ['  ', null, 123, { display_name: '회원 실명' }, { name: '회원 실명' }]) {
    assert.deepEqual(footer(build(board(), product, false, {}, value)), []);
  }
});

for (const method of ['generateDetailPage', 'startDetailPage']) {
  test(`mock ${method} chooses a connected storefront instead of the account's real name`, async () => {
    seed({ zigzag: mall('zigzag', '마켓몰'), cafe24: mall('cafe24', '  자사몰  ') });
    const result = await api[method](DB.project.id);
    let blocks = result.data;
    if (result.jobId) {
      let job;
      for (let attempts = 0; attempts < 100; attempts += 1) {
        job = await api.getJob(result.jobId);
        if (job.status === 'done') break;
        await new Promise(resolve => setTimeout(resolve, 1));
      }
      assert.equal(job.status, 'done'); blocks = job.result.data;
    }
    assert.deepEqual(footer(blocks), ['자사몰']);
    assert.ok(!JSON.stringify(blocks).includes(DB.account.name));
  });
}

test('mock generation omits an unconnected or unnamed shop even when a real account name exists', async () => {
  for (const connections of [{}, { cafe24: mall('cafe24', '  ') }, { cafe24: mall('cafe24', '옛 상점', 'revoked') }]) {
    seed(connections);
    const { data } = await api.generateDetailPage(DB.project.id);
    assert.deepEqual(footer(data), []); assert.ok(!JSON.stringify(data).includes(DB.account.name));
  }
});

test('mock generation skips an empty/reconnect-needed priority shop and uses connected zigzag', async () => {
  for (const preferred of [mall('cafe24', '  '), mall('cafe24', '만료된 몰', 'reconnect_required')]) {
    seed({ cafe24: preferred, zigzag: mall('zigzag', '마켓몰') });
    const { data } = await api.generateDetailPage(DB.project.id);
    assert.deepEqual(footer(data), ['마켓몰']);
  }
});
