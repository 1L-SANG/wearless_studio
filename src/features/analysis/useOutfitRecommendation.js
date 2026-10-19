import { useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api/index.js';
import { applyOutfitSet, outfitRecommendationPatch, toggleOutfitOptional } from '@/lib/outfitSelections.js';
import { initialRecommendationState, advanceRecommendationState, acceptLateRecommendation } from '@/lib/outfitPresentation.js';

// 이미 보인 폴백은 유지하고, 늦은 AI 후보와 세트는 명시 버튼을 누를 때 적용한다.
export function useOutfitRecommendation({ analysis, projectId, product, onRecommendation, onChange }) {
  const [state, setState] = useState(() => initialRecommendationState(analysis.outfitRecommendation, Date.now()));
  const latest = useRef({ analysis, onRecommendation, onChange, state });
  latest.current = { analysis, onRecommendation, onChange, state };
  const delivery = useRef(null);
  const propsDelivery = useRef(null);
  const propsRequests = useRef(new Map());
  useEffect(() => () => { for (const request of propsRequests.current.values()) request.controller.abort(); }, []);
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
    const received = propsDelivery.current?.contextKey === fields.outfitRecommendation?.contextKey
      ? propsDelivery.current : null;
    if (fields.outfitRecommendation?.props || received) {
      const recommendation = { ...fields.outfitRecommendation, props: {} };
      const sources = [current.analysis.outfitRecommendation, received, fields.outfitRecommendation].filter(Boolean);
      for (const key of ['propsContextKeys', 'propsExpiresAt']) {
        if (sources.some(source => source[key])) recommendation[key] = {};
      }
      for (const source of sources) for (const [role, value] of Object.entries(source.props || {})) {
        const prior = recommendation.props[role];
        if (value.status === 'pending' && prior && prior.status !== 'pending') continue;
        recommendation.props[role] = value;
        for (const key of ['propsContextKeys', 'propsExpiresAt']) {
          if (Object.hasOwn(source[key] || {}, role)) recommendation[key][role] = source[key][role];
        }
      }
      // Response callbacks can share one React batch. Keep authoritative props
      // synchronously so a later callback cannot re-read an older parent render.
      propsDelivery.current = recommendation;
      fields = { ...fields, outfitRecommendation: recommendation };
    }
    const now = Date.now();
    const prior = interaction.current.edited && current.state.skeleton
      ? { ...current.state, skeleton: false, recommendation: { ...current.state.recommendation, status: 'fallback' } }
      : current.state;
    const next = { ...advanceRecommendationState(prior, fields.outfitRecommendation, now) };
    if (fields.outfitRecommendation?.props) {
      next.recommendation = { ...next.recommendation,
        props: { ...(prior.recommendation?.contextKey === fields.outfitRecommendation.contextKey ? prior.recommendation?.props : {}), ...fields.outfitRecommendation.props } };
      for (const key of ['propsContextKeys', 'propsExpiresAt']) {
        if (fields.outfitRecommendation[key]) next.recommendation[key] = fields.outfitRecommendation[key];
      }
    }
    const automaticReady = prior.skeleton && !interaction.current.edited
      && now - prior.enteredAt < 1500 && fields.outfitRecommendation?.status === 'ready'
      && fields.outfitRecommendation.contextKey === prior.recommendation?.contextKey;
    latest.current.state = next;
    setState(next);
    const propsChanged = Object.entries(fields.outfitRecommendation?.props || {}).some(([role, value]) =>
      JSON.stringify(current.analysis.outfitRecommendation?.props?.[role]) !== JSON.stringify(value)
      || JSON.stringify(prior.recommendation?.props?.[role]) !== JSON.stringify(value));
    let propsPatch;
    if (propsChanged) {
      let merged = { ...current.analysis, outfitRecommendation: fields.outfitRecommendation };
      for (const role of ['bag', 'accessory']) {
        if (merged.outfitSelections?.optional?.[role] && fields.outfitRecommendation?.props?.[role]
          && merged.outfitSelections?.slots?.[role]?.origin !== 'user') {
          const patch = toggleOutfitOptional(merged, role, true);
          if (patch) { merged = { ...merged, ...patch }; propsPatch = patch; }
        }
      }
    }
    if (fields.outfitRecommendation?.status === 'pending' || next.lateRecommendation) {
      if (propsChanged) {
        const propFields = { ...fields, outfitRecommendation: { ...current.analysis.outfitRecommendation, props: fields.outfitRecommendation.props,
          ...(fields.outfitRecommendation.propsContextKeys ? { propsContextKeys: fields.outfitRecommendation.propsContextKeys } : {}),
          ...(fields.outfitRecommendation.propsExpiresAt ? { propsExpiresAt: fields.outfitRecommendation.propsExpiresAt } : {}) } };
        if (current.onRecommendation) { current.onRecommendation(value => value.outfitRecommendation?.contextKey === propFields.outfitRecommendation.contextKey
          ? { ...value, ...propFields } : value); if (propsPatch) current.onChange(propsPatch); }
        else current.onChange({ ...propFields, ...propsPatch });
      }
      return;
    }
    let patch = propsPatch;
    if (automaticReady) {
      patch = applyOutfitSet({ ...current.analysis, ...fields, ...propsPatch }, 0, { preserveUser: true });
      // Persist exactly what is displayed. A server set command would reapply
      // the set and replace user picks, or use a different pending/ready set.
      if (patch) delete patch.outfitSetIndex;
    }
    if (!publish && !patch && !propsChanged) return;
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
    if (analysis.outfitRecommendation?.status !== 'pending'
      && !Object.values(analysis.outfitRecommendation?.props || {}).some(value => value.status === 'pending')) return undefined;
    let canceled = false; let timer;
    const contextKey = analysis.outfitRecommendation.contextKey;
    const controller = new AbortController();
    const poll = async () => {
      try {
        const fields = outfitRecommendationPatch(await api.getOutfitRecommendation(projectId, { handoff: latest.current.analysis.outfitRecommendationHandoff, signal: controller.signal }));
        if (canceled || fields?.outfitRecommendation?.contextKey !== contextKey) return;
        delivery.current = fields;
        receiveRecommendation(fields, true);
        if (fields.outfitRecommendation?.status !== 'pending'
          && !Object.values(fields.outfitRecommendation?.props || {}).some(value => value.status === 'pending')) {
          return;
        }
      } catch { if (canceled) return; }
      const propsPending = Object.values(latest.current.analysis.outfitRecommendation?.props || {}).some(value => value.status === 'pending');
      timer = setTimeout(poll, propsPending ? 500 : 1000);
    };
    void poll();
    return () => { canceled = true; clearTimeout(timer); controller.abort(); };
  }, [projectId, analysis.outfitRecommendation?.contextKey, analysis.outfitRecommendation?.status, JSON.stringify(analysis.outfitRecommendation?.props)]);
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
  const recommendProps = async role => {
    if (!['bag', 'accessory'].includes(role)) return;
    const current = latest.current;
    const contextKey = current.analysis.outfitRecommendation?.contextKey;
    const requestKey = `${contextKey}:${role}`;
    if (current.analysis.outfitRecommendation?.props?.[role] || propsRequests.current.has(requestKey)) return;
    const controller = new AbortController();
    propsRequests.current.set(requestKey, { controller });
    const pending = { ...current.state, recommendation: { ...current.state.recommendation,
      props: { ...current.state.recommendation?.props, [role]: { status: 'pending', picks: [] } } } };
    latest.current.state = pending; setState(pending);
    try {
      const response = outfitRecommendationPatch(await api.recommendOutfitProps(projectId, role,
        { product, analysis: latest.current.analysis, signal: controller.signal }));
      if (controller.signal.aborted || contextKey !== latest.current.analysis.outfitRecommendation?.contextKey) return;
      receiveRecommendation(response, true);
    } catch {
      if (contextKey !== latest.current.analysis.outfitRecommendation?.contextKey || controller.signal.aborted) return;
      // No error surface for recommendation failures; retain usable deterministic candidates.
      const value = latest.current;
      const candidates = value.analysis.outfitRecommendation?.candidates?.[role] || [];
      const categories = new Set();
      const picks = candidates.filter(item => {
        const category = String(item.category || '').trim().toLowerCase();
        if (role === 'accessory' && categories.has(category)) return false;
        categories.add(category); return item.isCompatible !== false;
      }).slice(0, 3).map(item => item.id);
      const recommendation = { ...value.state.recommendation,
        props: { ...value.state.recommendation?.props, [role]: { status: picks.length ? 'fallback' : 'unavailable', picks } } };
      const next = { ...value.state, recommendation };
      latest.current.state = next; setState(next);
      if (value.analysis.outfitSelections?.optional?.[role]
        && value.analysis.outfitSelections?.slots?.[role]?.origin !== 'user') {
        value.onChange(toggleOutfitOptional({ ...value.analysis, outfitRecommendation: recommendation }, role, true));
      }
    } finally { propsRequests.current.delete(requestKey); }
  };
  return { ...state, acceptLate, markEdited, recommendProps };
}
