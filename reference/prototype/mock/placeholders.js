/* =============================================================
   mock/placeholders.js
   Monochrome SVG placeholder "photos". The design system forbids
   stock photography; these grayscale tiles stand in for fashion
   cuts and stay on-brand. Deterministic-ish by seed so the same
   asset id always renders the same tile.
   Exposes: Placeholder.* helpers on window.
   ============================================================= */
(function () {
  const TONES = ['#efeef0', '#e9e8eb', '#e3e2e6', '#edeaea', '#e6e7ea', '#eceaec'];
  // mid-tone silhouettes so the layout still reads when the canvas is zoomed out (#3)
  const INK = '#a9a7b2';
  const INK2 = '#9d9ba6';

  function svg(w, h, inner, bg) {
    const s = `<svg xmlns='http://www.w3.org/2000/svg' width='${w}' height='${h}' viewBox='0 0 ${w} ${h}'>` +
      `<defs><radialGradient id='g' cx='50%' cy='32%' r='80%'>` +
      `<stop offset='0%' stop-color='#fbfbfc'/><stop offset='100%' stop-color='${bg}'/></radialGradient></defs>` +
      `<rect width='${w}' height='${h}' fill='url(#g)'/>${inner}</svg>`;
    return 'data:image/svg+xml;utf8,' + encodeURIComponent(s);
  }
  function hash(str) { let h = 0; str = String(str); for (let i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) | 0; return Math.abs(h); }
  function tone(seed) { return TONES[hash(seed) % TONES.length]; }

  // a soft standing figure (head + torso + legs), faceless
  function figure(cx, topY, scale, color) {
    const s = scale;
    return (
      `<g fill='${color}' opacity='.9'>` +
      `<circle cx='${cx}' cy='${topY}' r='${22 * s}'/>` +
      `<path d='M ${cx - 30 * s} ${topY + 30 * s} q ${30 * s} ${-12 * s} ${60 * s} 0 l ${10 * s} ${150 * s} q ${-40 * s} ${16 * s} ${-80 * s} 0 z'/>` +
      `<rect x='${cx - 26 * s}' y='${topY + 178 * s}' width='${22 * s}' height='${150 * s}' rx='${10 * s}'/>` +
      `<rect x='${cx + 4 * s}' y='${topY + 178 * s}' width='${22 * s}' height='${150 * s}' rx='${10 * s}'/>` +
      `</g>`
    );
  }
  // a hanging ghost garment (product cut)
  function garment(cx, cy, scale, color) {
    const s = scale;
    return (
      `<g fill='${color}' opacity='.92'>` +
      `<path d='M ${cx - 70 * s} ${cy - 60 * s} l ${30 * s} ${-22 * s} q ${40 * s} ${22 * s} ${80 * s} 0 l ${30 * s} ${22 * s} l ${-26 * s} ${34 * s} l ${-14 * s} ${-10 * s} l 0 ${150 * s} q ${-40 * s} ${16 * s} ${-80 * s} 0 l 0 ${-150 * s} l ${-14 * s} ${10 * s} z'/>` +
      `</g>`
    );
  }

  const Placeholder = {
    hash, tone,
    // 3:4 person cut (styling / horizon / mannequin)
    photo(seed = 'x', kind = 'styling', w = 600, h = 800) {
      const bg = tone(seed);
      const c = kind === 'mannequin' ? '#bdbbc6' : INK;
      const shift = (hash(seed + kind) % 60) - 30;
      return svg(w, h, figure(w / 2 + shift, h * 0.16, w / 600, c), bg);
    },
    // 3:4 ghost product cut
    product(seed = 'p', w = 600, h = 800) {
      return svg(w, h, garment(w / 2, h * 0.46, w / 600, INK2), tone(seed + 'p'));
    },
    // fabric / detail close-up: soft diagonal weave
    detail(seed = 'd', w = 600, h = 800) {
      const bg = tone(seed + 'd');
      let lines = '';
      for (let i = -2; i < 14; i++) lines += `<line x1='${i * 60}' y1='0' x2='${i * 60 + 220}' y2='${h}' stroke='#c2c0c8' stroke-width='14' opacity='.55'/>`;
      return svg(w, h, lines, bg);
    },
    // square model / match-clothing thumb
    portrait(seed = 'm', w = 400, h = 400) {
      return svg(w, h, figure(w / 2, h * 0.12, w / 520, INK), tone(seed + 'm'));
    },
    swatch(seed = 's', w = 400, h = 400) {
      return svg(w, h, garment(w / 2, h * 0.5, w / 720, INK2), tone(seed + 's'));
    },
    // tiny pose icon tile
    pose(seed = 'pose', w = 120, h = 120) {
      return svg(w, h, figure(w / 2, h * 0.1, w / 320, '#c4c2ca'), '#f1f0f2');
    },
    // tiny background scene swatch (horizon / studio / outdoor)
    scene(seed = 'bg', w = 120, h = 120) {
      const bg = tone(seed + 'bg');
      return svg(w, h, `<rect y='${h * 0.62}' width='${w}' height='${h * 0.38}' fill='#c8c6ce'/><circle cx='${w * 0.72}' cy='${h * 0.28}' r='${w * 0.12}' fill='#d6d4dc'/>`, bg);
    },
    // generic 'any image' for the editor + canvas
    any(seed) {
      const kinds = ['styling', 'horizon', 'product', 'detail'];
      const k = kinds[hash(seed || Math.random()) % kinds.length];
      if (k === 'product') return Placeholder.product(seed);
      if (k === 'detail') return Placeholder.detail(seed);
      return Placeholder.photo(seed, k);
    },
  };

  window.Placeholder = Placeholder;
})();
