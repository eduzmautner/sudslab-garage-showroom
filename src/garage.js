import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { DRACOLoader } from 'three/examples/jsm/loaders/DRACOLoader.js';
import { RGBELoader } from 'three/examples/jsm/loaders/RGBELoader.js';

// Loads the garage exported by blender/bake_export.py:
//   static.glb    room + furniture with lighting baked into textures (shown unlit)
//   products.glb  products with normal materials, lit by env.hdr
//   slots.json    product positions for the hotspots
// The baked textures already carry Blender's exposure and tone curve; EXPOSURE
// applies the same exposure to the live-lit products so the two sit together.
export const EXPOSURE = 2 ** 1.6;

// Live-lit objects use the Neutral tone curve: three's AgX has no contrast look (the
// bake uses 'AgX - Medium High Contrast') and pushes oranges toward pale pink, so
// brand colours came out washed. Neutral keeps a product's own colour.
export const TONE_MAPPING = THREE.NeutralToneMapping;

// The 360 capture is taken mid-room, where the open door makes it brighter than the
// bench actually is, and it lights objects evenly from every side. So it is turned
// down and the three pendant lamps over the bench are added as real lights, which
// gives the products a lit top and a darker underside. [x, y, z] in metres.
const ENV_INTENSITY = 0.55;
const PENDANTS = [[-1.35, 1.96, -2.78], [-0.35, 1.96, -2.78], [0.65, 1.96, -2.78]];
const PENDANT_INTENSITY = 0.2;

// The sun: direction and colour match blender/build_garage.py (SUN_ELEV, SUN_AZIM).
// Blender's strength is 7, but its tone curve rolls bright colour off gently and the
// one used here clips to a pale coral, so the intensity is set by eye instead.
const SUN = { elevation: 15, azimuth: 20, intensity: 1.6, color: new THREE.Color(1.0, 0.83, 0.62) };

// The architect's lamp on the tool chest: a spot for the live-lit objects under it. Its
// position and aim come from two markers in products.glb ('Lamp spot', 'Lamp aim'), so
// moving the lamp in blender/build_garage.py moves the light too. Intensity is set by eye.
const LAMP = { color: 0xffd6a6, intensity: 0.232, angle: 0.85, penumbra: 0.6 };

// See-through liquids: how far light travels through the bottle (m), the distance over
// which it takes on the liquid's full colour (shorter = deeper colour), and how
// see-through a strongly coloured liquid is (0 = solid, 1 = fully clear).
const LIQUID = { thickness: 0.08, attenuationDistance: 1.0, clarity: 0.4 };

// The moulded jugs (natural plastic with liquid behind it): how solid they look.
// 1 = opaque, 0.5 = half the jug's own colour and half the room behind it.
const JUG = { opacity: 0.5 };

// A light breeze in the trees and bushes outside. They are one baked mesh, so the motion is
// done by the graphics card: every point leans with the wind by an amount that grows with
// its height above the ground (trunks stay planted), and the foliage stirs a little on top.
// The offset depends only on where a point is, so the faceted shapes never open at a seam.
//   strength  1 = as tuned, 0.5 = half the movement, 0 = still
//   loop      seconds until the motion repeats; every rhythm below fits a whole number of
//             times into it, so the repeat has no jump
//   fps       how often the picture is redrawn while only the trees are moving
// Slowing the motion = a longer loop: 37.5 s is the first tuning (30 s) made 20% slower.
export const BREEZE = { strength: 0.8, loop: 37.5, fps: 30 };
const BREEZE_GLSL = /* glsl */ `
uniform float uBreezeTime;      // 0..1 across one loop
uniform float uBreezeStrength;
vec3 breeze(vec3 p) {
  float T = 6.2831853 * uBreezeTime;
  float h = max(p.y, 0.0);
  vec2 wind = vec2(0.86, 0.5);                                     // the way the gusts travel (x, z)
  float along = dot(p.xz, wind);
  float gust = 0.65 + 0.35 * sin(T * 2.0 - along * 0.11 + 1.0);    // slow swell passing through the yard
  float sway = sin(T * 7.0 - along * 0.25) * gust;                 // main lean, 7 times per loop (about 5 s back and forth)
  float cross = 0.35 * sin(T * 5.0 - along * 0.2 + p.x * 0.07 + 2.1);
  float bend = 0.10 * pow(h / 4.0, 1.5);                           // at strength 1: 10 cm at 4 m up, nothing at the ground
  vec3 o = vec3(wind.x * sway - wind.y * cross, 0.0, wind.y * sway + wind.x * cross) * bend;
  float stir = 0.018 * min(h / 1.5, 1.0);                          // leaves: small, quicker, different clump to clump
  o.x += stir * sin(T * 13.0 + p.x * 1.9 + p.y * 2.3 + p.z * 0.7);
  o.z += stir * sin(T * 11.0 + p.z * 1.7 - p.y * 2.1 + 1.7);
  o.y += stir * 0.5 * sin(T * 9.0 + p.x * 1.3 + p.z * 1.5);
  return o * uBreezeStrength;
}`;

// Which set of garage files to load. The full set needs about 1.2 GB of graphics memory,
// nearly all of it textures, which is more than a phone's browser allows a page: it closes
// the page partway through loading. 'lite' is the same garage with every picture shrunk
// (about 200 MB; see step 6 of blender/bake_export.py) and a smaller shadow map.
// Touch-only devices (phones, tablets), devices that report 4 GB of memory or less, and
// graphics chips that cannot hold a full-size sheet get 'lite'. To force one for a test, add
// ?garage=lite or ?garage=full to the address, or set data-quality on the showroom element.
export function pickQuality(root, renderer) {
  const asked = new URLSearchParams(window.location.search).get('garage') || root.dataset.quality;
  if (asked === 'lite' || asked === 'full') return asked;
  const touchOnly = window.matchMedia('(hover: none) and (pointer: coarse)').matches;
  const smallMemory = navigator.deviceMemory !== undefined && navigator.deviceMemory <= 4;
  const smallSheets = renderer.capabilities.maxTextureSize < 8192;
  return touchOnly || smallMemory || smallSheets ? 'lite' : 'full';
}

// Fetches a binary asset. With `packed`, reads `<url>.json` ({ data: base64 }) instead,
// for hosts that only serve text and image files.
async function loadBuffer(url, packed) {
  if (!packed) return (await fetch(url)).arrayBuffer();
  const { data } = await (await fetch(`${url}.json`)).json();
  return Uint8Array.from(atob(data), (c) => c.charCodeAt(0)).buffer;
}

export async function loadGarage(scene, renderer, base, packed = false, quality = 'full') {
  const lite = quality === 'lite';
  const draco = new DRACOLoader().setDecoderPath(`${base}draco/`);
  const gltf = new GLTFLoader().setDRACOLoader(draco);

  // a host that has not been given the light files yet falls back to the full ones
  const model = (name) => {
    const get = (file) => loadBuffer(`${base}${file}`, packed).then((b) => gltf.parseAsync(b, base));
    return lite ? get(`${name}-lite.glb`).catch(() => get(`${name}.glb`)) : get(`${name}.glb`);
  };
  const [room, products, hdr, slots] = await Promise.all([
    model('static'),
    model('products'),
    loadBuffer(`${base}env.hdr`, packed).then((b) => new RGBELoader().parse(b)),
    fetch(`${base}slots.json`).then((r) => r.json()),
  ]);
  draco.dispose();
  const env = new THREE.DataTexture(hdr.data, hdr.width, hdr.height, THREE.RGBAFormat, hdr.type);
  env.colorSpace = THREE.LinearSRGBColorSpace;
  env.minFilter = env.magFilter = THREE.LinearFilter;
  env.generateMipmaps = false;
  env.flipY = true;
  env.needsUpdate = true;

  const maxAniso = renderer.capabilities.getMaxAnisotropy();
  let breeze = null;      // set once the trees are found: the two numbers the frame loop drives
  room.scene.traverse((o) => {
    if (!o.isMesh) return;
    const map = o.material.map;
    if (map) map.anisotropy = maxAniso;
    o.material = new THREE.MeshBasicMaterial({ map, toneMapped: false });
    if (o.name.startsWith('baked_plants')) {
      breeze = breeze || { time: { value: 0 }, strength: { value: BREEZE.strength } };
      o.material.onBeforeCompile = (shader) => {
        shader.uniforms.uBreezeTime = breeze.time;
        shader.uniforms.uBreezeStrength = breeze.strength;
        shader.vertexShader = shader.vertexShader
          .replace('#include <common>', `#include <common>\n${BREEZE_GLSL}`)
          .replace('#include <project_vertex>', `
            vec4 breezeWorld = modelMatrix * vec4(transformed, 1.0);
            breezeWorld.xyz += breeze(breezeWorld.xyz);
            vec4 mvPosition = viewMatrix * breezeWorld;
            gl_Position = projectionMatrix * mvPosition;`);
      };
      o.material.customProgramCacheKey = () => 'breeze';
    }
    o.matrixAutoUpdate = false;
    o.castShadow = true;   // the room shades the live sun, so it only lands where the baked sun does
  });
  scene.add(room.scene);

  // 360 capture from inside the room -> image-based light for the products
  const pmrem = new THREE.PMREMGenerator(renderer);
  env.mapping = THREE.EquirectangularReflectionMapping;
  scene.environment = pmrem.fromEquirectangular(env).texture;
  scene.environmentRotation.y = Math.PI / 2; // capture's centre faces the back wall (-z)
  env.dispose();
  pmrem.dispose();
  products.scene.traverse((o) => {
    if (!o.isMesh) return;
    const m = o.material;
    if (m.map) m.map.anisotropy = maxAniso;
    if (m.name.startsWith('Jug plastic')) {
      // Plain blending rather than glass-style transmission: transmission tints the room
      // behind with the jug's colour, and pink over a cream wall is just pink again, so
      // the jug did not read as see-through at all. Both sides are drawn, which lets the
      // far wall and base of the jug show faintly through the near one.
      m.transmission = 0;
      m.transparent = true;
      m.opacity = JUG.opacity;
      m.depthWrite = false;
      m.side = THREE.DoubleSide;
    }
    if (m.transmission > 0 && !m.userData.tuned) {
      // See-through materials arrive with just a flat tint. Give them depth instead:
      // clear at the edges of thin plastic, richer colour through a bottle of liquid.
      m.userData.tuned = true;
      const plastic = m.name.startsWith('Bottle plastic');
      m.attenuationColor = m.color.clone();
      // What shows through is the baked room, which already has its exposure applied.
      // The renderer would apply EXPOSURE to it a second time (the "glowing" plastic),
      // so see-through materials pass on 1 / EXPOSURE of what is behind them.
      m.color.setScalar(1 / EXPOSURE);
      if (!plastic) {
        // A liquid seen only by what is behind it turns muddy (orange over a wood
        // bench reads brown). Real liquid also scatters light, so part of it is
        // shaded as a solid in the liquid's colour: more for strong colours, least
        // for colourless ones.
        const tint = m.attenuationColor;
        m.transmission = LIQUID.clarity + (0.9 - LIQUID.clarity) * Math.min(tint.r, tint.g, tint.b);
        m.color.copy(tint).multiplyScalar(1 / (m.transmission * EXPOSURE));
      }
      m.thickness = plastic ? 0.003 : LIQUID.thickness;
      m.attenuationDistance = plastic ? 0.05 : LIQUID.attenuationDistance;
      m.ior = plastic ? 1.45 : 1.33;
    }
  });
  scene.add(products.scene);
  scene.environmentIntensity = ENV_INTENSITY;
  for (const p of PENDANTS) {
    const lamp = new THREE.PointLight(0xffc78c, PENDANT_INTENSITY, 0, 2);
    lamp.position.set(...p);
    scene.add(lamp);
  }

  const lampAt = products.scene.getObjectByName('Lamp_spot');
  const lampAim = products.scene.getObjectByName('Lamp_aim');
  if (lampAt && lampAim) {
    products.scene.updateMatrixWorld(true);
    const lamp = new THREE.SpotLight(LAMP.color, LAMP.intensity, 0, LAMP.angle, LAMP.penumbra, 2);
    lampAt.getWorldPosition(lamp.position);
    lampAim.getWorldPosition(lamp.target.position);
    scene.add(lamp, lamp.target);
  }

  // The same sun the bake used, for the live-lit objects. The room casts shadows on
  // them, so only objects standing in the baked sun patch are lit by it. Nothing
  // moves, so the shadow map is drawn once.
  products.scene.traverse((o) => {
    if (o.isMesh) o.castShadow = o.receiveShadow = true;
  });
  const sun = new THREE.DirectionalLight(SUN.color, SUN.intensity);
  const el = THREE.MathUtils.degToRad(SUN.elevation), az = THREE.MathUtils.degToRad(SUN.azimuth);
  sun.position.set(Math.sin(az) * Math.cos(el), Math.sin(el), Math.cos(az) * Math.cos(el)).multiplyScalar(20);
  sun.castShadow = true;
  sun.shadow.mapSize.set(lite ? 2048 : 4096, lite ? 2048 : 4096);
  Object.assign(sun.shadow.camera, { left: -6, right: 6, top: 6, bottom: -6, near: 5, far: 35 });
  sun.shadow.bias = -0.0004;
  sun.shadow.normalBias = 0.01;
  sun.shadow.autoUpdate = false;
  sun.shadow.needsUpdate = true;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  scene.add(sun);

  scene.background = new THREE.Color(0xe4edf7); // blown-out sky above the trees

  const hotspots = {};
  for (const s of slots) {
    hotspots[s.slot] = new THREE.Vector3(s.position[0], s.position[1] + s.hotspotOffsetY, s.position[2]);
  }
  return { hotspots, breeze };
}
