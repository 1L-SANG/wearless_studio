/* =============================================================
   app.jsx — root state machine
   Routes: create (input → analysis → mannequin → storyboard →
   generating → editor) | library. Editor is a full-screen surface.
   Layout 시안은 확정본으로 고정(아래 renderStep). 배경 오브 글로우는
   승인된 75%로 고정. #hash 딥링크(#editor 등)는 리뷰/데모용으로 유지.
   ============================================================= */
const { useState, useEffect } = React;

const FLOW = ['input', 'analysis', 'mannequin', 'storyboard', 'generating', 'editor'];

function App() {
  const [route, setRoute] = useState('create');
  const [step, setStep] = useState('input');
  const [account, setAccount] = useState({ name: '…', avatar: '', credits: 24, plan: 'Pro' });

  useEffect(() => { api.getAccount().then(setAccount); }, []);
  // deep-link by hash (#editor, #storyboard, #library …) for review/demo
  useEffect(() => {
    const apply = () => {
      const h = (location.hash || '').replace('#', '');
      if (h === 'library') { setRoute('library'); }
      else if (FLOW.includes(h)) { setRoute('create'); setStep(h); }
    };
    apply(); window.addEventListener('hashchange', apply);
    return () => window.removeEventListener('hashchange', apply);
  }, []);

  const go = (s) => { setStep(s); document.querySelector('.app-main')?.scrollTo({ top: 0 }); window.scrollTo({ top: 0 }); };
  const nav = (r) => { setRoute(r); if (r === 'create') go('input'); };

  const inEditor = route === 'create' && step === 'editor';
  const showStepper = route === 'create' && ['input', 'analysis', 'mannequin', 'storyboard'].includes(step);

  // background orb glow — fixed at the approved strength (75%)
  const glowVar = { '--glow-a': 0.75 };
  const bg = <div className="app-bg"><div className="edge" /><div className="orb-bg"><div className="l1" /><div className="l2" /><div className="l3" /><div className="hi" /></div></div>;

  const renderStep = () => {
    switch (step) {
      case 'input': return <ProductInput onPhase={() => {}} onNext={() => go('mannequin')} />;
      case 'analysis': return <Analysis onNext={() => go('mannequin')} onBackToInput={() => go('input')} />;
      case 'mannequin': return <Mannequin onNext={() => go('storyboard')} />;
      case 'storyboard': return <Storyboard onNext={() => go('generating')} />;
      case 'generating': return <Generating onDone={() => go('editor')} />;
      default: return null;
    }
  };

  return (
    <div className="app-shell" style={glowVar}>
      {bg}

      {!inEditor && <TopNav account={account} route={route} onNav={nav} />}

      <div className="app-main">
        {route === 'library' && (
          <Library onNew={() => nav('create')}
            onOpen={() => { setRoute('create'); go('editor'); }} />
        )}
        {route === 'create' && !inEditor && (
          <>{showStepper && <Stepper current={step} />}{renderStep()}</>
        )}
      </div>

      {inEditor && <Editor onExit={() => nav('library')} />}
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <ToastProvider><App /></ToastProvider>
);
