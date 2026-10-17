import { useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api/index.js';
import { applyOutfitSet, mergeOutfitAnalysis } from '@/lib/outfitSelections.js';
import { initialRecommendationState, advanceRecommendationState, acceptLateRecommendation } from '@/lib/outfitPresentation.js';

// 이미 보인 폴백은 유지하고, 늦은 AI 후보와 세트는 명시 버튼을 누를 때 적용한다.
export function useOutfitRecommendation({ analysis, projectId, onRecommendation, onChange }) {
  const [state, setState] = useState(() => initialRecommendationState(analysis.outfitRecommendation, Date.now()));
  const latest = useRef({ analysis, onRecommendation, onChange, state });
  latest.current = { analysis, onRecommendation, onChange, state };
  const delivery = useRef(null);
  useEffect(() => {
    if (delivery.current?.outfitRecommendation?.contextKey !== analysis.outfitRecommendation?.contextKey) delivery.current = null;
    setState((prior) => advanceRecommendationState(prior, analysis.outfitRecommendation, Date.now()));
  }, [analysis.outfitRecommendation]);
  useEffect(() => {
    if (!state.skeleton) return undefined;
    const timer = setTimeout(() => setState((prior) => advanceRecommendationState(prior, latest.current.analysis.outfitRecommendation, Date.now())), Math.max(0, 1500 - (Date.now() - state.enteredAt)));
    return () => clearTimeout(timer);
  }, [state.skeleton, state.enteredAt]);
  useEffect(() => {
    if (analysis.outfitRecommendation?.status !== 'pending') return undefined;
    let canceled = false; let timer;
    const contextKey = analysis.outfitRecommendation.contextKey;
    const controller = new AbortController();
    const poll = async () => {
      try {
        const fields = await api.getOutfitRecommendation(projectId, { handoff: latest.current.analysis.outfitRecommendationHandoff, signal: controller.signal });
        if (canceled || fields?.outfitRecommendation?.contextKey !== contextKey) return;
        const automaticReady = latest.current.state.skeleton && Date.now() - latest.current.state.enteredAt < 1500
          && fields.outfitRecommendation?.status === 'ready';
        const next = advanceRecommendationState(latest.current.state, fields.outfitRecommendation, Date.now());
        delivery.current = fields;
        setState(next);
        if (fields.outfitRecommendation?.status !== 'pending') {
          if (!next.lateRecommendation) {
            const replace = (current) => {
              if (current.outfitRecommendation?.contextKey !== contextKey) return current;
              const merged = mergeOutfitAnalysis(current, fields);
              return automaticReady ? { ...merged, ...applyOutfitSet(merged, 0) } : merged;
            };
            if (latest.current.onRecommendation) latest.current.onRecommendation(replace);
            else latest.current.onChange(replace(latest.current.analysis));
          }
          return;
        }
      } catch { if (canceled) return; }
      timer = setTimeout(poll, 1000);
    };
    void poll();
    return () => { canceled = true; clearTimeout(timer); controller.abort(); };
  }, [projectId, analysis.outfitRecommendation?.contextKey, analysis.outfitRecommendation?.status]);
  const acceptLate = () => {
    if (!state.lateRecommendation) return;
    const accepted = acceptLateRecommendation(state);
    const delivered = delivery.current?.outfitRecommendation?.status === 'ready'
      && delivery.current.outfitRecommendation.contextKey === accepted.recommendation.contextKey ? delivery.current : {};
    const fields = { ...delivered, outfitRecommendation: accepted.recommendation };
    if (fields.outfitRecommendation?.contextKey !== latest.current.analysis.outfitRecommendation?.contextKey) return;
    const merged = mergeOutfitAnalysis(latest.current.analysis, fields);
    const patch = applyOutfitSet(merged, 0);
    setState(accepted);
    latest.current.onRecommendation?.((current) => mergeOutfitAnalysis(current, fields));
    latest.current.onChange(patch);
  };
  const markEdited = () => setState((prior) => prior.recommendation?.status === 'pending'
    ? { ...prior, skeleton: false, recommendation: { ...prior.recommendation, status: 'fallback' } } : prior);
  return { ...state, acceptLate, markEdited };
}
