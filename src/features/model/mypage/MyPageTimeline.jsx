import { seoulDateTime } from '@/lib/datetime.js';
import { Icon } from '@/components/ui.jsx';
import s from './MyPage.module.css';

export function timelineTime(value) {
  return seoulDateTime(value, null);
}

export function MyPageTimeline({ journey, description, onAction }) {
  return <ol className={s.timeline} aria-label="등록 진행 상태">
    {journey.steps.map((step, index) => {
      const current = ['progress', 'todo'].includes(step.state);
      const time = timelineTime(step.timestamp);
      const body = <div>
        <div className={s.timelineHeading}><strong>{step.label}</strong>
          {step.tag && <span className={`${s.timelineTag} ${step.state === 'done' ? s.tagDone : ''}`}>{step.tag}</span>}
        </div>
        {time && <p className={s.timelineMeta}><time dateTime={step.timestamp}>{time}</time></p>}
        {current && description && <p>{description}</p>}
      </div>;
      return <li key={step.key} className={s[step.state]} aria-current={current ? 'step' : undefined}>
        <span className={`${s.timelineMarker} ${s[`marker_${step.state}`]}`} aria-hidden="true">
          {step.state === 'done' ? <Icon name="check" size={14} /> : index + 1}
        </span>
        {step.state === 'todo' ? <div className={s.timelineCard}>{body}
          {journey.action && <button className={s.primary} type="button" onClick={onAction}>{journey.action.label}</button>}
        </div> : body}
      </li>;
    })}
  </ol>;
}
