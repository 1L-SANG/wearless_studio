/* head의 --wl-t는 문서 시작 시각, currentTime은 그 뒤의 경과 시간이다. */
export function syncSellerGlowPhase(background) {
  const origin = performance.timeOrigin || (Date.now() - performance.now());
  const elapsed = Date.now() - origin;
  background?.querySelectorAll('.wl-orb > i, .wl-rb').forEach((layer) => {
    layer.getAnimations?.().forEach((animation) => { animation.currentTime = elapsed; });
  });
}

export function removeSellerBootBackground() {
  document.getElementById('seller-boot-bg')?.remove();
}
