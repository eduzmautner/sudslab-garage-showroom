import * as THREE from 'three';
import { loadGarage, EXPOSURE, TONE_MAPPING } from './garage.js';
import { createUI } from './ui.js';

const EYE = 1.5;
// Standing spots (floor rings). yaw 0 looks at the back wall, +yaw turns right.
const WAYPOINTS = [
  { pos: [0.3, EYE, 0.5], yaw: -0.3, pitch: -0.08 }, // spawn: centre, facing the pegboard + some left wall
  { pos: [-0.2, EYE, -1.35], yaw: -0.1, pitch: -0.1 }, // up close at the bench
  { pos: [1.35, EYE, 2.45], yaw: -0.62, pitch: -0.13 }, // by the door, looking in
];
const MAX_PIXELS = 2.6e6; // render budget; the baked scene is cheap, so this is generous
const H_FOV = 96; // target horizontal field of view, degrees
const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

export async function mountShowroom(root, { products }) {
  root.classList.add('sl-showroom');
  const stage = document.createElement('div');
  stage.className = 'sl-stage';
  root.appendChild(stage);
  const loading = document.createElement('div');
  loading.className = 'sl-loading';
  loading.textContent = 'Opening the garage…';
  root.appendChild(loading);

  const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' });
  renderer.toneMapping = TONE_MAPPING; // only affects the live-lit products
  renderer.toneMappingExposure = EXPOSURE;
  stage.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(60, 2, 0.05, 80);
  const base = root.dataset.assetBase || `${import.meta.env.BASE_URL}garage/`;
  let hotspots;
  try {
    ({ hotspots } = await loadGarage(scene, renderer, base, 'assetPacked' in root.dataset));
  } catch (err) {
    loading.textContent = 'The garage could not be loaded.';
    throw err;
  }

  // waypoint rings
  const rings = [];
  const ringGeo = new THREE.RingGeometry(0.2, 0.235, 48);
  WAYPOINTS.forEach((w, i) => {
    const ring = new THREE.Mesh(ringGeo, new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.85, depthWrite: false, toneMapped: false }));
    ring.rotation.x = -Math.PI / 2;
    ring.position.set(w.pos[0], 0.012, w.pos[2]);
    ring.userData.waypoint = i;
    const hit = new THREE.Mesh(new THREE.CircleGeometry(0.42, 24), new THREE.MeshBasicMaterial({ visible: false }));
    hit.userData.waypoint = i;
    ring.add(hit);
    scene.add(ring);
    rings.push(ring);
  });

  // ---------- view state ----------
  const view = { wp: 0, pos: new THREE.Vector3(...WAYPOINTS[0].pos), yaw: WAYPOINTS[0].yaw, pitch: WAYPOINTS[0].pitch, zoom: 1 };
  const vel = { yaw: 0, pitch: 0 };
  let tween = null;
  let dirty = true;
  let visible = true;
  const invalidate = () => { dirty = true; };

  const applyCamera = () => {
    const aspect = camera.aspect;
    const v = THREE.MathUtils.radToDeg(2 * Math.atan(Math.tan(THREE.MathUtils.degToRad(H_FOV / 2)) / aspect));
    camera.fov = THREE.MathUtils.clamp(v, 42, 72) / view.zoom;
    camera.updateProjectionMatrix();
    camera.position.copy(view.pos);
    const cp = Math.cos(view.pitch);
    camera.lookAt(view.pos.x + Math.sin(view.yaw) * cp, view.pos.y + Math.sin(view.pitch), view.pos.z - Math.cos(view.yaw) * cp);
  };

  const resize = () => {
    const w = stage.clientWidth, h = stage.clientHeight;
    if (!w || !h) return;
    // Cap the rendered pixel count so a fullscreen 4K/retina canvas stays smooth.
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2, Math.sqrt(MAX_PIXELS / (w * h))));
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    invalidate();
  };
  new ResizeObserver(resize).observe(stage);
  new IntersectionObserver(([e]) => { visible = e.isIntersecting; if (visible) invalidate(); }).observe(stage);

  const shortest = (from, to) => from + Math.atan2(Math.sin(to - from), Math.cos(to - from));
  const flyTo = ({ pos, yaw, pitch, zoom, wp }, ms = 1100) => {
    if (wp !== undefined) view.wp = wp;
    tween = {
      t0: performance.now(), ms,
      from: { pos: view.pos.clone(), yaw: view.yaw, pitch: view.pitch, zoom: view.zoom },
      to: {
        pos: pos ? new THREE.Vector3(...pos) : view.pos.clone(),
        yaw: yaw === undefined ? view.yaw : shortest(view.yaw, yaw),
        pitch: pitch === undefined ? view.pitch : pitch,
        zoom: zoom === undefined ? view.zoom : zoom,
      },
    };
    vel.yaw = vel.pitch = 0;
    invalidate();
  };

  const goToWaypoint = (i) => {
    const w = WAYPOINTS[i];
    flyTo({ pos: w.pos, yaw: w.yaw, pitch: w.pitch, zoom: 1, wp: i });
  };

  // Turn (and if needed walk) to face a product, leaving room for the panel.
  const focusProduct = (slot) => {
    const p = hotspots[slot];
    if (!p) return;
    let best = view.wp, bestScore = Infinity;
    WAYPOINTS.forEach((w, i) => {
      const d = Math.hypot(p.x - w.pos[0], p.z - w.pos[2]);
      const score = (d < 1.0 ? 3 : d) + (i === view.wp ? -0.9 : 0);
      if (score < bestScore) { bestScore = score; best = i; }
    });
    const w = WAYPOINTS[best];
    const dx = p.x - w.pos[0], dy = p.y - 0.12 - w.pos[1], dz = p.z - w.pos[2];
    let yaw = Math.atan2(dx, -dz);
    const pitch = Math.atan2(dy, Math.hypot(dx, dz));
    yaw += THREE.MathUtils.degToRad((H_FOV / 1.6) * ui.panelFraction() * 0.5);
    flyTo({ pos: w.pos, yaw, pitch, zoom: 1.6, wp: best }, 1000);
  };

  const ui = createUI(root, stage, {
    products,
    onSelect: focusProduct,
    onClose: () => flyTo({ zoom: 1 }, 700),
    onZoom: (dir) => flyTo({ zoom: THREE.MathUtils.clamp(view.zoom * (dir > 0 ? 1.3 : 1 / 1.3), 1, 2.4) }, 350),
    onFullscreenChange: () => { resize(); },
  });

  // ---------- pointer look ----------
  const el = renderer.domElement;
  const ray = new THREE.Raycaster();
  let drag = null;
  const pickRing = (e) => {
    const r = el.getBoundingClientRect();
    ray.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), camera);
    const hit = ray.intersectObjects(rings, true)[0];
    return hit ? hit.object.userData.waypoint : -1;
  };
  el.addEventListener('pointerdown', (e) => {
    drag = { x: e.clientX, y: e.clientY, moved: 0 };
    el.setPointerCapture(e.pointerId);
    tween = null;
    root.classList.add('is-dragging');
    ui.dismissHint();
  });
  el.addEventListener('pointermove', (e) => {
    if (!drag) {
      const i = pickRing(e);
      el.style.cursor = i >= 0 && i !== view.wp ? 'pointer' : '';
      return;
    }
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    drag.x = e.clientX; drag.y = e.clientY;
    drag.moved += Math.abs(dx) + Math.abs(dy);
    const k = THREE.MathUtils.degToRad(camera.fov) / el.clientHeight;
    vel.yaw = -dx * k; vel.pitch = dy * k;
    view.yaw += vel.yaw;
    view.pitch = THREE.MathUtils.clamp(view.pitch + vel.pitch, -0.9, 0.75);
    invalidate();
  });
  const endDrag = (e) => {
    if (!drag) return;
    root.classList.remove('is-dragging');
    if (drag.moved < 6) {
      vel.yaw = vel.pitch = 0;
      const i = pickRing(e);
      if (i >= 0 && i !== view.wp) { ui.close(true); goToWaypoint(i); }
    }
    drag = null;
  };
  el.addEventListener('pointerup', endDrag);
  el.addEventListener('pointercancel', endDrag);
  // Wheel zoom only in fullscreen, so the embedded section never traps page scroll.
  el.addEventListener('wheel', (e) => {
    if (!ui.isFullscreen()) return;
    e.preventDefault();
    view.zoom = THREE.MathUtils.clamp(view.zoom * (e.deltaY < 0 ? 1.08 : 1 / 1.08), 1, 2.4);
    invalidate();
  }, { passive: false });

  // ---------- loop (renders only when something changed) ----------
  const proj = new THREE.Vector3();
  const fwd = new THREE.Vector3();
  const frame = (now) => {
    requestAnimationFrame(frame);
    if (!visible) return;
    if (tween) {
      const t = Math.min(1, (now - tween.t0) / tween.ms), k = ease(t);
      view.pos.lerpVectors(tween.from.pos, tween.to.pos, k);
      view.yaw = THREE.MathUtils.lerp(tween.from.yaw, tween.to.yaw, k);
      view.pitch = THREE.MathUtils.lerp(tween.from.pitch, tween.to.pitch, k);
      view.zoom = THREE.MathUtils.lerp(tween.from.zoom, tween.to.zoom, k);
      if (t >= 1) tween = null;
      dirty = true;
    } else if (!drag && (Math.abs(vel.yaw) > 1e-4 || Math.abs(vel.pitch) > 1e-4)) {
      view.yaw += vel.yaw;
      view.pitch = THREE.MathUtils.clamp(view.pitch + vel.pitch, -0.9, 0.75);
      vel.yaw *= 0.9; vel.pitch *= 0.9;
      dirty = true;
    }
    if (!dirty) return;
    dirty = false;
    applyCamera();
    rings.forEach((r, i) => { r.visible = i !== view.wp || !!tween; });
    renderer.render(scene, camera);
    camera.getWorldDirection(fwd);
    const w = stage.clientWidth, h = stage.clientHeight;
    for (const slot in hotspots) {
      proj.copy(hotspots[slot]);
      const facing = proj.clone().sub(camera.position).dot(fwd) > 0.2;
      proj.project(camera);
      const on = facing && Math.abs(proj.x) < 1.05 && Math.abs(proj.y) < 1.05;
      ui.placeHotspot(slot, on, (proj.x * 0.5 + 0.5) * w, (-proj.y * 0.5 + 0.5) * h);
    }
  };
  resize();
  requestAnimationFrame(frame);
  loading.remove();
  root.classList.add('is-ready');

  return { scene, camera, renderer, view, flyTo, goToWaypoint, invalidate, hotspots };
}
