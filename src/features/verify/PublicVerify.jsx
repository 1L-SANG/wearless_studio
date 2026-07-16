/* =============================================================
   features/verify — 얼굴 라이선스 공개 검증 (/verify/:licenseId) · step02
   VC 카드의 QR({origin}/verify/{licenseId})을 찍으면 도착하는 페이지.

   **무인증** — 심사위원·구매자가 자기 폰으로 즉석에서 스캔한다. 로그인 게이트를
   두면 QR 이 무의미해지므로 App.jsx 에서 RequireAuth **밖**에, 그리고 앱 크롬 밖에
   등록한다(스캔으로 들어온 사람에게 앱 내비게이션은 잡음이다).

   🔴 얼굴을 렌더하지 않는다. 이 페이지는 무인증이라 여기 그린 건 전부 공개된다 —
   생체정보는 한 픽셀도 실을 수 없다. 서버(GET /v1/facemarket/verify/{id})도 얼굴·
   digest·CI·생년월일·user_id·model_id 를 애초에 응답에 싣지 않는다(화이트리스트).
   여기서 하는 건 그 화이트리스트 응답을 그대로 보여주는 것뿐이다.
   ============================================================= */
import { useCallback, useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { Icon } from '@/components/ui.jsx';
import { verifyLicensePublic } from '@/lib/api/facemarket.js';
import s from './PublicVerify.module.css';

const won = (n) => `₩${Number(n || 0).toLocaleString('ko-KR')}`;
const fmtDate = (iso) => { try { return new Date(iso).toLocaleDateString('ko-KR'); } catch { return iso; } };

const STATUS_COPY = {
  active: { title: '유효한 라이선스예요', desc: '이 얼굴은 아래 조건으로 사용할 수 있어요.' },
  revoked: { title: '해지된 라이선스예요', desc: '모델이 사용을 철회했어요. 이 얼굴은 사용할 수 없어요.' },
  expired: { title: '만료된 라이선스예요', desc: '유효기간이 지났어요. 이 얼굴은 사용할 수 없어요.' },
};

export function PublicVerify() {
  const { licenseId } = useParams();
  const [phase, setPhase] = useState('loading');  // loading | ok | notfound | error
  const [data, setData] = useState(null);
  const [message, setMessage] = useState(null);

  const load = useCallback(async () => {
    setPhase('loading');
    try {
      setData(await verifyLicensePublic(licenseId));
      setPhase('ok');
    } catch (e) {
      setMessage(e.message);
      setPhase(e.status === 404 ? 'notfound' : 'error');
    }
  }, [licenseId]);

  useEffect(() => { load(); }, [load]);

  if (phase === 'loading') {
    return <div className={s.page}><div className={s.shell}><p className={s.plain}>확인하는 중이에요…</p></div></div>;
  }

  if (phase === 'notfound' || phase === 'error') {
    return (
      <div className={s.page}>
        <div className={s.shell}>
          <div className={`${s.hero} ${s.heroUnknown}`}>
            <span className={s.heroIcon}><Icon name="alertTri" size={30} /></span>
            <h1>{phase === 'notfound' ? '찾을 수 없는 라이선스예요' : '확인하지 못했어요'}</h1>
            <p>{phase === 'notfound'
              ? '주소가 잘못됐거나 삭제된 라이선스일 수 있어요.'
              : (message || '잠시 후 다시 시도해 주세요.')}</p>
          </div>
          {phase === 'error' && (
            <button type="button" className={s.retry} onClick={load}>
              <Icon name="refresh" size={14} />다시 시도
            </button>
          )}
          <Footer />
        </div>
      </div>
    );
  }

  const copy = STATUS_COPY[data.status] ?? STATUS_COPY.revoked;
  const ok = data.valid;

  return (
    <div className={s.page}>
      <div className={s.shell}>
        <div className={`${s.hero} ${ok ? s.heroOk : s.heroBad}`}>
          <span className={s.heroIcon}><Icon name={ok ? 'check' : 'ban'} size={30} /></span>
          <h1>{copy.title}</h1>
          <p>{copy.desc}</p>
        </div>

        <section className={s.card}>
          <div className={s.who}>
            {/* 얼굴 없음 — 의도된 것. 무인증 페이지에 생체정보를 싣지 않는다. */}
            <div className={s.whoName}>
              {data.model?.nameMasked ?? '—'}
              {data.model?.age != null && <span className={s.whoAge}> · {data.model.age}세</span>}
            </div>
            <div className={s.whoTag}>등록된 모델</div>
          </div>

          <dl className={s.rows}>
            {data.allowedUse?.length > 0 && (
              <div className={s.row}>
                <dt>허용 용도</dt>
                <dd className={s.tags}>
                  {data.allowedUse.map((u) => <span key={u} className={s.tagAllow}>{u}</span>)}
                </dd>
              </div>
            )}
            {data.forbiddenUse?.length > 0 && (
              <div className={s.row}>
                <dt>금지 용도</dt>
                <dd className={s.tags}>
                  {data.forbiddenUse.map((u) => (
                    <span key={u} className={s.tagDeny}><Icon name="ban" size={10} />{u}</span>
                  ))}
                </dd>
              </div>
            )}
            <div className={s.row}>
              <dt>단가</dt>
              <dd className={s.price}>{won(data.unitPrice)}<em>/건</em></dd>
            </div>
            <div className={s.row}>
              <dt>유효기간</dt>
              <dd>{fmtDate(data.validUntil)}까지</dd>
            </div>
            {data.vcId && (
              <div className={s.row}>
                <dt>VC ID</dt>
                <dd><code className={s.vcid}>{data.vcId}</code></dd>
              </div>
            )}
          </dl>
        </section>

        <Footer />
      </div>
    </div>
  );
}

function Footer() {
  return (
    <p className={s.foot}>
      <Icon name="lock" size={12} />
      얼굴 이미지는 이 페이지에 표시되지 않아요. Wearless 얼굴 라이선스 검증.
    </p>
  );
}

export default PublicVerify;
