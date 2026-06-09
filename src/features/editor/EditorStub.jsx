/* =============================================================
   editor/EditorStub.jsx — 에디터 자리표시자 (PHASE 2).
   에디터 본체(캔버스·패널·react-moveable·크롭·다운로드)는 2차에서
   구현한다. 지금은 흐름이 끊기지 않도록 전체화면 셸 + 안내만 둔다.
   (사용자 결정 2026-06-09: 에디터 전부 2차)
   ============================================================= */
import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '@/lib/api/index.js';
import { useAppStore } from '@/store/useAppStore.js';
import { Button } from '@/components/Button.jsx';
import { Icon } from '@/components/Icon.jsx';
import styles from './EditorStub.module.css';

const TOOLS = ['AI', '의류', '이미지', '프레임', '텍스트', '오브젝트'];

export function EditorStub() {
  const navigate = useNavigate();
  const product = useAppStore((s) => s.product);
  const resetFlow = useAppStore((s) => s.resetFlow);
  const [blockCount, setBlockCount] = useState(null);

  useEffect(() => { api.getEditorBlocks().then((b) => setBlockCount(b.length)); }, []);

  const title = product?.name?.trim() || '소프트 골지 라운드 니트';
  const startNew = () => { resetFlow(); navigate('/create/input'); }; // 새 생성 진입 시 이전 플로우 상태 초기화

  return (
    <div className={styles.shell}>
      <header className={styles.toolbar}>
        <span className={styles.brand}>wearless</span>
        <nav className={styles.tools}>
          {TOOLS.map((t) => <span key={t} className={styles.tool}>{t}</span>)}
        </nav>
        <span className={styles.docName}>{title}</span>
        <div className={styles.toolbarRight}>
          <span className={styles.tdim}>Undo</span>
          <span className={styles.tdim}>Redo</span>
          <span className={styles.tdim}>미리보기</span>
          <span className={styles.tdim}>저장</span>
          <span className={styles.tdimCta}>다운로드</span>
        </div>
      </header>

      <main className={styles.stage}>
        <div className={styles.notice}>
          <div className={styles.icon}><Icon name="sparkles" size={26} /></div>
          <h1 className={styles.title}>상세페이지가 생성됐어요</h1>
          <p className={styles.desc}>
            {blockCount != null ? `${blockCount}개 블록이 준비됐어요. ` : ''}
            에디터(드래그·리사이즈·패널 편집·다운로드)는 다음 단계(2차)에서 구현됩니다.
          </p>
          <div className={styles.actions}>
            <Button variant="primary" icon="library" onClick={() => navigate('/library')}>보관함으로</Button>
            <Button variant="ghost" icon="plus" onClick={startNew}>새 상세페이지</Button>
          </div>
        </div>
      </main>
    </div>
  );
}

export default EditorStub;
