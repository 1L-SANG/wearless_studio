import React, { useEffect, useRef, useState } from 'react';
import { OUTER_CLOSURE_VALUES, outerClosureCaption, outerClosureDisplayValue } from '../lib/outerClosure.js';

const LABELS = ['전체 닫힘', '가슴까지', '반만 열림', '배까지', '전체 열림'];
const TOP_PADDING = 14;
const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
const round = (value) => Math.round(value * 10) / 10;
const railSpec = (range) => range === 'binary'
  ? { values: ['closed', 'open'], labels: [LABELS[0], LABELS[4]], step: 40, height: 68 }
  : { values: OUTER_CLOSURE_VALUES, labels: LABELS, step: 28, height: 140 };

export function outerClosureNearestIndex(y, range) {
  const spec = railSpec(range);
  return clamp(Math.round((y - TOP_PADDING) / spec.step), 0, spec.values.length - 1);
}

function railPaths(y, spec, range) {
  const cx = 18, top = 3, bottom = spec.height - 3;
  const last = TOP_PADDING + spec.step * (spec.values.length - 1);
  const spread = 7 * clamp((y - TOP_PADDING) / Math.max(spec.step * 2, last - TOP_PADDING), 0, 1)
    * (range === 'binary' ? 2 : 1);
  const fraction = clamp((y - (last - spec.step)) / spec.step, 0, 1);
  const gap = 5 * fraction * fraction * (3 - 2 * fraction);
  const left = (at) => at >= y ? cx - gap
    : cx - gap - (Math.max(spread, gap) - gap) * (y - at) / (y - top || 1);
  const right = (at) => 2 * cx - left(at);
  const teeth = gap < 0.2 && y + 9 < bottom - 3 ? `M${cx} ${round(y + 9)} L${cx} ${bottom - 3}` : '';
  const lower = gap >= 0.2
    ? ` M${round(left(y) + 1.4)} ${round(y + 8)} L${round(left(bottom) + 1.4)} ${bottom - 3} M${round(right(y) - 1.4)} ${round(y + 8)} L${round(right(bottom) - 1.4)} ${bottom - 3}` : '';
  return {
    teeth,
    openTeeth: y > TOP_PADDING + 1
      ? `M${round(left(top) + 1.4)} ${top} L${round(left(y) + 1.4)} ${round(y - 6)} M${round(right(top) - 1.4)} ${top} L${round(right(y) - 1.4)} ${round(y - 6)}${lower}` : lower.trim(),
    tape: `M${round(left(top) - 3.5)} ${top} L${round(left(y) - 3.5)} ${round(y)} L${round(left(bottom) - 3.5)} ${bottom} M${round(right(top) + 3.5)} ${top} L${round(right(y) + 3.5)} ${round(y)} L${round(right(bottom) + 3.5)} ${bottom}`,
    pull: `translate(${round(cx - gap)} ${round(y)})`,
    stopper: `M${cx - 4} ${top} h8 M${cx - 5} ${bottom - 3} h10 v4 h-10 Z`,
  };
}

export default function OuterClosureControl({ value, onChange, range = 'graded', labelledBy }) {
  const activeRange = range === 'binary' ? 'binary' : 'graded';
  const spec = railSpec(activeRange);
  const displayedIndex = spec.values.indexOf(outerClosureDisplayValue(value, activeRange));
  const [index, setIndex] = useState(displayedIndex);
  const [position, setPosition] = useState(TOP_PADDING + spec.step * displayedIndex);
  const [dragging, setDragging] = useState(false);
  const [pointerFocus, setPointerFocus] = useState(false);
  const railRef = useRef(null);
  const svgRef = useRef(null);
  const indexRef = useRef(displayedIndex);
  const positionRef = useRef(position);
  const rangeRef = useRef(activeRange);
  const animationRef = useRef(null);
  const dragRef = useRef(null);

  const stopAnimation = () => {
    if (animationRef.current !== null) cancelAnimationFrame(animationRef.current);
    animationRef.current = null;
  };
  const moveTo = (y) => { positionRef.current = y; setPosition(y); };
  useEffect(() => {
    if (indexRef.current === displayedIndex && rangeRef.current === activeRange) return;
    stopAnimation();
    dragRef.current = null;
    setDragging(false);
    rangeRef.current = activeRange;
    indexRef.current = displayedIndex;
    setIndex(displayedIndex);
    moveTo(TOP_PADDING + spec.step * displayedIndex);
  }, [value, activeRange]);
  useEffect(() => () => stopAnimation(), []);

  const animateTo = (target) => {
    stopAnimation();
    if (target === positionRef.current || (typeof matchMedia === 'function'
      && matchMedia('(prefers-reduced-motion: reduce)').matches)) {
      moveTo(target);
      return;
    }
    const from = positionRef.current, start = performance.now();
    const frame = (now) => {
      const progress = Math.min(1, (now - start) / 180);
      moveTo(from + (target - from) * (1 - Math.pow(1 - progress, 3)));
      animationRef.current = progress < 1 ? requestAnimationFrame(frame) : null;
    };
    animationRef.current = requestAnimationFrame(frame);
  };
  const select = (nextIndex) => {
    const next = clamp(nextIndex, 0, spec.values.length - 1);
    const changed = next !== indexRef.current;
    indexRef.current = next;
    setIndex(next);
    animateTo(TOP_PADDING + spec.step * next);
    if (changed) onChange?.(spec.values[next]);
  };
  const pointerY = (clientY) => {
    const rect = svgRef.current.getBoundingClientRect();
    return clamp((clientY - rect.top) / rect.height * spec.height,
      TOP_PADDING, TOP_PADDING + spec.step * (spec.values.length - 1));
  };
  const pointerDown = (event) => {
    if (dragRef.current || (event.pointerType === 'mouse' && event.button !== 0)) return;
    event.preventDefault();
    stopAnimation();
    const y = pointerY(event.clientY);
    const grab = Math.abs(y - positionRef.current) < 12 ? positionRef.current - y : 0;
    dragRef.current = { pointerId: event.pointerId, grab };
    event.currentTarget.setPointerCapture(event.pointerId);
    setDragging(true);
    setPointerFocus(true);
    moveTo(clamp(y + grab, TOP_PADDING, TOP_PADDING + spec.step * (spec.values.length - 1)));
    railRef.current.focus({ preventScroll: true });
  };
  const pointerMove = (event) => {
    if (dragRef.current?.pointerId !== event.pointerId) return;
    moveTo(clamp(pointerY(event.clientY) + dragRef.current.grab,
      TOP_PADDING, TOP_PADDING + spec.step * (spec.values.length - 1)));
  };
  const pointerEnd = (event) => {
    if (dragRef.current?.pointerId !== event.pointerId) return;
    dragRef.current = null;
    setDragging(false);
    select(outerClosureNearestIndex(positionRef.current, activeRange));
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  };
  const keyDown = (event) => {
    setPointerFocus(false);
    let next;
    if (event.key === 'ArrowDown' || event.key === 'ArrowRight') next = indexRef.current + 1;
    else if (event.key === 'ArrowUp' || event.key === 'ArrowLeft') next = indexRef.current - 1;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = spec.values.length - 1;
    else return;
    event.preventDefault();
    select(next);
  };
  const paths = railPaths(position, spec, activeRange);
  const near = dragging ? outerClosureNearestIndex(position, activeRange) : null;

  return (
    <div className={`outer-closure-control${dragging ? ' dragging' : ''}`} data-range={activeRange}>
      <div className="outer-closure-rail" ref={railRef} tabIndex={0} role="slider"
        aria-orientation="vertical" aria-labelledby={labelledBy}
        aria-label={labelledBy ? undefined : '아우터 열림 정도'}
        aria-valuemin={0} aria-valuemax={spec.values.length - 1} aria-valuenow={index}
        aria-valuetext={outerClosureCaption(spec.values[index], activeRange)}
        data-pointer={pointerFocus ? '1' : undefined}
        onPointerDown={pointerDown} onPointerMove={pointerMove}
        onPointerUp={pointerEnd} onPointerCancel={pointerEnd} onLostPointerCapture={pointerEnd}
        onBlur={() => setPointerFocus(false)} onKeyDown={keyDown}>
        <svg ref={svgRef} viewBox={`0 0 36 ${spec.height}`} aria-hidden="true" focusable="false">
          <path className="outer-closure-tape" d={paths.tape} />
          <path className="outer-closure-teethbase" d={paths.teeth} />
          <path className="outer-closure-teeth" d={paths.teeth} />
          <path className="outer-closure-teeth open" d={paths.openTeeth} />
          <path className="outer-closure-stopper" d={paths.stopper} />
          <g transform={paths.pull}>
            <rect className="outer-closure-pull-body" x="-6" y="-7" width="12" height="16" rx="3.5" />
            <path className="outer-closure-pull-slot" d="M0 -2.5 v6" />
            <rect className="outer-closure-hit" x="-18" y="-14" width="36" height="28" />
          </g>
        </svg>
      </div>
      <div className="outer-closure-stops">
        {spec.labels.map((label, i) => (
          <button key={spec.values[i]} type="button" tabIndex={-1} data-index={i}
            className={`outer-closure-stop${i === index ? ' on' : ''}${i === near && i !== index ? ' near' : ''}`}
            onClick={() => { setPointerFocus(true); select(i); railRef.current.focus({ preventScroll: true }); }}>
            <span>{label}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
