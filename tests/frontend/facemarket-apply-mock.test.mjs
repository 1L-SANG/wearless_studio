import test from 'node:test';
import assert from 'node:assert/strict';
import { createFacemarketMock, MOCK_APPLICANT } from '../../src/mock/facemarket.js';
import { resolveHubJourney } from '../../src/features/model/modelHubState.js';

test('new mock visitors can upload and submit, then see the same application in status', async () => {
  const api = createFacemarketMock();
  assert.equal(await api.getCurrentApplication(), null);
  const body = { ...MOCK_APPLICANT, weightKg: null, agencyContracted: true };
  await assert.rejects(api.submitApplication(body), { code: 'profile_photo_required' });
  await api.stageApplicationPhoto({ kind: 'profile' });
  const submitted = await api.submitApplication(body);
  assert.equal(submitted.status, 'under_review');
  assert.equal(submitted.weightKg, null);
  assert.equal(submitted.agencyContracted, true);
  assert.deepEqual(await api.getCurrentApplication(), submitted);
  await assert.rejects(api.submitApplication(body), { status: 409 });
  await api.cancelApplication(submitted.id);
  assert.equal((await api.getCurrentApplication()).status, 'cancelled');
});
test('review, approved and rejected fixtures expose the new profile values', async () => {
  for (const scenario of ['review', 'approved', 'rejected']) {
    const app = await createFacemarketMock({ scenario }).getCurrentApplication();
    assert.equal(app.status, scenario === 'review' ? 'under_review' : scenario);
    assert.equal(app.weightKg, 55);
    assert.equal(app.agencyContracted, false);
    assert.equal(typeof app.contactEmail, 'string');
    assert.equal(resolveHubJourney({ application: app }).steps.length, 5);
  }
});

test('the API facade in explicit development mock mode completes the application without HTTP', async () => {
  const { createServer } = await import('vite');
  const server = await createServer({
    configFile: false,
    root: new URL('../..', import.meta.url).pathname,
    server: { middlewareMode: true, hmr: false, ws: false },
    appType: 'custom',
    define: { 'import.meta.env.DEV': 'true', 'import.meta.env.VITE_API_MODE': '"mock"' },
    plugins: [{
      name: 'assert-no-live-application-api',
      enforce: 'pre',
      resolveId(id) {
        if (id === '@/lib/api/httpAdapter.js') return '\0no-live-http';
        if (id === '@/lib/supabase.js') return '\0no-live-auth';
        return null;
      },
      load(id) {
        if (id === '\0no-live-http') return 'export const http = () => { throw new Error("live HTTP in mock mode"); };';
        if (id === '\0no-live-auth') return 'export const supabase = { auth: { getSession() { throw new Error("live auth in mock mode"); } } };';
        return null;
      },
    }],
  });
  try {
    const api = await server.ssrLoadModule('/src/lib/api/facemarket.js');
    assert.deepEqual(await api.getApplicationConfig(), { applicationRequired: true });
    assert.equal(await api.getCurrentApplication(), null);
    assert.deepEqual(await api.listMyModels(), []);
    assert.equal(await api.getCurrentEnrollment(), null);
    assert.deepEqual(await api.listLicenses(), []);
    assert.equal((await api.getSettlementSummary()).monthCount, 0);
    await api.stageApplicationPhoto({ kind: 'profile', fileBlob: new Blob(['photo']), filename: 'profile.jpg' });
    const app = await api.submitApplication(MOCK_APPLICANT);
    assert.equal(app.weightKg, 55);
    assert.equal((await api.getCurrentApplication()).status, 'under_review');
    await api.cancelApplication(app.id);
    assert.equal((await api.getCurrentApplication()).status, 'cancelled');
  } finally { await server.close(); }
});
