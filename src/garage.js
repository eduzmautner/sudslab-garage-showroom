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

// See-through liquids: how far light travels through the bottle (m), the distance over
// which it takes on the liquid's full colour (shorter = deeper colour), and how
// see-through a strongly coloured liquid is (0 = solid, 1 = fully clear).
const LIQUID = { thickness: 0.08, attenuationDistance: 1.0, clarity: 0.4 };

// Fetches a binary asset. With `packed`, reads `<url>.json` ({ data: base64 }) instead,
// for hosts that only serve text and image files.
async function loadBuffer(url, packed) {
  if (!packed) return (await fetch(url)).arrayBuffer();
  const { data } = await (await fetch(`${url}.json`)).json();
  return Uint8Array.from(atob(data), (c) => c.charCodeAt(0)).buffer;
}

export async function loadGarage(scene, renderer, base, packed = false) {
  const draco = new DRACOLoader().setDecoderPath(`${base}draco/`);
  const gltf = new GLTFLoader().setDRACOLoader(draco);

  const [room, products, hdr, slots] = await Promise.all([
    loadBuffer(`${base}static.glb`, packed).then((b) => gltf.parseAsync(b, base)),
    loadBuffer(`${base}products.glb`, packed).then((b) => gltf.parseAsync(b, base)),
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
  room.scene.traverse((o) => {
    if (!o.isMesh) return;
    const map = o.material.map;
    if (map) map.anisotropy = maxAniso;
    o.material = new THREE.MeshBasicMaterial({ map, toneMapped: false });
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
  sun.shadow.mapSize.set(4096, 4096);
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
  return { hotspots };
}
