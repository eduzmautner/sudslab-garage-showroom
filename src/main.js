import './showroom.css';
import { mountShowroom } from './showroom.js';
import { PRODUCTS } from './products.js';

// Auto-mounts on every <div data-sudslab-showroom>. On Shopify the section
// renders a <script type="application/json"> child with live product data.
function readProducts(el) {
  const tag = el.querySelector('script[type="application/json"]');
  if (!tag) return PRODUCTS;
  try {
    const live = JSON.parse(tag.textContent);
    if (!Array.isArray(live) || !live.length) return PRODUCTS;
    // keep the built-in bullets as a fallback for products without them
    const fallback = Object.fromEntries(PRODUCTS.map((p) => [p.slot, p]));
    return live.map((p) => ({ ...p, bullets: p.bullets && p.bullets.length ? p.bullets : (fallback[p.slot] || {}).bullets || [] }));
  } catch {
    return PRODUCTS;
  }
}

function boot() {
  document.querySelectorAll('[data-sudslab-showroom]:not([data-mounted])').forEach((el) => {
    el.dataset.mounted = '1';
    mountShowroom(el, { products: readProducts(el) }).then((api) => { el.__showroom = api; });
  });
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
else boot();
document.addEventListener('shopify:section:load', boot); // theme editor
