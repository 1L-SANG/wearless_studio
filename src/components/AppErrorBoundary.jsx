import { Component } from 'react';
import { Button } from './ui.jsx';
import { HangTag } from '../features/shell/HangTag.jsx';
import { reportUserVisibleError } from '../lib/clientEvents.js';
import s from './NotFound.module.css';

export class AppErrorBoundary extends Component {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error) {
    reportUserVisibleError({ kind: 'error_boundary', action: 'load', messageCode: 'screen_render_failed', blocking: true, error });
  }
  render() {
    if (!this.state.failed) return this.props.children;
    return <section className={s.page} role="alert" aria-labelledby="app-error-title">
      <HangTag label="다시 열 수 있는 옷 태그">
        <text className={`${s.mono} ${s.accentFill}`} x="140" y="146" textAnchor="middle" fontSize="18" letterSpacing="1.6">RETRY</text>
      </HangTag>
      <h1 id="app-error-title" className={s.title}>화면을 불러오지 못했어요</h1>
      <div className={s.actions}>
        <Button variant="primary" onClick={() => window.location.reload()}>새로고침</Button>
      </div>
    </section>;
  }
}
