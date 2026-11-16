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
    if (o.isMesh && o.material.map) o.material.map.anisotropy = maxAniso;
  });
  scene.add(products.scene);

  scene.background = new THREE.Color(0xe4edf7); // blown-out sky above the trees

  const hotspots = {};
  for (const s of slots) {
    hotspots[s.slot] = new THREE.Vector3(s.position[0], s.position[1] + s.hotspotOffsetY, s.position[2]);
  }
  return { hotspots };
}
