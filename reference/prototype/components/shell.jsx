/* =============================================================
   components/shell.jsx — app chrome: TopNav, Stepper, PageHead
   ============================================================= */

const WIZARD_STEPS = [
  { key: 'input', label: '제품 정보·분석' },
  { key: 'mannequin', label: '마네킹컷' },
  { key: 'storyboard', label: '콘티보드' },
  { key: 'editor', label: '에디터' },
];

/* input+analysis collapse into step 0; generating shares the editor step */
const STEP_INDEX = { input: 0, analysis: 0, mannequin: 1, storyboard: 2, generating: 3, editor: 3 };

function TopNav({ account, route, onNav, onOpenLibrary }) {
  return (
    <nav className="topnav">
      <span className="brand">wearless</span>
      <div className="nav-links">
        <button className={`nav-link${route === 'create' ? ' active' : ''}`} onClick={() => onNav('create')}>상세페이지 제작</button>
        <button className={`nav-link${route === 'library' ? ' active' : ''}`} onClick={() => onNav('library')}>보관함</button>
      </div>
      <div className="nav-right">
        <span className="credit-badge"><Icon name="coins" size={15} stroke={1.8} />크레딧 <b>{account.credits}</b></span>
        <span className="plan-badge">{account.plan}</span>
        {account.avatar
          ? <img className="avatar" src={account.avatar} alt={account.name} title={account.name} />
          : <span className="avatar" style={{ display: 'inline-block', background: 'var(--bg-2)' }} />}
      </div>
    </nav>
  );
}

function Stepper({ current }) {
  const idx = STEP_INDEX[current] ?? 0;
  return (
    <div className="stepper dots">
      {WIZARD_STEPS.map((s, i) => (
        <div key={s.key} className={`step${i < idx ? ' done' : ''}${i === idx ? ' active' : ''}`}>
          {i > 0 && <span className="step-line" />}
          <span className="step-dot" title={s.label} />
        </div>
      ))}
    </div>
  );
}

function PageHead({ title, sub }) {
  return (
    <div className="page-head">
      <h1 dangerouslySetInnerHTML={{ __html: title }} />
      {sub && <p>{sub}</p>}
    </div>
  );
}

/* CTA footer for wizard pages */
function WizardCTA({ children }) {
  return <div className="wizard-cta">{children}</div>;
}

/* a media placeholder image that lazy-fills */
function Media({ src, alt, style, className, ratio }) {
  return <img src={src} alt={alt || ''} className={`media ${className || ''}`}
    style={{ aspectRatio: ratio, width: '100%', ...style }} loading="lazy" />;
}

Object.assign(window, { TopNav, Stepper, PageHead, WizardCTA, Media, WIZARD_STEPS });
