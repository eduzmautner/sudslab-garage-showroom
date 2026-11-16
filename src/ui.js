// DOM layer: hotspot buttons, product panel, fullscreen + zoom controls.

const ICON = {
  plus: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>',
  close: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>',
  prev: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 5l-7 7 7 7"/></svg>',
  next: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 5l7 7-7 7"/></svg>',
  expand: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></svg>',
  shrink: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5"/></svg>',
  minus: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14"/></svg>',
  check: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
  arrow: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 17L17 7M9 7h8v8"/></svg>',
};

const h = (tag, cls, html) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html !== undefined) e.innerHTML = html;
  return e;
};
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const money = (p) => `$${Number(p).toFixed(2)}`;
const sized = (url, w) => (url.includes('cdn.shopify.com') ? `${url}${url.includes('?') ? '&' : '?'}width=${w}` : url);

export function createUI(root, stage, { products, onSelect, onClose, onZoom, onFullscreenChange }) {
  const bySlot = Object.fromEntries(products.map((p) => [p.slot, p]));
  let current = -1;

  // ----- hotspots -----
  const layer = h('div', 'sl-hotspots');
  stage.appendChild(layer);
  const dots = {};
  products.forEach((p, i) => {
    const b = h('button', 'sl-hotspot', ICON.plus);
    b.type = 'button';
    b.setAttribute('aria-label', `View ${p.title}`);
    b.addEventListener('click', () => open(i));
    layer.appendChild(b);
    dots[p.slot] = b;
  });

  // ----- controls -----
  const controls = h('div', 'sl-controls');
  const fsBtn = h('button', 'sl-btn sl-fs', ICON.expand);
  fsBtn.type = 'button';
  fsBtn.setAttribute('aria-label', 'Enter fullscreen');
  const zoomIn = h('button', 'sl-btn', ICON.plus);
  zoomIn.type = 'button';
  zoomIn.setAttribute('aria-label', 'Zoom in');
  const zoomOut = h('button', 'sl-btn', ICON.minus);
  zoomOut.type = 'button';
  zoomOut.setAttribute('aria-label', 'Zoom out');
  const zoomGroup = h('div', 'sl-zoom');
  zoomGroup.append(zoomIn, zoomOut);
  controls.append(fsBtn, zoomGroup);
  stage.appendChild(controls);
  zoomIn.addEventListener('click', () => onZoom(1));
  zoomOut.addEventListener('click', () => onZoom(-1));

  const hint = h('div', 'sl-hint', 'Drag to look around &nbsp;·&nbsp; Tap <span class="sl-hint-dot">+</span> to shop');
  stage.appendChild(hint);
  const dismissHint = () => hint.classList.add('is-hidden');

  // ----- fullscreen (native where available, CSS fallback for iOS) -----
  const isFullscreen = () => document.fullscreenElement === root || root.classList.contains('is-expanded');
  const syncFs = () => {
    const on = isFullscreen();
    root.classList.toggle('is-fullscreen', on);
    fsBtn.innerHTML = on ? ICON.shrink : ICON.expand;
    fsBtn.setAttribute('aria-label', on ? 'Exit fullscreen' : 'Enter fullscreen');
    onFullscreenChange(on);
  };
  fsBtn.addEventListener('click', async () => {
    if (isFullscreen()) {
      if (document.fullscreenElement) await document.exitFullscreen();
      root.classList.remove('is-expanded');
      document.documentElement.classList.remove('sl-lock');
    } else if (root.requestFullscreen) {
      try { await root.requestFullscreen(); } catch { root.classList.add('is-expanded'); }
    } else {
      root.classList.add('is-expanded');
      document.documentElement.classList.add('sl-lock');
    }
    syncFs();
  });
  document.addEventListener('fullscreenchange', syncFs);
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (current >= 0) close();
    else if (root.classList.contains('is-expanded')) fsBtn.click();
  });

  // ----- product panel -----
  const panel = h('aside', 'sl-panel');
  panel.setAttribute('aria-hidden', 'true');
  panel.innerHTML = `
    <div class="sl-panel-head">
      <button type="button" class="sl-btn sl-prev" aria-label="Previous product">${ICON.prev}</button>
      <span class="sl-count"></span>
      <button type="button" class="sl-btn sl-next" aria-label="Next product">${ICON.next}</button>
      <button type="button" class="sl-btn sl-close" aria-label="Close">${ICON.close}</button>
    </div>
    <div class="sl-panel-body"></div>`;
  root.appendChild(panel);
  const body = panel.querySelector('.sl-panel-body');
  const count = panel.querySelector('.sl-count');
  panel.querySelector('.sl-prev').addEventListener('click', () => open((current - 1 + products.length) % products.length));
  panel.querySelector('.sl-next').addEventListener('click', () => open((current + 1) % products.length));
  panel.querySelector('.sl-close').addEventListener('click', () => close());

  // On the live Shopify store we can add straight to cart; elsewhere link out.
  const onShopify = typeof window.Shopify !== 'undefined' && !!window.Shopify.routes;

  function render(p) {
    const others = products.filter((o) => o.slot !== p.slot).slice(0, 8);
    const canAdd = onShopify && p.variantId;
    body.innerHTML = `
      <div class="sl-media"><img src="${esc(sized(p.image, 800))}" alt="${esc(p.title)}"></div>
      <h3 class="sl-title">${esc(p.title)}</h3>
      <div class="sl-price">${money(p.price)}</div>
      <ul class="sl-bullets">${(p.bullets || []).map((b) => `<li>${ICON.check}<span>${esc(b)}</span></li>`).join('')}</ul>
      ${canAdd
        ? `<button type="button" class="sl-cta" data-variant="${esc(p.variantId)}">Add to cart</button>
           <a class="sl-link" href="${esc(p.url)}">View full details</a>`
        : `<a class="sl-cta" href="${esc(p.url)}" target="_blank" rel="noopener">Shop now ${ICON.arrow}</a>
           <div class="sl-note">Opens the product page in a new tab</div>`}
      <div class="sl-more-label">More in this garage</div>
      <div class="sl-more">${others.map((o) => `
        <button type="button" class="sl-card" data-slot="${esc(o.slot)}">
          <img src="${esc(sized(o.image, 240))}" alt="" loading="lazy">
          <span>${esc(o.title)}</span>
        </button>`).join('')}</div>`;
    body.scrollTop = 0;
    body.querySelectorAll('.sl-card').forEach((c) =>
      c.addEventListener('click', () => open(products.findIndex((o) => o.slot === c.dataset.slot))));
    const add = body.querySelector('button.sl-cta');
    if (add) add.addEventListener('click', () => addToCart(add));
  }

  async function addToCart(btn) {
    btn.disabled = true;
    btn.textContent = 'Adding…';
    try {
      const res = await fetch(`${window.Shopify.routes.root}cart/add.js`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ items: [{ id: Number(btn.dataset.variant), quantity: 1 }] }),
      });
      if (!res.ok) throw new Error(String(res.status));
      btn.textContent = 'Added to cart';
      // Themes listen for different events to refresh the cart drawer; this is the hook.
      document.dispatchEvent(new CustomEvent('sudslab:cart-added', { detail: await res.json() }));
    } catch {
      btn.textContent = 'Could not add — try again';
    }
    setTimeout(() => { btn.disabled = false; btn.textContent = 'Add to cart'; }, 2200);
  }

  function open(i) {
    current = i;
    const p = products[i];
    render(p);
    count.textContent = `${i + 1} / ${products.length}`;
    root.classList.add('has-panel');
    panel.setAttribute('aria-hidden', 'false');
    Object.entries(dots).forEach(([slot, d]) => d.classList.toggle('is-active', slot === p.slot));
    dismissHint();
    onSelect(p.slot);
  }

  function close(silent) {
    if (current < 0) return;
    current = -1;
    root.classList.remove('has-panel');
    panel.setAttribute('aria-hidden', 'true');
    Object.values(dots).forEach((d) => d.classList.remove('is-active'));
    if (!silent) onClose();
  }

  return {
    open, close, dismissHint, isFullscreen,
    // share of the stage width covered by the panel (0 when it is a bottom sheet)
    panelFraction: () => (window.matchMedia('(max-width: 720px)').matches ? 0 : Math.min(0.5, panel.offsetWidth / stage.clientWidth)),
    placeHotspot(slot, on, x, y) {
      const d = dots[slot];
      if (!d || !bySlot[slot]) return;
      d.style.display = on ? '' : 'none';
      if (on) d.style.transform = `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px) translate(-50%, -50%)`;
    },
  };
}
