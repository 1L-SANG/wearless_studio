/* =============================================================
   features/model — 모델 온보딩 (/model/register)  [FM-10]
   CX 표준인증창(ENT_MID) 위젯을 임베드해 모바일 신분증 본인확인을 수행한다.
   성공 콜백의 token 만 백엔드(verifyIdentity)로 넘기고, 서버가 CX trans 로
   실 신원을 받아 검증·모델 등록한다. 원문 신원은 브라우저→서버로 보내지 않는다.
   ============================================================= */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Button, Icon } from '@/components/ui.jsx';
import { verifyIdentity } from '@/lib/api/facemarket.js';
import s from './ModelRegister.module.css';

const CX_ORIGIN = 'https://cx.raonsecure.co.kr:17543';
// ENT_MID config (FM-03 채택). 팀전용 config 수령 시 VITE_CX_CONFIG_URL 로 교체.
const CX_CONFIG_URL =
  import.meta.env.VITE_CX_CONFIG_URL || `${CX_ORIGIN}/ent/esign/config/config.mid.json`;

const STEPS = [
  { icon: 'person', label: '신분증 인증', desc: '모바일 신분증으로 실명 확인' },
  { icon: 'lock', label: '안전한 처리', desc: '원본은 저장 없이 암호화 지문만' },
  { icon: 'sparkles', label: '검증 배지', desc: '검증 모델로 등록 완료' },
];

// 위젯 리소스(vendor→ux js + css)를 1회 주입하고 window.OACX 준비를 기다린다.
let _cxLoader;
function loadCxWidget() {
  if (window.OACX) return Promise.resolve();
  if (_cxLoader) return _cxLoader;
  const addScript = (id, src) =>
    new Promise((res, rej) => {
      if (document.getElementById(id)) return res();
      const el = document.createElement('script');
      el.id = id;
      el.src = src;
      el.onload = () => res();
      el.onerror = () => rej(new Error('인증 모듈을 불러오지 못했어요.'));
      document.head.appendChild(el);
    });
  _cxLoader = new Promise((resolve, reject) => {
    if (!document.getElementById('oacx-ux-css')) {
      const link = document.createElement('link');
      link.id = 'oacx-ux-css';
      link.rel = 'stylesheet';
      link.href = `${CX_ORIGIN}/ent/esign/oacx-ux.css`;
      document.head.appendChild(link);
    }
    addScript('oacx-vendor', `${CX_ORIGIN}/ent/esign/oacx-vendor.js`)
      .then(() => addScript('oacx-ux', `${CX_ORIGIN}/ent/esign/oacx-ux.js`))
      .then(() => {
        let tries = 0;
        const t = setInterval(() => {
          if (window.OACX) {
            clearInterval(t);
            resolve();
          } else if (++tries > 50) {
            clearInterval(t);
            reject(new Error('인증 모듈이 준비되지 않았어요.'));
          }
        }, 100);
      })
      .catch(reject);
  });
  return _cxLoader;
}

export function ModelRegister() {
  const [phase, setPhase] = useState('loading'); // loading|ready|verifying|done|error
  const [error, setError] = useState('');
  const [result, setResult] = useState(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    loadCxWidget()
      .then(() => mounted.current && setPhase('ready'))
      .catch((e) => {
        if (!mounted.current) return;
        setError(e.message);
        setPhase('error');
      });
    return () => {
      mounted.current = false;
    };
  }, []);

  const onAuth = useCallback(() => {
    if (!window.OACX) {
      setError('인증 모듈이 아직 준비되지 않았어요.');
      return;
    }
    setError('');
    const json = { contentInfo: { signType: 'ENT_MID' }, compareCI: false, isBirth: true };
    window.OACX.LOAD_MODULE(CX_CONFIG_URL, json, async (res) => {
      try {
        const parsed = typeof res === 'string' ? JSON.parse(res) : res;
        const token = parsed && parsed.token;
        if (!token) {
          setError('인증 토큰을 받지 못했어요. 다시 시도해 주세요.');
          return;
        }
        setPhase('verifying');
        const r = await verifyIdentity(token);
        if (!mounted.current) return;
        setResult(r);
        setPhase('done');
      } catch (e) {
        if (!mounted.current) return;
        setError(e.message || '본인확인에 실패했어요.');
        setPhase('ready');
      }
    });
  }, []);

  const busy = phase === 'loading' || phase === 'verifying';

  if (phase === 'done') {
    return (
      <div className="wizard narrow">
        <div className={s.successWrap}>
          <div className={s.successIcon}>
            <Icon name="check" size={30} stroke={2.4} />
          </div>
          <h1 className={s.successTitle}>검증 완료</h1>
          <p className={s.successLead}>
            <b>{result?.nameMasked}</b> 님, 검증 모델로 등록됐어요.
          </p>
          <div className={s.idPill}>
            <Icon name="sparkles" size={14} />
            <span>모델 ID · {result?.modelId}</span>
          </div>
          <div className={s.nextCard}>
            <div className={s.nextIcon}><Icon name="shirt" size={18} /></div>
            <div>
              <div className={s.nextTitle}>다음 단계 — 얼굴 라이선스 발급</div>
              <div className={s.nextDesc}>얼굴과 사용 조건을 라이선스로 등록하면 셀러가 사용할 수 있어요. (곧 제공)</div>
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="wizard narrow">
      <div className="page-head">
        <h1>모델 본인확인</h1>
        <p>모바일 신분증으로 실명을 확인하면 검증 모델로 등록돼요.</p>
      </div>

      <div className="surface">
        <div className={s.eyebrow}>검증 모델 온보딩</div>

        <div className={s.steps}>
          {STEPS.map((st, i) => (
            <div className={s.step} key={st.label}>
              <div className={s.stepIcon}><Icon name={st.icon} size={20} /></div>
              <div className={s.stepText}>
                <div className={s.stepLabel}>{st.label}</div>
                <div className={s.stepDesc}>{st.desc}</div>
              </div>
              {i < STEPS.length - 1 && <div className={s.stepArrow}><Icon name="chevRight" size={16} /></div>}
            </div>
          ))}
        </div>

        {/* 표준인증창이 렌더되는 컨테이너 */}
        <div id="oacxDiv" className={s.widget} />

        <Button variant="primary" block onClick={onAuth} disabled={busy} iconRight="arrowRight">
          {phase === 'loading'
            ? '인증 모듈 준비 중…'
            : phase === 'verifying'
              ? '본인확인 중…'
              : '모바일 신분증으로 인증하기'}
        </Button>

        {error && (
          <p className={s.error}>
            <Icon name="alertCircle" size={15} /> {error}
          </p>
        )}

        <div className={s.privacy}>
          <Icon name="lock" size={15} />
          <span>실명·주민번호는 저장하지 않아요. 암호화된 지문(HMAC)만 보관해 중복만 방지해요.</span>
        </div>
      </div>
    </div>
  );
}
