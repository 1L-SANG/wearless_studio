// pnpm dev:mock 전용 자료. fmMock=new, review, approved, rejected로 시작 상태를 고른다.
export const MOCK_APPLICANT = Object.freeze({
  applicantName: '김미나', contactEmail: 'mina.kim@example.com', gender: 'female',
  birthdate: '1999-04-12', phone: '010-1234-5678', heightCm: 170, weightKg: 55,
  agencyContracted: false, experienceLevel: 'none', portfolioUrl: null, snsUrl: null,
  attestations: { adultAndTruthful: true, photosAreMine: true, noAgencyContract: true, reviewOnlyUse: true, privacyPolicy: true },
  privacyConsent: { accepted: true, documentVersion: '2026-09-v1' },
});
const SESSION = { user: { id: 'facemarket-mock-model', email: MOCK_APPLICANT.contactEmail } };
const STORAGE_KEY = 'fm_mock_application_v3';
const fail = (status, code) => Object.assign(new Error(code), { status, code });

export function createFacemarketMock({ scenario = 'new', storage = null } = {}) {
  let application = null;
  let staged = false;
  if (scenario === 'saved') {
    try { application = JSON.parse(storage?.getItem(STORAGE_KEY) || 'null'); } catch { /* 빈 시작 */ }
  } else if (['review', 'approved', 'rejected'].includes(scenario)) {
    application = {
      ...MOCK_APPLICANT, id: 'mock-application',
      status: scenario === 'review' ? 'under_review' : scenario,
      createdAt: '2026-09-09T05:12:00Z', submittedAt: '2026-09-09T05:12:00Z',
      reviewedAt: scenario === 'review' ? null : '2026-09-09T05:58:00Z',
      rejectReason: scenario === 'rejected' ? '얼굴이 잘 보이는 정면 사진으로 다시 지원해주세요.' : null,
      hasProfileImage: true, photoKinds: ['profile'], piiPurgedAt: null,
    };
  }
  const persist = () => {
    try { storage?.setItem(STORAGE_KEY, JSON.stringify(application)); } catch { /* 현재 탭에서는 계속 진행 */ }
  };
  if (scenario !== 'saved') persist();
  return {
    getCurrentApplication: async () => application ? structuredClone(application) : null,
    stageApplicationPhoto: async ({ kind }) => { staged = kind === 'profile'; return { staged, kind }; },
    submitApplication: async (body) => {
      if (application && ['under_review', 'approved'].includes(application.status)) throw fail(409, 'application_exists');
      if (!staged) throw fail(400, 'profile_photo_required');
      const now = new Date().toISOString();
      application = { ...structuredClone(body), id: 'mock-application', status: 'under_review', createdAt: now, submittedAt: now, reviewedAt: null, rejectReason: null, hasProfileImage: true, photoKinds: ['profile'], piiPurgedAt: null };
      staged = false;
      persist();
      return structuredClone(application);
    },
    cancelApplication: async (id) => {
      if (!application || application.id !== id) throw fail(404, 'application_not_found');
      application = { ...application, status: 'cancelled' };
      persist();
      return structuredClone(application);
    },
  };
}

let current;
export function getFacemarketMock() {
  if (!current) {
    const params = new URLSearchParams(globalThis.window?.location?.search || '');
    let storage;
    try { storage = globalThis.sessionStorage; } catch { /* 메모리만 사용 */ }
    current = createFacemarketMock({ scenario: params.get('fmMock') || 'saved', storage });
  }
  return current;
}
export function getMockSession() {
  const params = new URLSearchParams(globalThis.window?.location?.search || '');
  return params.get('mockAuth') === 'anonymous' ? null : SESSION;
}
export function signInMock() { return SESSION; }
