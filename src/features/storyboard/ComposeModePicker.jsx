/* 사진 양 선택 — 콘티 시드의 컷 수를 정한다. 사용자가 보드를 보면서 고를 수 있게 콘티 상단에 둔다.
   손대지 않은 기본 시드는 모드 변경 시 재시드되고 사용자가 손댄 보드는 유지된다
   (src/lib/api/httpAdapter.js 의 getStoryboard 재시드 규칙). */
import { useAppStore } from '@/store/useAppStore.js';
import './ComposeModePicker.css';

export function ComposeModePicker({ modes, onModeChange, onError }) {
  const composeMode = useAppStore((s) => s.composeMode);
  const setComposeMode = useAppStore((s) => s.setComposeMode);
  if (!modes?.length) return null;

  return (
    <div className="sb-compose">
      <div className="sb-compose-q">사진 양</div>
      <div className="sb-cmp2">
        {modes.map((m) => {
          const on = composeMode === m.value;
          return (
            <button
              type="button"
              key={m.value}
              className={`sb-cmp${on ? ' on' : ''}`}
              aria-pressed={on}
              onClick={() => {
                if (on) return;
                setComposeMode(m.value).then(() => onModeChange(m.value)).catch(() => onError());
              }}
            >
              <b>{m.label}</b>
              <span>{m.desc}</span>
              {m.count && <em>예상 {m.count}컷</em>}
            </button>
          );
        })}
      </div>
    </div>
  );
}

export default ComposeModePicker;
