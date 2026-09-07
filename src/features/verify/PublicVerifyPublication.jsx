/* =============================================================
   features/verify — 배포본 공개 검증 (/verify/p/:publicationId)
   파일 안 C2PA 매니페스트의 verifyUrl 이 여기로 온다.

   PublicVerify.jsx(라이선스 QR 검증)의 형제 페이지 — 같은 셸·같은 상태 카피 톤·같은
   CSS 모듈을 그대로 쓴다(새 시각 언어를 만들지 않는다). App.jsx 에서 RequireAuth **밖**,
   앱 크롬 밖에 등록한다 — 스캔·검색으로 들어온 사람에게는 계정도 맥락도 없다.

   🔴 얼굴을 렌더하지 않는다. 무인증이라 여기 그린 건 전부 공개된다.
   서버(GET /v1/facemarket/publications/verify/{id})가 화이트리스트로만 응답하고,
   여기서 하는 건 그 응답을 그대로 보여주는 것뿐이다.
   ============================================================= */
import { useCallback, useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { Icon } from '@/components/ui.jsx';
import { verifyPublicationPublic } from '@/lib/api/facemarket.js';
import s from './PublicVerify.module.css';

const fmtDate = (iso) => { try { return new Date(iso).toLocaleDateString('ko-KR'); } catch { return iso; } };

const STATUS_COPY = {
  active: { title: '정품 이미지예요', desc: '아래 조건으로 사용이 허가된 이미지예요.' },
  revoked: { title: '철회된 이미지예요', desc: '모델이 사용을 철회했어요. 이 이미지는 더 이상 사용할 수 없어요.' },
  expired: { title: '기간이 지난 이미지예요', desc: '라이선스 유효기간이 지났어요.' },
};

export function PublicVerifyPublication() {
  const { publicationId } = useParams();
  const [phase, setPhase] = useState('loading');  // loading | ok | notfound | error
  const [data, setData] = useState(null);
  const [message, setMessage] = useState(null);

  const load = useCallback(async () => {
    setPhase('loading');
    try {
      setData(await verifyPublicationPublic(publicationId));
      setPhase('ok');
    } catch (e) {
      setMessage(e.message);
      setPhase(e.status === 404 ? 'notfound' : 'error');
    }
  }, [publicationId]);

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
            <h1>{phase === 'notfound' ? '찾을 수 없는 기록이에요' : '확인하지 못했어요'}</h1>
            <p>{phase === 'notfound' ? '주소가 잘못됐을 수 있어요.' : (message || '잠시 후 다시 시도해 주세요.')}</p>
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
          <span className={s.heroIcon}><Icon name={ok ? 'check' : 'alertTri'} size={30} /></span>
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
            <div className={s.row}>
              <dt>발행</dt>
              <dd>{fmtDate(data.publishedAt)}</dd>
            </div>
            {(data.allowedUse?.length ?? 0) > 0 && (
              <div className={s.row}>
                <dt>사용 허용</dt>
                <dd className={s.tags}>
                  {data.allowedUse.map((u) => <span key={u} className={s.tagAllow}>{u}</span>)}
                </dd>
              </div>
            )}
            {(data.forbiddenUse?.length ?? 0) > 0 && (
              <div className={s.row}>
                <dt>사용 금지</dt>
                <dd className={s.tags}>
                  {data.forbiddenUse.map((u) => (
                    <span key={u} className={s.tagDeny}><Icon name="ban" size={10} />{u}</span>
                  ))}
                </dd>
              </div>
            )}
            {data.licenseValidUntil && (
              <div className={s.row}>
                <dt>유효기간</dt>
                <dd>{fmtDate(data.licenseValidUntil)}까지</dd>
              </div>
            )}
            <div className={s.row}>
              <dt>파일 지문</dt>
              <dd><code className={s.vcid}>{data.imageHashPrefix}…</code></dd>
            </div>
            {data.chain && (
              <div className={s.row}>
                <dt>블록체인 기록</dt>
                <dd>
                  {data.chain.status === 'confirmed'
                    ? <code className={s.vcid}>
                        {data.chain.txHash ? `${data.chain.txHash.slice(0, 14)}…` : `block ${data.chain.block}`}
                      </code>
                    : '기록 대기 중'}
                </dd>
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
      얼굴 이미지는 이 페이지에 표시되지 않아요. Wearless 배포본 공개 검증.
    </p>
  );
}

export default PublicVerifyPublication;
