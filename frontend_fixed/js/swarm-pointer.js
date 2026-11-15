/* swarm-pointer.js — Web port of Clicky's [POINT:x,y] visual-pointing magic.
 * Zero dependencies. Drops an animated cursor + speech bubble over the page
 * and guides the user to specific elements, one step at a time.
 *
 * Usage:
 *   SwarmPointer.start([
 *     { selector: '.stat.refu', text: 'We publish the 5 REFUTED scenarios — not hidden.' },
 *     { x: 200, y: 120,        text: 'Or point at raw viewport coordinates.' },
 *   ]);
 *   SwarmPointer.stop();
 */
(function () {
  'use strict';
  const CURSOR_BLUE = '#3b82f6';
  const Z = 2147483000;

  let layer = null, cursor = null, bubble = null, bar = null, hint = null;
  let steps = [], idx = -1, timer = null, running = false;

  function build() {
    if (layer) return;
    layer = document.createElement('div');
    layer.className = 'swarm-ptr-layer';
    layer.innerHTML =
      '<svg class="swarm-ptr-cursor" width="34" height="34" viewBox="0 0 34 34" fill="none">' +
        '<path d="M5 3 L5 27 L11 21 L15 31 L19 29 L15 19 L24 19 Z" ' +
              'fill="' + CURSOR_BLUE + '" stroke="#fff" stroke-width="2" stroke-linejoin="round"/>' +
      '</svg>' +
      '<div class="swarm-ptr-bubble"></div>' +
      '<div class="swarm-ptr-bar">' +
        '<span class="swarm-ptr-progress"></span>' +
        '<button class="swarm-ptr-prev">‹ 上一步</button>' +
        '<button class="swarm-ptr-next">下一步 ›</button>' +
        '<button class="swarm-ptr-skip">跳过 ✕</button>' +
      '</div>';
    document.body.appendChild(layer);
    cursor = layer.querySelector('.swarm-ptr-cursor');
    bubble = layer.querySelector('.swarm-ptr-bubble');
    bar = layer.querySelector('.swarm-ptr-bar');
    hint = layer.querySelector('.swarm-ptr-progress');
    bar.querySelector('.swarm-ptr-prev').addEventListener('click', function (e) { e.stopPropagation(); go(idx - 1); });
    bar.querySelector('.swarm-ptr-next').addEventListener('click', function (e) { e.stopPropagation(); go(idx + 1); });
    bar.querySelector('.swarm-ptr-skip').addEventListener('click', function (e) { e.stopPropagation(); stop(); });

    const style = document.createElement('style');
    style.textContent =
      '.swarm-ptr-layer{position:fixed;inset:0;z-index:' + Z + ';pointer-events:none;}' +
      '.swarm-ptr-cursor{position:fixed;left:0;top:0;filter:drop-shadow(0 2px 6px rgba(59,130,246,.55));' +
        'transition:transform .55s cubic-bezier(.22,1,.36,1);will-change:transform;}' +
      '.swarm-ptr-bubble{position:fixed;max-width:320px;background:#0f172a;color:#e2e8f0;' +
        'border:1px solid ' + CURSOR_BLUE + ';border-radius:12px;padding:12px 14px;font:14px/1.6 system-ui,sans-serif;' +
        'box-shadow:0 8px 30px rgba(0,0,0,.35);opacity:0;transform:translateY(6px);' +
        'transition:opacity .25s ease,transform .25s ease;}' +
      '.swarm-ptr-bubble.show{opacity:1;transform:translateY(0);}' +
      '.swarm-ptr-pulse{position:fixed;border:2px solid ' + CURSOR_BLUE + ';border-radius:10px;' +
        'box-shadow:0 0 0 0 rgba(59,130,246,.45);transition:box-shadow .4s ease;pointer-events:none;}' +
      '.swarm-ptr-pulse.on{box-shadow:0 0 0 8px rgba(59,130,246,0);}' +
      '.swarm-ptr-bar{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);display:flex;gap:8px;align-items:center;' +
        'background:rgba(15,23,42,.92);border:1px solid rgba(59,130,246,.5);border-radius:999px;padding:7px 10px;' +
        'pointer-events:auto;font:13px system-ui,sans-serif;}' +
      '.swarm-ptr-bar button{background:' + CURSOR_BLUE + ';color:#fff;border:0;border-radius:999px;' +
        'padding:6px 12px;cursor:pointer;font-size:13px;}' +
      '.swarm-ptr-bar button.swarm-ptr-skip{background:#475569;}' +
      '.swarm-ptr-progress{color:#94a3b8;margin-right:4px;white-space:nowrap;}';
    document.head.appendChild(style);
    // click anywhere advances (except on bar buttons)
    layer.addEventListener('click', function (e) {
      if (e.target.closest('.swarm-ptr-bar')) return;
      go(idx + 1);
    });
    document.addEventListener('keydown', onKey);
  }

  function onKey(e) {
    if (!running) return;
    if (e.key === 'Escape') stop();
    else if (e.key === 'ArrowRight' || e.key === 'Enter') go(idx + 1);
    else if (e.key === 'ArrowLeft') go(idx - 1);
  }

  function moveTo(x, y) {
    cursor.style.transform = 'translate(' + x + 'px,' + y + 'px)';
  }

  function pulse(el) {
    const p = document.createElement('div');
    p.className = 'swarm-ptr-pulse';
    const r = el.getBoundingClientRect();
    p.style.left = r.left - 4 + 'px'; p.style.top = r.top - 4 + 'px';
    p.style.width = r.width + 8 + 'px'; p.style.height = r.height + 8 + 'px';
    layer.appendChild(p);
    requestAnimationFrame(function () { p.classList.add('on'); });
    setTimeout(function () { p.remove(); }, 900);
  }

  function go(i) {
    if (!running) return;
    if (timer) { clearTimeout(timer); timer = null; }
    if (i >= steps.length) { stop(); return; }
    if (i < 0) i = 0;
    idx = i;
    const step = steps[i];
    let target = null;
    if (step.selector) target = document.querySelector(step.selector);
    if (!target && (step.x == null || step.y == null)) {
      // skip unresolvable step
      go(i + 1); return;
    }
    if (target) {
      target.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
    setTimeout(function () {
      let r;
      if (target) { r = target.getBoundingClientRect(); pulse(target); }
      else { r = { left: step.x, top: step.y, width: 0, height: 0 }; }
      // cursor points at top-left of the element (like a finger)
      const cx = r.left, cy = r.top;
      moveTo(cx, cy);
      // bubble placed below-right of the target, kept on screen
      const bw = 320, bh = 90;
      let bx = r.left + 18, by = r.top + r.height + 14;
      if (bx + bw > window.innerWidth - 12) bx = Math.max(12, window.innerWidth - bw - 12);
      if (by + bh > window.innerHeight - 12) by = Math.max(12, r.top - bh - 14);
      bubble.textContent = step.text;
      bubble.style.left = bx + 'px'; bubble.style.top = by + 'px';
      // force reflow then show
      void bubble.offsetWidth;
      bubble.classList.add('show');
      hint.textContent = (idx + 1) + ' / ' + steps.length;
      const hold = step.hold == null ? 4200 : step.hold;
      if (step.wait) return; // manual advance only
      timer = setTimeout(function () { go(idx + 1); }, hold);
    }, target ? 360 : 0);
  }

  function start(list, opts) {
    opts = opts || {};
    if (!list || !list.length) return;
    build();
    steps = list; idx = -1; running = true;
    layer.style.display = 'block';
    go(0);
  }

  function stop() {
    running = false;
    if (timer) { clearTimeout(timer); timer = null; }
    if (layer) {
      bubble.classList.remove('show');
      layer.style.display = 'none';
    }
  }

  window.SwarmPointer = { start: start, stop: stop };
})();
