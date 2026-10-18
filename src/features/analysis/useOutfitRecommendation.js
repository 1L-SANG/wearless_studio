import { useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api/index.js';
import { applyOutfitSet, outfitRecommendationPatch } from '@/lib/outfitSelections.js';
import { initialRecommendationState, advanceRecommendationState, acceptLateRecommendation } from '@/lib/outfitPresentation.js';

// 이미 보인 폴백은 유지하고, 늦은 AI 후보와 세트는 명시 버튼을 누를 때 적용한다.
export function useOutfitRecommendation({ analysis, projectId, onRecommendation, onChange }) {
  const [state, setState] = useState(() => initialRecommendationState(analysis.outfitRecommendation, Date.now()));
  const latest = useRef({ analysis, onRecommendation, onChange, state });
  latest.current = { analysis, onRecommendation, onChange, state };
  const delivery = useRef(null);
  const selectionKey = JSON.stringify([analysis.outfitSelections,
    (analysis.matchClothing || []).filter(item => item.selected).map(item => item.id)]);
  const interaction = useRef({ contextKey: analysis.outfitRecommendation?.contextKey, selectionKey, edited: false });
  if (interaction.current.contextKey !== analysis.outfitRecommendation?.contextKey) {
    interaction.current = { contextKey: analysis.outfitRecommendation?.contextKey, selectionKey, edited: false };
  } else if (interaction.current.selectionKey !== selectionKey) {
    // Uploads can change a selection outside this picker's markEdited handlers.
    interaction.current.edited = true;
  }
  const receiveRecommendation = (fields, publish = false) => {
    const current = latest.current;
    if (fields.outfitRecommendation?.contextKey !== current.analysis.outfitRecommendation?.contextKey) return;
    const now = Date.now();
    const prior = interaction.current.edited && current.state.skeleton
      ? { ...current.state, skeleton: false, recommendation: { ...current.state.recommendation, status: 'fallback' } }
      : current.state;
    const next = advanceRecommendationState(prior, fields.outfitRecommendation, now);
    const automaticReady = prior.skeleton && !interaction.current.edited
      && now - prior.enteredAt < 1500 && fields.outfitRecommendation?.status === 'ready'
      && fields.outfitRecommendation.contextKey === prior.recommendation?.contextKey;
    latest.current.state = next;
    setState(next);
    if (fields.outfitRecommendation?.status === 'pending' || next.lateRecommendation) return;
    let patch;
    if (automaticReady) {
      patch = applyOutfitSet({ ...current.analysis, ...fields }, 0, { preserveUser: true });
      // Persist exactly what is displayed. A server set command would reapply
      // the set and replace user picks, or use a different pending/ready set.
      if (patch) delete patch.outfitSetIndex;
    }
    if (!publish && !patch) return;
    if (current.onRecommendation) {
      current.onRecommendation(value => value.outfitRecommendation?.contextKey === fields.outfitRecommendation?.contextKey
        ? { ...value, ...fields } : value);
      if (patch) current.onChange(patch);
    } else current.onChange({ ...fields, ...patch });
  };
  useEffect(() => {
    if (delivery.current?.outfitRecommendation?.contextKey !== analysis.outfitRecommendation?.contextKey) delivery.current = null;
    receiveRecommendation(outfitRecommendationPatch(analysis));
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
        const fields = outfitRecommendationPatch(await api.getOutfitRecommendation(projectId, { handoff: latest.current.analysis.outfitRecommendationHandoff, signal: controller.signal }));
        if (canceled || fields?.outfitRecommendation?.contextKey !== contextKey) return;
        delivery.current = fields;
        receiveRecommendation(fields, true);
        if (fields.outfitRecommendation?.status !== 'pending') {
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
    const merged = { ...latest.current.analysis, ...fields };
    const patch = applyOutfitSet(merged, 0);
    setState(accepted);
    latest.current.onRecommendation?.((current) => ({ ...current, ...fields }));
    latest.current.onChange(latest.current.onRecommendation ? patch : { ...fields, ...patch });
  };
  const markEdited = () => {
    interaction.current.edited = true;
    const prior = latest.current.state;
    const next = prior.recommendation?.status === 'pending'
      ? { ...prior, skeleton: false, recommendation: { ...prior.recommendation, status: 'fallback' } } : prior;
    latest.current.state = next;
    setState(next);
  };
  return { ...state, acceptLate, markEdited };
}
