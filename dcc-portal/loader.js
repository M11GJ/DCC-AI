// DCC AI（Open WebUI）の /static/loader.js に差し込むスクリプト。
// DCCポータルに埋め込まれているときだけ、横に払われた向きをポータルへ知らせる。
// 判定の値は src/utils/tabSwipe.ts とそろえる。
(() => {
  if (window.parent === window) return

  const PORTAL_ORIGINS = ['https://app.shu-dcc.net', 'https://staging.shu-dcc.net']
  const MIN_DISTANCE = 60
  const MIN_RATIO = 1.5
  // 左端からの操作はOpen WebUIのサイドバーを開く操作と重なるため使わない。
  const LEFT_EDGE = 40

  const ignores = target => {
    for (let el = target instanceof Element ? target : null; el && el !== document.body; el = el.parentElement) {
      if (el.matches('input, textarea, select, [contenteditable=""], [contenteditable="true"]')) return true
      if (el.scrollWidth > el.clientWidth && /auto|scroll/.test(getComputedStyle(el).overflowX)) return true
    }
    return false
  }

  let start = null
  document.addEventListener('touchstart', event => {
    const touch = event.touches[0]
    start = event.touches.length === 1 && touch.clientX > LEFT_EDGE && !ignores(event.target)
      ? { x: touch.clientX, y: touch.clientY }
      : null
  }, { passive: true })

  document.addEventListener('touchend', event => {
    const touch = event.changedTouches[0]
    if (!start || !touch) return
    const dx = touch.clientX - start.x
    const dy = touch.clientY - start.y
    start = null
    if (Math.abs(dx) < MIN_DISTANCE || Math.abs(dx) < Math.abs(dy) * MIN_RATIO) return
    const message = { source: 'dcc-ai', type: 'dcc-swipe', direction: dx < 0 ? 'next' : 'prev' }
    // 宛先が違う場合、ブラウザは黙って捨てる。埋め込み元のポータルにだけ届く。
    for (const origin of PORTAL_ORIGINS) window.parent.postMessage(message, origin)
  }, { passive: true })
})()
