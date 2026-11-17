"""
Bakes the garage lighting and exports everything the web viewer needs.

    blender -b --factory-startup -P blender/bake_export.py -- [--res=4096] [--samples=128]

Reads blender/garage.blend (never writes back to it) and produces, in public/garage/:

    static.glb     room, furniture, props and exterior. Lighting is baked into
                   three texture atlases with the scene's exposure and AgX look
                   already applied, so the browser just displays them unlit.
    products.glb   the products on their slot empties, with normal PBR
                   materials. They stay un-baked so they can be swapped.
    env.hdr        a small 360 capture from inside the room, used to light
                   the products in the browser.
    slots.json     product positions for the hotspots.

Baked light is diffuse only (no reflections), and metals are baked as plain
grey because a view-dependent highlight cannot live in a texture.
"""
import bpy, bmesh, math, os, shutil, sys, time
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), 'public', 'garage')
ARGS = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
def arg(name, default):
    for a in ARGS:
        if a.startswith(f'--{name}='):
            return int(a.split('=')[1])
    return default
RES = arg('res', 4096)
SAMPLES = arg('samples', 128)
T0 = time.time()
def log(msg):
    print(f'BAKE [{time.time() - T0:6.0f}s] {msg}', flush=True)

# interior extents, must match build_garage.py
W, D, H, T = 4.8, 6.8, 2.85, 0.2
HW, HD = W / 2, D / 2

# Bake groups. Texture sharpness is pixels per metre of surface, so the surfaces
# people look at closely get an atlas to themselves instead of sharing one.
HERO = ('Pegboard panel', 'Pegboard frame', 'Bench top', 'Bench apron', 'Peg shelf', 'Wall shelf', 'Chest top')
CEILING = ('Ceiling', 'Joist', 'LED ')
FLOOR = ('Floor',)
PLANTS = ('Tree', 'Shrub', 'Bush')      # own atlas, so the lawn doesn't eat their pixels
def in_cols(o, *names):
    return any(o.name in bpy.data.collections[n].all_objects for n in names)
GROUPS = [   # name, which objects, atlas size
    ('floor', lambda o: in_cols(o, 'Shell') and o.name.startswith(FLOOR), RES),
    ('walls', lambda o: in_cols(o, 'Shell') and not o.name.startswith(FLOOR + CEILING), RES),
    ('ceiling', lambda o: in_cols(o, 'Shell') and o.name.startswith(CEILING), max(512, RES // 2)),
    ('hero', lambda o: in_cols(o, 'Furniture', 'Props') and o.name.startswith(HERO), RES),
    ('interior', lambda o: in_cols(o, 'Furniture', 'Props', 'Lights') and not o.name.startswith(HERO), RES),
    ('plants', lambda o: in_cols(o, 'Exterior') and o.name.startswith(PLANTS), max(512, RES * 2 // 3)),
    ('exterior', lambda o: in_cols(o, 'Exterior') and not o.name.startswith(PLANTS), max(512, RES * 2 // 3)),
]
SHELL_GROUPS = ('floor', 'walls', 'ceiling')

bpy.ops.wm.open_mainfile(filepath=os.path.join(HERE, 'garage.blend'))
scene = bpy.context.scene
if '--dry' in ARGS:                    # a dry run must not touch the live web files
    import tempfile
    OUT = tempfile.mkdtemp()
for a in ARGS:                         # --out=DIR: write the web files somewhere else (test bakes)
    if a.startswith('--out='):
        OUT = a.split('=', 1)[1]
os.makedirs(OUT, exist_ok=True)

def gpu():
    try:
        prefs = bpy.context.preferences.addons['cycles'].preferences
        prefs.compute_device_type = 'OPTIX'
        prefs.get_devices()
        for d in prefs.devices:
            d.use = d.type == 'OPTIX'
        scene.cycles.device = 'GPU'
    except Exception as e:
        log(f'GPU setup failed, using CPU: {e}')
gpu()
scene.render.engine = 'CYCLES'

def select_only(objs):
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]

def export_glb(path, objs, **extra):
    select_only(objs)
    kw = dict(filepath=path, export_format='GLB', use_selection=True, export_apply=True, export_yup=True,
              export_extras=True, export_cameras=False, export_lights=False,
              export_draco_mesh_compression_enable=True, export_draco_mesh_compression_level=6)
    kw.update(extra)
    try:
        bpy.ops.export_scene.gltf(**kw)
    except TypeError as e:          # option names shift between Blender versions
        log(f'export option fallback: {e}')
        for k in list(extra):
            kw.pop(k, None)
        bpy.ops.export_scene.gltf(**kw)
    log(f'wrote {os.path.basename(path)}  {os.path.getsize(path) / 1e6:.2f} MB')

# ---------------------------------------------------------------- 1. products (un-baked)
product_objs = [o for c in ('Products', 'Slots', 'Live props') if c in bpy.data.collections
                for o in bpy.data.collections[c].all_objects]
export_glb(os.path.join(OUT, 'products.glb'), product_objs, export_image_format='WEBP', export_image_quality=85)
shutil.copyfile(os.path.join(HERE, 'slots.json'), os.path.join(OUT, 'slots.json'))

# ---------------------------------------------------------------- 2. environment capture for product lighting
cd = bpy.data.cameras.new('Env probe')
cd.type = 'PANO'
try:
    cd.panorama_type = 'EQUIRECTANGULAR'
except Exception:
    cd.cycles.panorama_type = 'EQUIRECTANGULAR'
probe = bpy.data.objects.new('Env probe', cd)
probe.location = (0.0, 0.9, 1.35)                 # mid-room, a little toward the bench
probe.rotation_euler = (math.pi / 2, 0, 0)        # image centre looks at the back wall
scene.collection.objects.link(probe)
main_cam, main_res = scene.camera, (scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage)
scene.camera = probe
scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 512, 256, 100
scene.cycles.samples = 96
scene.cycles.use_denoising = True
scene.render.image_settings.file_format = 'HDR'
scene.render.filepath = os.path.join(OUT, 'env.hdr')
bpy.ops.render.render(write_still=True)
log('wrote env.hdr')
scene.camera = main_cam
scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = main_res
bpy.data.objects.remove(probe)

# ---------------------------------------------------------------- 3. prepare for baking
# Metals have no diffuse component, so a diffuse-only bake would turn them black.
for m in bpy.data.materials:
    if m.node_tree:
        for n in m.node_tree.nodes:
            if n.bl_idname == 'ShaderNodeBsdfPrincipled':
                n.inputs['Metallic'].default_value = 0.0

tyres = bpy.data.objects.get('Stacked tyres')
if tyres:                                           # supplied model is ~38k faces; plenty at a quarter
    dec = tyres.modifiers.new('Decimate', 'DECIMATE')
    dec.ratio = 0.25

def inside_room(c):
    return abs(c.x) <= HW + 0.01 and -HD - 0.01 <= -c.y <= HD + T + 0.01 and -0.01 <= c.z <= H + 0.01

def hidden_against_room(c):
    """Faces pressed flat against the floor or a wall: never visible, so not worth texture space."""
    return abs(c.x) > HW - 0.004 or c.z < 0.003 or -c.y < -HD + 0.004

def join_group(name, wanted):
    """One mesh per bake group: modifiers applied, world-space, material slots merged."""
    dg = bpy.context.evaluated_depsgraph_get()
    sources = [o for o in scene.objects if o.type in ('MESH', 'CURVE', 'FONT') and not o.name.startswith('baked_') and wanted(o)]
    mats, bm = [], bmesh.new()
    for o in sources:
        tmp = bpy.data.meshes.new_from_object(o.evaluated_get(dg))
        tmp.transform(o.matrix_world)
        if o.matrix_world.determinant() < 0:
            tmp.flip_normals()
        remap = []
        for m in tmp.materials:
            if m not in mats:
                mats.append(m)
            remap.append(mats.index(m))
        idx = [0] * len(tmp.polygons)
        tmp.polygons.foreach_get('material_index', idx)
        tmp.polygons.foreach_set('material_index', [remap[i] if i < len(remap) else 0 for i in idx])
        for l in [l for l in tmp.uv_layers if l.name != 'src']:   # 'src' = UVs an image-textured material needs
            tmp.uv_layers.remove(l)
        bm.from_mesh(tmp)
        bpy.data.meshes.remove(tmp)
    if name in SHELL_GROUPS:                        # drop faces nobody inside can ever see
        bmesh.ops.delete(bm, geom=[f for f in bm.faces if not inside_room(f.calc_center_median())], context='FACES')
    elif name not in ('exterior', 'plants'):
        bmesh.ops.delete(bm, geom=[f for f in bm.faces if hidden_against_room(f.calc_center_median())], context='FACES')
        # backs of panels mounted a couple of centimetres off the back wall (the pegboard)
        bmesh.ops.delete(bm, geom=[f for f in bm.faces if f.normal.y > 0.9 and -f.calc_center_median().y < -HD + 0.03], context='FACES')
    me = bpy.data.meshes.new('baked_' + name)
    bm.to_mesh(me)
    bm.free()
    for m in mats:
        me.materials.append(m)
    ob = bpy.data.objects.new('baked_' + name, me)
    scene.collection.objects.link(ob)
    for o in sources:                               # the joined copy stands in for them from here on
        o.hide_render = True
    return ob

def unwrap(ob):
    layer = ob.data.uv_layers.new(name='bake')
    ob.data.uv_layers.active = layer
    layer.active_render = True
    select_only([ob])
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.0015, area_weight=0.0)
    bpy.ops.object.mode_set(mode='OBJECT')

baked = []
for name, wanted, size in GROUPS:
    ob = join_group(name, wanted)
    log(f'{name}: joined {len(ob.data.polygons)} faces, {len(ob.data.materials)} materials')
    unwrap(ob)
    me = ob.data
    area = sum(p.area for p in me.polygons)
    uvs = me.uv_layers.active.data
    uv_area = 0.0
    for p in me.polygons:
        pts = [uvs[li].uv for li in p.loop_indices]
        uv_area += abs(sum(pts[i].x * pts[(i + 1) % len(pts)].y - pts[(i + 1) % len(pts)].x * pts[i].y for i in range(len(pts)))) / 2
    log(f'{name}: unwrapped  surface {area:.1f} m2, atlas {size}px {uv_area * 100:.0f}% used -> {math.sqrt(uv_area / area) * size:.0f} px per metre')
    img = bpy.data.images.new('bake_' + name, size, size, alpha=False, float_buffer=True)
    for m in ob.data.materials:                     # every material bakes into the same atlas
        node = m.node_tree.nodes.new('ShaderNodeTexImage')
        node.name = 'BAKE_TARGET'
        node.image = img
        m.node_tree.nodes.active = node
    baked.append((name, ob, img))

if '--dry' in ARGS:
    sys.exit(0)

# ---------------------------------------------------------------- 4. bake
scene.cycles.samples = SAMPLES
scene.cycles.use_denoising = True
bk = scene.render.bake
bk.use_pass_direct = bk.use_pass_indirect = bk.use_pass_diffuse = bk.use_pass_emit = True
bk.use_pass_glossy = bk.use_pass_transmission = False
bk.margin = 8
bk.use_clear = True
bk.use_selected_to_active = False
scene.render.image_settings.file_format = 'JPEG'
scene.render.image_settings.quality = 95
for name, ob, img in baked:
    for m in ob.data.materials:                     # the shared BAKE_TARGET nodes point at one image at a time
        m.node_tree.nodes['BAKE_TARGET'].image = img
        m.node_tree.nodes.active = m.node_tree.nodes['BAKE_TARGET']
    select_only([ob])
    log(f'{name}: baking {img.size[0]}px at {SAMPLES} samples...')
    bpy.ops.object.bake(type='COMBINED')
    path = os.path.join(HERE, 'textures', f'baked_{name}.jpg')
    img.save_render(path, scene=scene)              # applies the scene exposure + AgX look
    log(f'{name}: baked -> {os.path.basename(path)}')

# ---------------------------------------------------------------- 5. export the baked room
for name, ob, img in baked:
    tex = bpy.data.images.load(os.path.join(HERE, 'textures', f'baked_{name}.jpg'))
    mat = bpy.data.materials.new('baked_' + name)
    try:
        mat.use_nodes = True
    except Exception:
        pass
    bsdf = next(n for n in mat.node_tree.nodes if n.bl_idname == 'ShaderNodeBsdfPrincipled')
    node = mat.node_tree.nodes.new('ShaderNodeTexImage')
    node.image = tex
    mat.node_tree.links.new(node.outputs['Color'], bsdf.inputs['Base Color'])
    bsdf.inputs['Roughness'].default_value = 1.0
    if 'src' in ob.data.uv_layers:                  # only the bake UVs go to the browser
        ob.data.uv_layers.remove(ob.data.uv_layers['src'])
    ob.data.materials.clear()
    ob.data.materials.append(mat)
    ob.data.polygons.foreach_set('material_index', [0] * len(ob.data.polygons))
export_glb(os.path.join(OUT, 'static.glb'), [ob for _, ob, _ in baked], export_image_format='WEBP', export_image_quality=92)
log('done')
