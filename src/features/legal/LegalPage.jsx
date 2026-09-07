import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { marked } from 'marked';
import { ErrorState, Skeleton } from '@/components/ui.jsx';
import { COMPANY_INFO_LINES } from '@/lib/companyInfo.js';
import { IS_FACEMARKET } from '@/lib/host.js';
import './legal.css';

const SELLER_DOCUMENTS = [
  { slug: 'terms-seller', to: '/terms', label: '이용약관' },
  { slug: 'privacy-seller', to: '/privacy', label: '개인정보 처리방침' },
  { slug: 'refund', to: '/refund', label: '환불 정책' },
  { slug: 'seller-license-terms', to: '/model-license-terms', label: '모델 라이선스 이용조건' },
];

const FACEMARKET_DOCUMENTS = [
  { slug: 'terms-model', to: '/terms', label: '모델 이용약관' },
  { slug: 'license-agreement', to: '/license-agreement', label: '초상 라이선스 계약' },
  { slug: 'privacy-model', to: '/privacy', label: '개인정보 처리방침' },
  { slug: 'seller-license-terms', to: '/seller-terms', label: '셀러 라이선스 이용조건' },
  { slug: 'answers', to: '/answers', label: '자주 묻는 법적 질문' },
];

export function LegalPage({ slug }) {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState({ phase: 'loading', document: null, markdown: '' });

  useEffect(() => {
    const controller = new AbortController();
    setState({ phase: 'loading', document: null, markdown: '' });

    Promise.all([
      fetch('/legal/manifest.json', { signal: controller.signal }),
      fetch(`/legal/${slug}.md`, { signal: controller.signal }),
    ]).then(async ([manifestResponse, markdownResponse]) => {
      if (!manifestResponse.ok || !markdownResponse.ok) throw new Error('legal-document-fetch-failed');
      const [manifest, markdown] = await Promise.all([
        manifestResponse.json(),
        markdownResponse.text(),
      ]);
      const document = manifest.find((item) => item.slug === slug);
      if (!document) throw new Error('legal-document-not-in-manifest');
      setState({ phase: 'ready', document, markdown });
    }).catch((error) => {
      if (error.name !== 'AbortError') {
        setState({ phase: 'error', document: null, markdown: '' });
      }
    });

    return () => controller.abort();
  }, [attempt, slug]);

  useEffect(() => {
    if (!state.document?.title) return undefined;
    const previousTitle = document.title;
    document.title = state.document.title;
    return () => { document.title = previousTitle; };
  }, [state.document?.title]);

  const html = useMemo(() => {
    if (!state.markdown) return '';
    const parsed = marked.parse(state.markdown, { gfm: true });
    return parsed
      .replace(/^<h1>[\s\S]*?<\/h1>\s*/, '')
      .replaceAll('<table>', '<div class="legal-table-wrap"><table>')
      .replaceAll('</table>', '</table></div>');
  }, [state.markdown]);

  if (state.phase === 'loading') {
    return (
      <main className="legal-page" aria-busy="true">
        <div className="legal-state">
          <Skeleton h={48} r={8} />
          <Skeleton h={20} r={6} />
          <Skeleton h={320} r={12} />
        </div>
      </main>
    );
  }

  if (state.phase === 'error') {
    return (
      <main className="legal-page">
        <div className="legal-state">
          <ErrorState
            desc="법적 고지 문서를 불러오지 못했어요. 잠시 후 다시 시도해 주세요."
            onRetry={() => setAttempt((value) => value + 1)}
          />
        </div>
      </main>
    );
  }

  const relatedDocuments = (IS_FACEMARKET ? FACEMARKET_DOCUMENTS : SELLER_DOCUMENTS)
    .filter((item) => item.slug !== slug);
  const [year, month, day] = state.document.effectiveDate.split('-').map(Number);

  return (
    <main className="legal-page">
      <article className="legal-article">
        <header className="legal-header">
          <p className="legal-eyebrow">법적 고지</p>
          <h1>{state.document.title}</h1>
          <p className="legal-meta">시행일 {year}년 {month}월 {day}일 · {state.document.version}</p>
          <nav className="legal-related" aria-label="다른 법적 고지">
            {relatedDocuments.map((item, index) => (
              <span key={item.slug}>
                {index > 0 && <span aria-hidden="true"> · </span>}
                <Link to={item.to}>{item.label}</Link>
              </span>
            ))}
          </nav>
        </header>

        <div className="legal-prose" dangerouslySetInnerHTML={{ __html: html }} />

        <section className="legal-business" aria-labelledby="legal-business-title">
          <h2 id="legal-business-title">사업자 정보</h2>
          {COMPANY_INFO_LINES.map((line) => <p key={line}>{line}</p>)}
        </section>
      </article>
    </main>
  );
}

export default LegalPage;
