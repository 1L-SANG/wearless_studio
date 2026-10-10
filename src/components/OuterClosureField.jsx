import { useId } from 'react';
import OuterClosureControl from './OuterClosureControl.jsx';
import { outerClosureRange } from '../lib/outerClosure.js';

export default function OuterClosureField({ value, onChange, frontClosure, id }) {
  const generatedId = useId();
  const range = outerClosureRange(frontClosure);
  const labelId = `${id || generatedId}-label`;
  if (range === 'hidden') return null;
  return (
    <div className="insp-sec outer-closure-field" id={id}>
      <div className="outer-closure-head"><span className="lbl" id={labelId}>아우터 열림 정도</span></div>
      <OuterClosureControl value={value} onChange={onChange} range={range} labelledBy={labelId} />
    </div>
  );
}
