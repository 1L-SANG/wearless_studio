/* 화면의 모든 시각은 한국 시간으로 읽힌다 — 보는 사람이 어디에 있든.

   되돌아가면: 공개 검증 페이지(/verify)를 해외에서 열었을 때 라이선스 유효기간이 다른
   날짜로 나온다. 국내에서만 테스트하면 절대 안 드러나는 종류의 결함이라, 여기서는
   프로세스 시간대를 실제로 바꿔 가며 확인한다. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = new URL('../../', import.meta.url);
const read = (name) => readFileSync(fileURLToPath(new URL(name, root)), 'utf8');
const mod = await import(pathToFileURL(fileURLToPath(new URL('src/lib/datetime.js', root))).href);

// UTC 2026-09-07 22:00 == KST 2026-09-08 07:00 — 날짜가 갈리는 시각.
const CROSSES_MIDNIGHT = '2026-09-07T22:00:00Z';

test('자정을 넘기는 시각은 한국 날짜로 나온다', () => {
  assert.equal(mod.seoulDateKey(CROSSES_MIDNIGHT), '2026-09-08');
  assert.equal(mod.seoulDate(CROSSES_MIDNIGHT), '2026. 9. 8.');
});

test('보는 사람의 시간대가 결과를 바꾸지 않는다', () => {
  /* 이게 이 파일의 존재 이유다. 같은 프로세스 안에서 process.env.TZ 를 바꿔 봐야
     소용없다 — 모듈 로드 때 만든 Intl 포매터는 timeZone 이 이미 박혀 있어서, 검사가
     통과해도 아무것도 증명하지 못한다(공허한 초록불). TZ 를 **프로세스 시작 전에**
     심어서 자식 프로세스로 확인한다. */
  const modulePath = fileURLToPath(new URL('src/lib/datetime.js', root));
  const script = `import('${pathToFileURL(modulePath).href}')`
    + `.then((m) => process.stdout.write(m.seoulDateKey('${CROSSES_MIDNIGHT}')))`;

  for (const tz of ['UTC', 'America/New_York', 'Asia/Seoul', 'Pacific/Kiritimati']) {
    const out = execFileSync(process.execPath, ['--input-type=module', '-e', script], {
      env: { ...process.env, TZ: tz },
      encoding: 'utf8',
    });
    assert.equal(out, '2026-09-08', `TZ=${tz}`);
  }
});

test('시간대를 고정하지 않으면 실제로 틀린다 — 이 테스트가 무의미하지 않다는 증거', () => {
  // 고정 없이 같은 값을 그리면 뉴욕에서는 하루 전이 나온다. 위 테스트가 우연히 통과하는
  // 게 아니라는 것을 여기서 못 박는다.
  const naive = `process.stdout.write(new Date('${CROSSES_MIDNIGHT}').toLocaleDateString('en-CA'))`;
  const inNewYork = execFileSync(process.execPath, ['-e', naive], {
    env: { ...process.env, TZ: 'America/New_York' }, encoding: 'utf8',
  });
  assert.equal(inNewYork, '2026-09-07');
});

test('서버가 보내는 오프셋 표기가 달라도 같은 순간이면 같은 날짜다', () => {
  // 커넥션 시간대를 KST 로 바꾸면 서버는 +09:00 을 달고 보낸다. 그 전 데이터는 +00:00 이다.
  // 둘은 같은 순간이므로 화면도 같아야 한다 — 아니면 배포 전후로 날짜가 흔들린다.
  assert.equal(mod.seoulDateKey('2026-09-07T22:00:00+00:00'), mod.seoulDateKey('2026-09-08T07:00:00+09:00'));
});

test('읽을 수 없는 값은 화면을 무너뜨리지 않고 대체값으로 떨어진다', () => {
  assert.equal(mod.seoulDateKey(null), '-');
  assert.equal(mod.seoulDateKey(''), '-');
  assert.equal(mod.seoulDate('쓰레기', '쓰레기'), '쓰레기');
  assert.equal(mod.seoulClock(undefined), '--:--');
  // 예전 코드는 try/catch 로 감쌌지만 new Date('쓰레기') 는 던지지 않아서, 화면에
  // "Invalid Date" 가 그대로 나갔다.
  assert.ok(!mod.seoulDate('쓰레기', '쓰레기').includes('Invalid'));
});

test('연월 표기는 한국 기준이다', () => {
  // UTC 2026-12-31 20:00 == KST 2027-01-01 05:00 — 연도까지 갈린다.
  assert.equal(mod.seoulYearMonth('2026-12-31T20:00:00Z'), '2027.01');
});

test('시각 표기도 한국 기준이다', () => {
  assert.equal(mod.seoulClock(CROSSES_MIDNIGHT), '07:00');
});

test('날짜를 그리는 화면은 전부 이 모듈을 쓴다', () => {
  // 한 곳이라도 직접 포맷하면 그 화면만 브라우저 시간대로 돌아간다 — 그리고 국내에서는
  // 안 보인다.
  const screens = [
    'src/features/admin/AdminUsers.jsx',
    'src/features/admin/AdminModels.jsx',
    'src/features/verify/PublicVerify.jsx',
    'src/features/verify/PublicVerifyPublication.jsx',
    'src/features/analysis/AnalysisForm.jsx',
    'src/features/credits/CreditsHistory.jsx',
    'src/features/model/mypage/MyPageConditions.jsx',
    'src/features/model/mypage/MyPageTimeline.jsx',
    'src/features/model/ModelRegister.jsx',
    'src/features/model/ModelLicense.jsx',
    'src/lib/draftSlot.js',
  ];
  for (const name of screens) {
    const source = read(name);
    assert.ok(/from '(@\/lib|\.)\/datetime\.js'/.test(source), `${name}: datetime.js 를 안 쓴다`);
    assert.ok(!source.includes('toLocaleDateString'), `${name}: 직접 날짜 포맷`);
    assert.ok(!source.includes('toLocaleTimeString'), `${name}: 직접 시각 포맷`);
    assert.ok(!/slice\(0, ?10\)/.test(source), `${name}: iso.slice(0,10) = UTC 날짜`);
    assert.ok(!/\.getFullYear\(\)|\.getMonth\(\)|\.getDate\(\)/.test(source),
      `${name}: 브라우저 로컬 getter`);
  }
});
