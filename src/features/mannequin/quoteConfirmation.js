export const expectedMannequinQuote = (credits) => ({ free: credits === 0, credits });
export const mannequinQuoteLabel = (quote) => quote?.free ? '무료' : `${quote?.credits} 크레딧`;

export function createMannequinQuoteConfirmation(storage) {
  const states = new Map();
  const target = () => {
    try { return storage || globalThis.sessionStorage; } catch { return null; }
  };
  const key = (id) => `wl_mannequin_quote_${id}`;
  const read = (id) => {
    if (states.has(id)) return states.get(id);
    try { return JSON.parse(target()?.getItem(key(id)) || 'null') || {}; } catch { return {}; }
  };
  const write = (id, state) => {
    states.set(id, state);
    try { target()?.setItem(key(id), JSON.stringify(state)); } catch { /* 메모리본 유지 */ }
  };
  const changedError = (quote) => Object.assign(new Error('마네킹 생성 견적이 바뀌었어요. 현재 견적을 확인해 주세요.'), {
    status: 409, code: 'quote_changed', meta: { quote },
  });
  return {
    remember(id, operation, quote) {
      write(id, { ...read(id), [operation]: quote });
    },
    pending: (id) => read(id).pending || null,
    seen: (id, operation) => read(id)[operation] || null,
    clear(id) {
      states.delete(id);
      try { target()?.removeItem(key(id)); } catch { /* 무시 */ }
    },
    async request(id, operation, request, { expectedQuote, confirmed = false, ...options } = {}) {
      const state = read(id);
      if (state.pending && !confirmed) throw changedError(state.pending.quote);
      const quote = confirmed && state.pending ? state.pending.quote : expectedQuote || state[operation];
      try {
        const result = await request({ ...options, expectedQuote: quote });
        write(id, { ...read(id), [operation]: quote, pending: null });
        return result;
      } catch (error) {
        if (error?.status === 409 && error.code === 'quote_changed') {
          write(id, { ...state, pending: { operation, quote: error.meta.quote } });
        }
        throw error;
      }
    },
  };
}

export const mannequinQuoteConfirmation = createMannequinQuoteConfirmation();
