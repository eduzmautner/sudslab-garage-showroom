"""
Builds the SudsLab garage scene from scratch and saves blender/garage.blend.

    blender -b --factory-startup -P blender/build_garage.py -- [--render]

Layout numbers are written in the web viewer's coordinates (x right, y up,
z toward the door, metres) and converted to Blender's Z-up on the way in, so
this file and src/garage.js stay easy to compare.

Spray bottles are intentionally NOT modelled: each one is an empty named
`slot_<id>` sitting where the bottle's base goes, facing its front (+Z in
viewer terms / -Y local in Blender). All other products are simple stand-ins
parented to their own slot empty.
"""
import bpy, bmesh, glob, json, math, os, random, sys
import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, '3D assets')
TEXTURES = os.path.join(HERE, 'textures')
ARGS = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
PI = math.pi

# ---------------------------------------------------------------- dimensions
W, D, H = 4.8, 6.8, 2.85            # interior width, depth, height
DOOR_W, DOOR_H, DOOR_OPEN = 3.0, 2.35, 0.7716     # open fraction: bottom edge at 1.813 m (was 0.75, raised 2 inches)
HW, HD = W / 2, D / 2
SUN_ELEV, SUN_AZIM = math.radians(15), math.radians(20)   # = Blender sun rotation X 75, Z 20
EYE = 1.5

# ---------------------------------------------------------------- scene reset
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.unit_settings.system = 'METRIC'

COLS = {}
def collection(name):
    if name not in COLS:
        c = bpy.data.collections.new(name)
        scene.collection.children.link(c)
        COLS[name] = c
    return COLS[name]

COL = None
def use(name):
    global COL
    COL = collection(name)

def V(x, y, z):
    """viewer (x, y-up, z-toward-door) -> Blender (x, y, z-up)"""
    return Vector((x, -z, y))

# ---------------------------------------------------------------- materials
def lin(hexstr, a=1.0):
    h = hexstr.lstrip('#')
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    c = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return (*c, a)

def new_mat(name):
    m = bpy.data.materials.new(name)
    try:
        m.use_nodes = True
    except Exception:
        pass
    nt = m.node_tree
    bsdf = next(n for n in nt.nodes if n.bl_idname == 'ShaderNodeBsdfPrincipled')
    return m, nt, bsdf

def plain(name, color, rough=0.5, metal=0.0, coat=0.0, sheen=0.0):
    m, nt, b = new_mat(name)
    b.inputs['Base Color'].default_value = lin(color)
    b.inputs['Roughness'].default_value = rough
    b.inputs['Metallic'].default_value = metal
    if coat:
        b.inputs['Coat Weight'].default_value = coat
    if sheen:
        b.inputs['Sheen Weight'].default_value = sheen
    m.diffuse_color = lin(color)
    return m

def obj_coords(nt, scale=(1, 1, 1)):
    tc = nt.nodes.new('ShaderNodeTexCoord')
    mp = nt.nodes.new('ShaderNodeMapping')
    mp.inputs['Scale'].default_value = scale
    nt.links.new(tc.outputs['Object'], mp.inputs['Vector'])
    return mp.outputs['Vector']

def ramp(nt, fac, stops):
    r = nt.nodes.new('ShaderNodeValToRGB')
    els = r.color_ramp.elements
    while len(els) < len(stops):
        els.new(0.5)
    for e, (pos, col) in zip(els, stops):
        e.position = pos
        e.color = lin(col) if isinstance(col, str) else col
    nt.links.new(fac, r.inputs['Fac'])
    return r.outputs['Color']

def noise(nt, vec, scale, detail=6.0, rough=0.55, distortion=0.0):
    n = nt.nodes.new('ShaderNodeTexNoise')
    n.inputs['Scale'].default_value = scale
    n.inputs['Detail'].default_value = detail
    n.inputs['Roughness'].default_value = rough
    n.inputs['Distortion'].default_value = distortion
    nt.links.new(vec, n.inputs['Vector'])
    return n.outputs['Fac']

def bump(nt, bsdf, height, strength=0.1, distance=0.002):
    b = nt.nodes.new('ShaderNodeBump')
    b.inputs['Strength'].default_value = strength
    b.inputs['Distance'].default_value = distance
    nt.links.new(height, b.inputs['Height'])
    nt.links.new(b.outputs['Normal'], bsdf.inputs['Normal'])
    return b

def mix_color(nt, fac, a, b):
    n = nt.nodes.new('ShaderNodeMix')
    n.data_type = 'RGBA'
    sock = lambda name, typ: next(s for s in n.inputs if s.name == name and s.type == typ)
    for s, v in ((sock('Factor', 'VALUE'), fac), (sock('A', 'RGBA'), a), (sock('B', 'RGBA'), b)):
        if hasattr(v, 'is_output'):
            nt.links.new(v, s)
        else:
            s.default_value = v
    return next(s for s in n.outputs if s.type == 'RGBA')

def concrete(name, dark, light, rough=(0.38, 0.6), blotch=1.1):
    m, nt, b = new_mat(name)
    vec = obj_coords(nt)
    big = noise(nt, vec, blotch, 9, 0.6, 0.4)
    fine = noise(nt, vec, 140, 3, 0.7)
    nt.links.new(ramp(nt, big, [(0.3, dark), (0.72, light)]), b.inputs['Base Color'])
    r = nt.nodes.new('ShaderNodeMapRange')
    r.inputs['To Min'].default_value, r.inputs['To Max'].default_value = rough
    nt.links.new(big, r.inputs['Value'])
    nt.links.new(r.outputs['Result'], b.inputs['Roughness'])
    bump(nt, b, fine, 0.12, 0.001)
    m.diffuse_color = lin(light)
    return m

def wood(name, dark, light, grain='x', rough=0.55, density=9.0):
    """Stretched-noise wood. grain = Blender axis the fibres run along."""
    m, nt, b = new_mat(name)
    s = {'x': (0.5, density, density), 'y': (density, 0.5, density), 'z': (density, density, 0.5)}[grain]
    vec = obj_coords(nt, s)
    fibres = noise(nt, vec, 6, 8, 0.62, 1.2)
    streak = noise(nt, vec, 38, 2, 0.5)
    col = ramp(nt, fibres, [(0.28, dark), (0.5, light), (0.74, dark)])
    nt.links.new(col, b.inputs['Base Color'])
    b.inputs['Roughness'].default_value = rough
    bump(nt, b, streak, 0.08, 0.0008)
    m.diffuse_color = lin(light)
    return m

def pegboard_mat(name):
    """Plywood with a 50 mm grid of 9 mm holes, done in the shader."""
    m, nt, b = new_mat(name)
    vec = obj_coords(nt, (0.5, 9, 9))
    fibres = noise(nt, vec, 6, 8, 0.62, 1.2)
    ply = ramp(nt, fibres, [(0.25, '#b98a4e'), (0.5, '#d9ad72'), (0.76, '#bd8f54')])
    tc = nt.nodes.new('ShaderNodeTexCoord')
    def vmath(op, a, bval=None):
        n = nt.nodes.new('ShaderNodeVectorMath')
        n.operation = op
        nt.links.new(a, n.inputs[0])
        if bval is not None:
            (n.inputs['Scale'] if op == 'SCALE' else n.inputs[1]).default_value = bval
        return n
    cell = vmath('SUBTRACT', vmath('FRACTION', vmath('SCALE', tc.outputs['Object'], 20.0).outputs['Vector']).outputs['Vector'], (0.5, 0.5, 0.5))
    flat = vmath('MULTIPLY', cell.outputs['Vector'], (1.0, 0.0, 1.0))   # board is thin along local Y
    dist = vmath('LENGTH', flat.outputs['Vector']).outputs['Value']
    hole = ramp(nt, dist, [(0.085, (1, 1, 1, 1)), (0.1, (0, 0, 0, 1))])  # 1 inside a hole
    nt.links.new(mix_color(nt, hole, ply, lin('#1c1108')), b.inputs['Base Color'])
    b.inputs['Roughness'].default_value = 0.62
    inv = nt.nodes.new('ShaderNodeInvert')
    nt.links.new(hole, inv.inputs['Color'])
    bump(nt, b, inv.outputs['Color'], 0.6, 0.004)
    m.diffuse_color = lin('#d2a468')
    return m

def emission(name, color, strength):
    m, nt, b = new_mat(name)
    b.inputs['Base Color'].default_value = lin(color)
    b.inputs['Emission Color'].default_value = lin(color)
    b.inputs['Emission Strength'].default_value = strength
    return m

M = dict(
    floor=concrete('Floor concrete', '#6f6d69', '#9a9792', (0.3, 0.55)),
    drive=concrete('Driveway concrete', '#a9a59b', '#c9c4b8', (0.7, 0.9), 0.7),
    wall=concrete('Wall paint', '#ded7ca', '#ece6db', (0.8, 0.92), 0.5),
    ceiling=wood('Ceiling boards', '#9a6e3c', '#c39760', 'y', 0.7),
    joist=wood('Joist', '#855a2b', '#b0824a', 'x', 0.7),
    bench_top=wood('Bench top', '#a8743a', '#d6a463', 'x', 0.45),
    bench_leg=plain('Bench frame metal', '#3b3e43', 0.5),
    shelf=wood('Shelf wood', '#a06f38', '#cf9c5e', 'y', 0.5),
    peg=pegboard_mat('Pegboard'),
    steel=plain('Steel', '#a4a6a8', 0.35, 1.0),
    dark_steel=plain('Dark steel', '#2b2d30', 0.45, 0.8),
    slat=plain('Door slat', '#c4c7c9', 0.4, 0.9),
    black=plain('Black plastic', '#161616', 0.5),
    black_gloss=plain('Black gloss', '#101010', 0.25, 0, 0.5),
    rubber=plain('Rubber', '#191919', 0.85),
    orange=plain('Orange plastic', '#ff5b00', 0.4, 0, 0.3),
    orange_cloth=plain('Orange microfiber', '#f2691c', 0.95, 0, 0, 0.8),
    grey_cloth=plain('Grey microfiber', '#8d8f93', 0.95, 0, 0, 0.8),
    cabinet=plain('Cabinet paint', '#3d4a44', 0.42, 0.3, 0.3),
    cabinet_front=plain('Cabinet front', '#46564e', 0.4, 0.3, 0.3),
    chest=plain('Tool chest', '#1f2124', 0.35, 0.5, 0.4),
    chest_front=plain('Tool chest drawer', '#2c2f33', 0.35, 0.5, 0.4),
    brass=plain('Brass', '#b08a3c', 0.3, 1.0),
    bristle=plain('Bristles', '#d8c7a0', 0.9, 0, 0, 0.5),
    white=plain('White plastic', '#e9e6de', 0.35, 0, 0.3),
    cardboard=plain('Cardboard', '#b08a5c', 0.9),
    red=plain('Red paint', '#c62a1e', 0.35, 0, 0.5),
    sign=plain('Sign board', '#141414', 0.5),
    sign_white=plain('Sign white', '#f2f2f2', 0.5),
    alu=plain('Aluminium', '#c9cbcd', 0.3, 1.0),
    grass=plain('Grass', '#587a36', 0.95),
    hedge=plain('Hedge', '#3f6130', 0.95),
    fence=plain('Fence', '#efe9dc', 0.8),
    bulb=emission('Bulb', '#ffd9a0', 12.0),
    tube=emission('LED tube (off)', '#f4f6f8', 0.0),
)

# ---------------------------------------------------------------- mesh helpers
def finish(name, bm, mat, parent, pos, rot=(0, 0, 0), bevel=0.0, smooth=None):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    if smooth is not None:
        for p in me.polygons:
            p.use_smooth = smooth(p)
    ob = bpy.data.objects.new(name, me)
    if mat is not None:
        me.materials.append(mat)
    ob.location = V(*pos)
    ob.rotation_euler = rot
    ob.parent = parent
    COL.objects.link(ob)
    if bevel > 0:
        mod = ob.modifiers.new('Bevel', 'BEVEL')
        mod.width = bevel
        mod.segments = 2
        mod.limit_method = 'ANGLE'
        mod.angle_limit = math.radians(40)
        mod.harden_normals = True
    return ob

def box(name, w, h, d, mat, parent, x, y, z, ry=0.0, bevel=0.004, rot=None):
    """w along x, h up, d along viewer-z."""
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(w, d, h), verts=bm.verts)
    return finish(name, bm, mat, parent, (x, y, z), rot or (0, 0, ry), min(bevel, 0.45 * min(w, h, d)))

AXIS_ROT = {'y': (0, 0, 0), 'z': (PI / 2, 0, 0), 'x': (0, -PI / 2, 0)}   # viewer axis the top points along (z -> +z, x -> -x)

def cyl(name, r_top, r_bot, h, mat, parent, x, y, z, axis='y', seg=32, bevel=0.0015, rot=None):
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=seg, radius1=r_bot, radius2=r_top, depth=h)
    return finish(name, bm, mat, parent, (x, y, z), rot or AXIS_ROT[axis], bevel, smooth=lambda p: abs(p.normal.z) < 0.9)

def torus(name, R, r, mat, parent, x, y, z, axis='y', arc=2 * PI, seg=48, rseg=14, rot=None):
    """Ring around the given viewer axis. arc < 2pi leaves it open."""
    closed = arc >= 2 * PI - 1e-6
    n = seg if closed else seg + 1
    verts, faces = [], []
    for i in range(n):
        a = arc * i / seg
        for j in range(rseg):
            b = 2 * PI * j / rseg
            rr = R + r * math.cos(b)
            verts.append((rr * math.cos(a), rr * math.sin(a), r * math.sin(b)))
    for i in range(seg if closed else seg):
        i2 = (i + 1) % n
        if not closed and i + 1 >= n:
            break
        for j in range(rseg):
            j2 = (j + 1) % rseg
            faces.append((i * rseg + j, i2 * rseg + j, i2 * rseg + j2, i * rseg + j2))
    bm = bmesh.new()
    vs = [bm.verts.new(v) for v in verts]
    for f in faces:
        bm.faces.new([vs[k] for k in f])
    return finish(name, bm, mat, parent, (x, y, z), rot or AXIS_ROT[axis], smooth=lambda p: True)

def sphere(name, r, mat, parent, x, y, z, scale=(1, 1, 1), ico=False):
    bm = bmesh.new()
    if ico:
        bmesh.ops.create_icosphere(bm, subdivisions=3, radius=r)
    else:
        bmesh.ops.create_uvsphere(bm, u_segments=24, v_segments=14, radius=r)
    ob = finish(name, bm, mat, parent, (x, y, z), smooth=lambda p: True)
    ob.scale = scale
    return ob

def empty(name, parent, x, y, z, ry=0.0, display='PLAIN_AXES', size=0.05):
    e = bpy.data.objects.new(name, None)
    e.empty_display_type = display
    e.empty_display_size = size
    e.location = V(x, y, z)
    e.rotation_euler = (0, 0, ry)
    e.parent = parent
    COL.objects.link(e)
    return e

SLOTS = []
def slot(slot_id, kind, parent, x, y, z, ry=0.0, hotspot_dy=0.34):
    """Product anchor. Origin = product base centre; kind says what goes there."""
    prev = COL
    use('Slots')
    e = empty('slot_' + slot_id, parent, x, y, z, ry, 'ARROWS', 0.09)
    e['slot'] = slot_id
    e['kind'] = kind
    e['hotspot_dy'] = hotspot_dy
    globals()['COL'] = prev
    SLOTS.append(e)
    return e

# ---------------------------------------------------------------- shell
use('Shell')
T = 0.2
box('Floor', W, 0.2, D + T, M['floor'], None, 0, -0.1, T / 2, bevel=0)
for i, z in enumerate((-1.13, 1.13)):                       # saw-cut control joints
    box(f'Floor joint {i}', W, 0.004, 0.008, M['dark_steel'], None, 0, 0.0005, z, bevel=0)
box('Floor joint long', 0.008, 0.004, D, M['dark_steel'], None, 0, 0.0005, 0, bevel=0)
box('Wall left', T, H, D + 2 * T, M['wall'], None, -HW - T / 2, H / 2, 0, bevel=0)
box('Wall right', T, H, D + 2 * T, M['wall'], None, HW + T / 2, H / 2, 0, bevel=0)
box('Wall back', W, H, T, M['wall'], None, 0, H / 2, -HD - T / 2, bevel=0)
pier = HW - DOOR_W / 2
box('Wall front L', pier, H, T, M['wall'], None, -HW + pier / 2, H / 2, HD + T / 2, bevel=0)
box('Wall front R', pier, H, T, M['wall'], None, HW - pier / 2, H / 2, HD + T / 2, bevel=0)
box('Door header', DOOR_W, H - DOOR_H, T, M['wall'], None, 0, DOOR_H + (H - DOOR_H) / 2, HD + T / 2, bevel=0)
box('Ceiling', W + 1.0, 0.2, D + 1.2, M['ceiling'], None, 0, H + 0.1, 0, bevel=0)
z = -HD + 0.4
i = 0
while z < HD:
    box(f'Joist {i:02d}', W, 0.16, 0.05, M['joist'], None, 0, H - 0.08, z, bevel=0.003)
    z += 0.62
    i += 1
box('Cove left', 0.012, 0.1, D, M['rubber'], None, -HW + 0.006, 0.05, 0, bevel=0.002)
box('Cove right', 0.012, 0.1, D, M['rubber'], None, HW - 0.006, 0.05, 0, bevel=0.002)
box('Cove back', W, 0.1, 0.012, M['rubber'], None, 0, 0.05, -HD + 0.006, bevel=0.002)
cyl('Floor drain', 0.07, 0.07, 0.006, M['steel'], None, 0.2, 0.003, -0.3, seg=24)

# roller door, 75 % open
door_bottom = DOOR_H * DOOR_OPEN
slat_h = 0.085
y = door_bottom + slat_h / 2
i = 0
while y < DOOR_H + 0.05:
    s = cyl(f'Door slat {i:02d}', slat_h * 0.62, slat_h * 0.62, DOOR_W + 0.06, M['slat'], None, 0, y, HD + 0.1, axis='x', seg=12, bevel=0)
    s.scale = (1, 0.28, 1)       # flattened tube = curved slat profile
    y += slat_h
    i += 1
box('Door bottom bar', DOOR_W + 0.06, 0.06, 0.05, M['dark_steel'], None, 0, door_bottom - 0.03, HD + 0.1)
box('Door seal', DOOR_W + 0.06, 0.02, 0.035, M['rubber'], None, 0, door_bottom - 0.07, HD + 0.1, bevel=0.006)
for sx, n in ((-1, 'L'), (1, 'R')):
    box(f'Door guide {n}', 0.06, DOOR_H, 0.09, M['steel'], None, sx * (DOOR_W / 2 + 0.03), DOOR_H / 2, HD - 0.02)
box('Door drum housing', DOOR_W + 0.3, 0.32, 0.32, M['steel'], None, 0, DOOR_H + 0.19, HD - 0.16, bevel=0.01)
box('Door motor', 0.16, 0.2, 0.22, M['dark_steel'], None, DOOR_W / 2 + 0.26, DOOR_H + 0.19, HD - 0.16, bevel=0.01)

# electrics: conduit run, outlets, switch, two LED battens (off - it is daytime)
box('Conduit back', 2.2, 0.02, 0.02, M['alu'], None, 1.3, 1.25, -HD + 0.012, bevel=0.008)
box('Conduit drop', 0.02, 1.6, 0.02, M['alu'], None, 2.38, 2.05, -HD + 0.012, bevel=0.008)
for k, (x, y_, z_) in enumerate(((1.45, 1.25, -HD + 0.02), (HW - 0.02, 1.25, 1.2), (-HW + 0.02, 0.45, 0.2))):
    side = abs(x) > HW - 0.1
    box(f'Outlet box {k}', 0.03 if side else 0.08, 0.12, 0.08 if side else 0.03, M['alu'], None, x, y_, z_, bevel=0.004)
box('Light switch', 0.03, 0.12, 0.08, M['white'], None, HW - 0.02, 1.3, HD - 0.5, bevel=0.004)
for k, z_ in enumerate((-0.6, 1.5)):
    box(f'LED batten {k}', 1.2, 0.05, 0.1, M['white'], None, 0, H - 0.19, z_, bevel=0.008)
    box(f'LED tube {k}', 1.16, 0.012, 0.07, M['tube'], None, 0, H - 0.221, z_, bevel=0.004)

# ---------------------------------------------------------------- outside
use('Exterior')
# Driveway: straight out of the door, then a sweeping turn toward +x and off the lot.
def road():
    pts = [(3.0, 0.2), (-3.0, 0.2), (-3.0, 4.7)]
    pts += [(4 - 7 * math.cos(t), 4.7 + 7 * math.sin(t)) for t in (PI / 2 * k / 16 for k in range(1, 17))]   # outer edge
    pts += [(25.0, 11.7), (25.0, 7.0), (5.5, 7.0)]
    pts += [(5.5 - 2.5 * math.sin(t), 4.5 + 2.5 * math.cos(t)) for t in (PI / 2 * k / 10 for k in range(1, 11))]  # inner edge
    bm = bmesh.new()
    f = bm.faces.new([bm.verts.new((x, -(HD + z), -0.015)) for x, z in pts])
    f.normal_update()
    if f.normal.z < 0:
        f.normal_flip()
    bmesh.ops.triangulate(bm, faces=[f])
    return finish('Driveway', bm, M['drive'], None, (0, 0, 0))
road()
box('Lawn', 50, 0.2, 30, M['grass'], None, 0, -0.13, HD + 13, bevel=0)
# Fence: along the back of the lot, turning 90 degrees at x = FENCE_X to run back past the garage.
FENCE_X = -11.0
box('Fence', 22 - FENCE_X, 1.25, 0.06, M['fence'], None, (22 + FENCE_X) / 2, 0.62, HD + 15, bevel=0.004)
box('Fence side', 0.06, 1.25, 17.0, M['fence'], None, FENCE_X, 0.62, HD + 6.5, bevel=0.004)
x = 21.0
i = 0
while x > FENCE_X + 0.5:
    box(f'Fence post {i:02d}', 0.12, 1.4, 0.12, M['fence'], None, x, 0.7, HD + 14.93, bevel=0.006)
    x -= 2.2
    i += 1
z_ = 14.93
while z_ > -2:
    box(f'Fence post {i:02d}', 0.12, 1.4, 0.12, M['fence'], None, FENCE_X + 0.07, 0.7, HD + z_, bevel=0.006)
    z_ -= 2.2
    i += 1
# Low-poly trees and shrubs beyond the fence: faceted, flat-shaded, a few greens.
rnd = random.Random(7)
LEAF = [plain(f'Leaf {n}', c, 0.9) for n, c in enumerate(('#4f7a34', '#628f3c', '#3f6a33', '#7a9f45'))]
PINE = [plain(f'Pine {n}', c, 0.9) for n, c in enumerate(('#2f5a3a', '#3a6b44'))]
TRUNK = plain('Trunk', '#6b4a2f', 0.9)

def flat(ob):
    ob.data.polygons.foreach_set('use_smooth', [False] * len(ob.data.polygons))
    return ob

def faceted_blob(name, r, mat, parent, x, y, z, squash=0.85, jitter=0.18):
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=1, radius=r)
    for v in bm.verts:
        v.co *= 1 + rnd.uniform(-jitter, jitter)
        v.co.z *= squash
    ob = finish(name, bm, mat, parent, (x, y, z))        # flat shaded on purpose
    ob.rotation_euler = (0, 0, rnd.uniform(0, PI))
    return ob

def leafy_tree(i, x, z, h):
    t = empty(f'Tree {i:02d}', None, x, 0, z, rnd.uniform(0, PI))
    trunk_h = h * 0.3
    flat(cyl(f'Tree {i:02d} trunk', h * 0.035, h * 0.06, trunk_h, TRUNK, t, 0, trunk_h / 2, 0, seg=6, bevel=0))
    r = h * 0.34
    faceted_blob(f'Tree {i:02d} crown', r, rnd.choice(LEAF), t, 0, trunk_h + r * 0.55, 0)
    for k in range(rnd.randint(2, 3)):
        a = rnd.uniform(0, 2 * PI)
        faceted_blob(f'Tree {i:02d} clump {k}', r * rnd.uniform(0.5, 0.7), rnd.choice(LEAF), t,
                     math.cos(a) * r * 0.65, trunk_h + r * rnd.uniform(0.1, 0.75), math.sin(a) * r * 0.65)

def pine_tree(i, x, z, h):
    t = empty(f'Tree {i:02d}', None, x, 0, z, rnd.uniform(0, PI))
    flat(cyl(f'Tree {i:02d} trunk', h * 0.025, h * 0.04, h * 0.25, TRUNK, t, 0, h * 0.125, 0, seg=6, bevel=0))
    mat = rnd.choice(PINE)
    tiers = 4
    for k in range(tiers):
        f = k / tiers
        th = h * 0.34
        c = flat(cyl(f'Tree {i:02d} tier {k}', 0.0, h * 0.24 * (1 - f * 0.62), th, mat, t, 0, h * 0.16 + f * h * 0.66 + th / 2, 0, seg=7, bevel=0))
        c.rotation_euler = (0, 0, rnd.uniform(0, PI))

TREES = [  # kind, x, z beyond the door wall, height
    ('leaf', -15.5, 18.5, 6.2), ('pine', -11.5, 21.0, 7.5), ('leaf', -8.0, 18.0, 5.2), ('pine', -5.2, 22.5, 8.5),
    ('leaf', -2.6, 19.0, 6.6), ('pine', 0.4, 23.0, 7.8), ('leaf', 3.4, 18.2, 5.6), ('leaf', 6.8, 20.5, 7.0),
    ('pine', 9.6, 18.4, 6.4), ('leaf', 12.8, 21.5, 6.8), ('pine', 16.0, 19.0, 7.2),
]
for i, (kind, x, z_, h) in enumerate(TREES):
    (leafy_tree if kind == 'leaf' else pine_tree)(i, x, HD + z_, h * 0.55)   # kept short so the crowns show under the door
for i, x in enumerate((-9.5, -6.4, -3.3, -0.9, 2.2, 5.0, 8.3)):       # shrubs along the fence
    faceted_blob(f'Shrub {i}', rnd.uniform(0.55, 0.85), rnd.choice(LEAF), None, x + rnd.uniform(-0.4, 0.4), 0.45, HD + 14.0 + rnd.uniform(-0.3, 0.3), squash=0.75)

# More planting: trees behind the fence and filling the lot beyond its side run, bushes on the lawn.
TREES2 = [  # kind, x, z beyond the door wall, height
    ('pine', 11.5, 25.8, 8.0), ('pine', 4.9, 24.6, 8.6), ('leaf', 12.4, 17.6, 6.0), ('pine', 0.7, 16.8, 6.8), ('leaf', -4.8, 16.8, 6.2),
    ('pine', -12.8, 12.6, 8.0), ('pine', -18.4, 13.0, 8.8), ('leaf', -16.5, 11.0, 6.4), ('pine', -12.8, 7.8, 7.4),
    ('pine', -17.5, 7.2, 8.4), ('leaf', -21.2, 8.0, 7.0), ('leaf', -13.3, 5.6, 6.0), ('pine', -21.2, 3.8, 8.0),
    ('pine', -13.1, 1.9, 7.6), ('leaf', -16.9, 2.4, 6.6),
]
for i, (kind, x, z_, h) in enumerate(TREES2, len(TREES)):
    (leafy_tree if kind == 'leaf' else pine_tree)(i, x, HD + z_, h * 0.55)
BUSHES = [  # x, z beyond the door wall, radius
    (12.1, 13.3, 0.7), (-7.7, 12.6, 0.8), (-9.4, 10.0, 0.75), (-10.0, 8.7, 0.45), (-5.9, 8.1, 0.8),
    (-8.6, 6.9, 0.7), (-9.4, 4.8, 0.8), (-9.6, 1.4, 0.8), (5.0, 3.6, 0.85),
]
for i, (x, z_, r) in enumerate(BUSHES):
    faceted_blob(f'Bush {i}', r, rnd.choice(LEAF), None, x, r * 0.55, HD + z_, squash=0.75)
    a = rnd.uniform(0, 2 * PI)
    faceted_blob(f'Bush {i} clump', r * 0.6, rnd.choice(LEAF), None, x + math.cos(a) * r * 0.8, r * 0.35, HD + z_ + math.sin(a) * r * 0.8, squash=0.75)

# ---------------------------------------------------------------- product stand-ins (non-bottle)
# Microfiber towels. They are lit live in the browser, so the soft look cannot come from a
# Blender shader: it comes from the shape (a rounded, slightly uneven pad rather than a slab)
# and two small tiling images drawn once here - a fuzzy bump and a light/dark mottle.
def tile_noise(n, cells, blur, seed):
    """Seamless grey noise, n x n: random cells enlarged and blurred with wrap-around."""
    rng = np.random.default_rng(seed)
    a = np.kron(rng.random((cells, cells)), np.ones((n // cells, n // cells))).astype(np.float32)
    step = max(1, n // cells)           # start as wide as a cell so the cell edges melt away
    while step >= 1:
        for _ in range(blur):
            a = (a + np.roll(a, step, 0) + np.roll(a, -step, 0) + np.roll(a, step, 1) + np.roll(a, -step, 1)) / 5
        step //= 2
    a -= a.min()
    return a / a.max()

TOWEL_TILE = 0.08                    # metres of cloth covered by one repeat of the images
def towel_image(name, rgb_rows, data=False):
    path = os.path.join(TEXTURES, name + '.png')
    if not os.path.exists(path):
        os.makedirs(TEXTURES, exist_ok=True)
        n = rgb_rows.shape[0]
        px = np.ones((n, n, 4), np.float32)
        px[..., :3] = rgb_rows
        img = bpy.data.images.new(name, n, n, alpha=False, is_data=data)
        img.pixels.foreach_set(px.ravel())
        img.filepath_raw = path
        img.file_format = 'PNG'
        img.save()
        bpy.data.images.remove(img)
    img = bpy.data.images.load(path, check_existing=True)
    if data:
        img.colorspace_settings.name = 'Non-Color'
    img.pack()
    return img

def towel_mat(name, color):
    n = 512
    fuzz = tile_noise(n, 256, 1, 1)             # ~1 mm pile
    lumps = tile_noise(n, 16, 3, 2)             # ~5 mm unevenness
    height = 0.55 * fuzz + 0.45 * lumps
    dx = (np.roll(height, -1, 1) - np.roll(height, 1, 1)) * 6.0
    dy = (np.roll(height, -1, 0) - np.roll(height, 1, 0)) * 6.0
    nrm = np.stack([-dx, -dy, np.ones_like(dx)], -1)
    nrm /= np.linalg.norm(nrm, axis=-1, keepdims=True)
    h = color.lstrip('#')
    srgb = np.array([int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)], np.float32)
    shade = 0.91 + 0.09 * (0.6 * lumps + 0.4 * fuzz)
    m, nt, b = new_mat(name)
    col = nt.nodes.new('ShaderNodeTexImage')
    col.image = towel_image('towel_' + h, srgb[None, None, :] * shade[..., None])
    nt.links.new(col.outputs['Color'], b.inputs['Base Color'])
    bump_tex = nt.nodes.new('ShaderNodeTexImage')
    bump_tex.image = towel_image('towel_pile_normal', nrm * 0.5 + 0.5, data=True)
    nm = nt.nodes.new('ShaderNodeNormalMap')
    nm.inputs['Strength'].default_value = 1.0
    nt.links.new(bump_tex.outputs['Color'], nm.inputs['Color'])
    nt.links.new(nm.outputs['Normal'], b.inputs['Normal'])
    b.inputs['Roughness'].default_value = 0.95
    # a light sheen in the cloth's own colour: a strong white one greys the towel out
    b.inputs['Sheen Weight'].default_value = 0.35
    b.inputs['Sheen Tint'].default_value = lin(color)
    # The bench is dimmer for live-lit objects than for the baked ones around them, which left
    # the towels looking rusty. A faint glow in their own colour lifts them back to a lively
    # orange; set by eye in the browser (0.13 already flattens the shading).
    b.inputs['Emission Color'].default_value = lin(color)
    b.inputs['Emission Strength'].default_value = 0.07
    m.diffuse_color = lin(color)
    return m

def towel(name, e, mat, w, d, t, x, y, z, ry=0.0, corner=0.016, seed=0):
    """One folded towel, w x d and t thick, its underside at height y. Built as rings running
    in from a fully rounded edge, so there is no hard corner anywhere, and the flat of the
    pad is nudged up and down by under a millimetre so it does not read as a rigid board."""
    from mathutils import noise
    a, b_, h = w / 2, d / 2, t / 2
    N = 120
    def outline(inset):
        aa, bb = a - inset, b_ - inset
        r = min(max(corner - inset, 0.0008), aa, bb)
        pts = []
        for k in range(N):
            c, s_ = math.cos(2 * PI * k / N), math.sin(2 * PI * k / N)
            tt = min(aa / max(abs(c), 1e-9), bb / max(abs(s_), 1e-9))
            px, pz = tt * c, tt * s_
            if abs(px) > aa - r and abs(pz) > bb - r:                       # in a rounded corner
                cx, cz = math.copysign(aa - r, c), math.copysign(bb - r, s_)
                uc = c * cx + s_ * cz
                tt = uc + math.sqrt(max(uc * uc - (cx * cx + cz * cz) + r * r, 0.0))
                px, pz = tt * c, tt * s_
            pts.append((px, pz))
        return pts
    rings = [(h * (1 - math.cos(f * PI / 2)), h * math.sin(f * PI / 2)) for f in (0, 0.25, 0.5, 0.75, 1.0)]   # the rounded edge
    inset, last = h, min(a, b_) - 0.004
    while inset + 0.012 < last:
        inset += 0.012
        rings.append((inset, h))
    rings.append((last, h))
    bm = bmesh.new()
    uv = bm.loops.layers.uv.new()
    def layer(sign):
        out = []
        for i, (ins, up) in enumerate(rings):
            if sign < 0 and i == 0:
                out.append(top[0])                       # both halves share the rim
                continue
            ring = []
            for px, pz in outline(ins):
                wob = noise.noise(Vector((px * 35 + seed * 7.3, pz * 35, seed * 3.1 + sign))) * 0.0007 * min(1.0, ins / h)
                ring.append(bm.verts.new(V(px, h + sign * up + wob, pz)))
            out.append(ring)
        return out
    top = layer(1)
    bottom = layer(-1)
    for half in (top, bottom):
        for r0, r1 in zip(half, half[1:]):
            for k in range(N):
                bm.faces.new((r0[k], r0[(k + 1) % N], r1[(k + 1) % N], r1[k]))
        bm.faces.new(half[-1])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    for f in bm.faces:
        for loop in f.loops:
            co = loop.vert.co                            # Blender x, y = viewer x, -z
            loop[uv].uv = (co.x / TOWEL_TILE, co.y / TOWEL_TILE)
    return finish(name, bm, mat, e, (x, y, z), rot=(0, 0, ry), smooth=lambda p: True)

def towels(e):
    """Three folded towels, about an inch tall together."""
    orange, grey = '#ff7a26', '#a6a8ad'
    mats = {c: towel_mat(n, c) for n, c in (('Towel orange', orange), ('Towel grey', grey))}
    t = 0.0085
    for i, (color, w, d) in enumerate(((orange, 0.300, 0.210), (grey, 0.296, 0.206), (orange, 0.292, 0.203))):
        towel(f'Towel {i}', e, mats[color], w, d, t, (i - 1) * 0.005, i * t, (1 - i) * 0.003, ry=(i - 1) * 0.045, seed=i + 1)

def detail_brush(e, s=1.0):
    cyl('Brush handle', 0.011 * s, 0.014 * s, 0.14 * s, M['black_gloss'], e, 0, -0.07 * s, 0, seg=16)
    cyl('Brush band', 0.015 * s, 0.015 * s, 0.018 * s, M['orange'], e, 0, -0.135 * s, 0, seg=16)
    cyl('Brush ferrule', 0.016 * s, 0.016 * s, 0.03 * s, M['steel'], e, 0, -0.158 * s, 0, seg=16)
    cyl('Brush bristles', 0.017 * s, 0.024 * s, 0.06 * s, M['bristle'], e, 0, -0.203 * s, 0, seg=16)

# Bristles and microfiber "noodles" are real strands, not a texture: these products are lit
# live in the browser, where Blender's procedural shaders don't exist, and it is the ragged
# outline that makes a brush read as a brush. Colour is stored per strand on the mesh
# (darker at the root, which fakes the shadow inside the pile), so a head is one material.
FIBRE_RNG = random.Random(7)         # own generator: must not shift the random trees and bushes
def fibre_mat():
    if 'Fibre' in bpy.data.materials:
        return bpy.data.materials['Fibre']
    m, nt, b = new_mat('Fibre')
    vc = nt.nodes.new('ShaderNodeVertexColor')
    vc.layer_name = 'Col'
    nt.links.new(vc.outputs['Color'], b.inputs['Base Color'])
    b.inputs['Roughness'].default_value = 0.85
    return m

def fibres(name, strands, parent, sides=3, root=0.35):
    """One mesh of tapered strands. strands: (base, tip, r_base, r_tip, '#colour'), viewer coords
    relative to the parent. Each strand ends in a point so it is closed from every angle."""
    verts, faces, cols = [], [], []
    for a, b, r0, r1, col in strands:
        a, b = Vector(V(*a)), Vector(V(*b))
        ax = (b - a).normalized()
        u = ax.orthogonal().normalized()
        w = ax.cross(u)
        rgb = [c * FIBRE_RNG.uniform(0.82, 1.08) for c in lin(col)[:3]]
        i0 = len(verts)
        for p, r, shade in ((a, r0, root), (b, r1, 1.0)):
            for k in range(sides):
                ang = 2 * PI * k / sides
                verts.append(p + (u * math.cos(ang) + w * math.sin(ang)) * r)
                cols.append((rgb[0] * shade, rgb[1] * shade, rgb[2] * shade, 1.0))
        verts.append(b + ax * r1 * 1.2)
        cols.append((*rgb, 1.0))
        for k in range(sides):
            k2 = (k + 1) % sides
            faces.append((i0 + k, i0 + k2, i0 + sides + k2, i0 + sides + k))
            faces.append((i0 + sides + k, i0 + sides + k2, i0 + 2 * sides))
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(v) for v in verts], [], faces)
    attr = me.color_attributes.new('Col', 'FLOAT_COLOR', 'POINT')
    for i, c in enumerate(cols):
        attr.data[i].color = c
    me.color_attributes.active_color = attr
    me.color_attributes.render_color_index = 0
    for p in me.polygons:
        p.use_smooth = True
    me.materials.append(fibre_mat())
    ob = bpy.data.objects.new(name, me)
    ob.parent = parent
    COL.objects.link(ob)
    return ob

def radial_head(y0, y1, radius, core, n, r0, r1, palette):
    """Strands radiating from a core that runs down the hanging brush from y0 to y1 (the free
    end). radius(t) is the outline, t = 0..1 along the head; the last strands fan round the end."""
    rnd, out = FIBRE_RNG, []
    codes = [c for c, share in palette for _ in range(int(share * 100))]
    for i in range(n):
        t = (i + rnd.random()) / n
        ang = i * 2.39996 + rnd.uniform(-0.3, 0.3)                    # golden angle: even coverage
        lean = math.radians(-12 + 30 * t if t < 0.86 else 18 + 68 * (t - 0.86) / 0.14)
        lean += rnd.uniform(-0.16, 0.16)
        ang2 = ang + rnd.uniform(-0.18, 0.18)
        d = (math.cos(ang2) * math.cos(lean), -math.sin(lean), math.sin(ang2) * math.cos(lean))
        y = y0 + (y1 - y0) * min(t, 0.97)
        reach = radius(t) * rnd.uniform(0.86, 1.07)
        base = (math.cos(ang) * core * 0.6, y, math.sin(ang) * core * 0.6)
        out.append((base, (d[0] * reach, y + d[1] * reach, d[2] * reach), r0, r1, rnd.choice(codes)))
    return out

def wheel_brush(e, fluffy):
    if fluffy:      # microfiber: fat chenille noodles, orange flecked with black, foam grip
        cyl('MF brush grip', 0.016, 0.016, 0.12, M['rubber'], e, 0, -0.06, 0, seg=16, bevel=0.004)
        cyl('MF brush stem', 0.0045, 0.0045, 0.06, M['black'], e, 0, -0.145, 0, seg=10, bevel=0)
        cyl('MF brush guard', 0.014, 0.036, 0.016, M['black'], e, 0, -0.166, 0, seg=24)
        cyl('MF brush core', 0.03, 0.03, 0.2, plain('MF brush core', '#8a3a08', 1.0), e, 0, -0.285, 0, seg=14, bevel=0.012)
        fibres('MF brush noodles', radial_head(
            -0.18, -0.4, lambda t: 0.045 + 0.008 * math.sin(PI * t), 0.03, 460, 0.0075, 0.0066,
            [('#f47a12', 0.62), ('#e0600a', 0.2), ('#1a1512', 0.18)]), e, sides=5, root=0.5)
    else:           # wheel brush: stiff rust and black bristles round a tapered head
        cyl('Wheel brush grip', 0.016, 0.011, 0.11, M['black_gloss'], e, 0, -0.055, 0, seg=16, bevel=0.004)
        cyl('Wheel brush stem', 0.004, 0.004, 0.07, M['black'], e, 0, -0.142, 0, seg=10, bevel=0)
        cyl('Wheel brush guard', 0.012, 0.031, 0.02, plain('Guard grey', '#9a9c9f', 0.5), e, 0, -0.174, 0, seg=24)
        cyl('Wheel brush core', 0.026, 0.016, 0.21, plain('Wheel brush core', '#3a1c08', 1.0), e, 0, -0.293, 0, seg=14, bevel=0.008)
        fibres('Wheel brush bristles', radial_head(
            -0.19, -0.4, lambda t: 0.047 - 0.017 * t, 0.02, 1100, 0.0026, 0.0007,
            [('#c9651b', 0.5), ('#93470f', 0.22), ('#1c1410', 0.28)]), e)

def tire_brush(e):
    """Black paddle with a fan of long orange bristles facing the room."""
    body = plain('Brush body', '#141414', 0.62)
    box('Tire brush handle', 0.03, 0.14, 0.022, body, e, 0, -0.07, 0.012, bevel=0.01)
    box('Tire brush head', 0.066, 0.125, 0.028, body, e, 0, -0.2, 0.012, bevel=0.013)
    rnd, strands = FIBRE_RNG, []
    for ix in range(7):
        for iy in range(13):
            sx, sy = (ix - 3) / 3, (iy - 6) / 6                      # -1..1 across and along the head
            for _ in range(10):
                bx = sx * 0.023 + rnd.uniform(-0.004, 0.004)
                by = -0.2 + sy * 0.05 + rnd.uniform(-0.004, 0.004)
                dx, dy = sx * 0.34 + rnd.uniform(-0.07, 0.07), sy * 0.26 + rnd.uniform(-0.07, 0.07)
                ln = 0.044 * rnd.uniform(0.9, 1.05) / math.sqrt(1 + dx * dx + dy * dy)
                strands.append(((bx, by, 0.024), (bx + dx * ln, by + dy * ln, 0.024 + ln),
                                0.0019, 0.0014, rnd.choice(('#ff8c0a', '#ff8c0a', '#f57200'))))
    fibres('Tire brush bristles', strands, e, root=0.5)

def wash_mitt(e):
    """Telescoping pole with a chenille mitt over a flat head; the mitt faces the room (+z)."""
    cyl('Mitt pole lower', 0.014, 0.014, 0.72, M['alu'], e, 0, 0.36, 0, seg=14, bevel=0)
    cyl('Mitt pole upper', 0.011, 0.011, 0.6, M['alu'], e, 0, 1.0, 0, seg=14, bevel=0)
    cyl('Mitt grip', 0.018, 0.018, 0.3, M['rubber'], e, 0, 0.2, 0, seg=14, bevel=0.004)
    for k, y in enumerate((0.47, 0.72)):
        cyl(f'Mitt lock {k}', 0.016, 0.019, 0.05, M['black'], e, 0, y, 0, seg=14, bevel=0.003)
    HY, HZ, A, B = 1.34, 0.02, 0.125, 0.095          # head centre height, offset off the pole, half-size
    box('Mitt joint', 0.045, 0.07, 0.03, M['black'], e, 0, HY - 0.05, 0.006, bevel=0.008)
    box('Mitt plate', 0.21, 0.15, 0.012, M['black'], e, 0, HY, HZ, bevel=0.005)
    box('Mitt pad', 0.235, 0.175, 0.04, plain('Mitt pad', '#8a3a08', 1.0), e, 0, HY, HZ + 0.024, bevel=0.018)
    rnd, strands = FIBRE_RNG, []
    shade = lambda: rnd.choice(('#f47a12', '#f47a12', '#e0600a'))
    def noodle(base, d):
        n = math.sqrt(sum(c * c for c in d))
        ln = 0.026 * rnd.uniform(0.85, 1.1)
        strands.append((base, tuple(b + c / n * ln for b, c in zip(base, d)), 0.0075, 0.0066, shade()))
    step = 0.0125
    for gx in range(-10, 11):                         # the face: upright in the middle, splaying toward the edge
        for gy in range(-8, 9):
            x, y = gx * step + rnd.uniform(-0.004, 0.004), gy * step + rnd.uniform(-0.004, 0.004)
            edge = ((x / A) ** 4 + (y / B) ** 4) ** 0.25
            if edge > 0.97:
                continue
            out = edge ** 3
            noodle((x, HY + y, HZ + 0.042),
                   (x / A * out * 1.3 + rnd.uniform(-0.2, 0.2), y / B * out * 1.3 + rnd.uniform(-0.2, 0.2), 1.05 - 0.8 * out))
    for i in range(110):                              # the rim: pointing outward all the way round
        th = 2 * PI * (i + rnd.random()) / 110
        c, s = math.cos(th), math.sin(th)
        x, y = A * math.copysign(abs(c) ** 0.5, c), B * math.copysign(abs(s) ** 0.5, s)
        nx, ny = x ** 3 / A ** 4, y ** 3 / B ** 4
        nn = math.hypot(nx, ny)
        noodle((x * 0.93, HY + y * 0.93, HZ + 0.026 + rnd.uniform(-0.012, 0.014)),
               (nx / nn + rnd.uniform(-0.15, 0.15), ny / nn + rnd.uniform(-0.15, 0.15), rnd.uniform(-0.45, 0.4)))
    fibres('Mitt noodles', strands, e, sides=5, root=0.5)

# ---------------------------------------------------------------- back wall: bench + pegboard
use('Furniture')
BW, BD, BH = 3.0, 0.66, 0.92
bench = empty('Workbench', None, -0.35, 0, -HD + BD / 2 + 0.03)
box('Bench top', BW, 0.06, BD, M['bench_top'], bench, 0, BH - 0.03, 0, bevel=0.006)
box('Bench apron', BW - 0.1, 0.12, 0.035, M['bench_leg'], bench, 0, BH - 0.12, BD / 2 - 0.07)
k = 0
for sx in (-1, 1):
    for sz in (-1, 1):
        box(f'Bench leg {k}', 0.09, BH - 0.06, 0.09, M['bench_leg'], bench, sx * (BW / 2 - 0.09), (BH - 0.06) / 2, sz * (BD / 2 - 0.07), bevel=0.006)
        k += 1
box('Bench lower shelf', BW - 0.12, 0.035, BD - 0.1, M['bench_leg'], bench, 0, 0.2, 0)
for sx, n in ((-1, 'L'), (1, 'R')):
    box(f'Bench stretcher {n}', 0.05, 0.09, BD - 0.2, M['bench_leg'], bench, sx * (BW / 2 - 0.09), 0.16, 0)
cab = empty('Drawer cabinet', bench, -0.75, 0.22, 0)
box('Drawer cabinet body', 1.3, 0.5, 0.5, M['cabinet'], cab, 0, 0.25, 0, bevel=0.006)
for r in range(3):
    for c in range(5):
        box(f'Drawer {r}{c}', 0.235, 0.14, 0.012, M['cabinet_front'], cab, -0.52 + c * 0.26, 0.09 + r * 0.16, 0.253, bevel=0.003)
        box(f'Drawer pull {r}{c}', 0.09, 0.016, 0.014, M['steel'], cab, -0.52 + c * 0.26, 0.09 + r * 0.16, 0.264, bevel=0.004)
use('Props')
box('Tote', 0.55, 0.3, 0.4, M['black'], bench, 0.55, 0.37, 0, bevel=0.025)
box('Tote lid', 0.57, 0.03, 0.42, M['orange'], bench, 0.55, 0.535, 0, bevel=0.01)
box('Bench carton', 0.34, 0.26, 0.3, M['cardboard'], bench, 1.12, 0.35, 0.02, ry=0.08, bevel=0.003)
use('Live props')        # solid-colour props: not baked, lit live in the browser like the products
bk = empty('Bench bucket', bench, 1.2, BH, -0.05)
cyl('Bench bucket body', 0.15, 0.12, 0.27, M['orange'], bk, 0, 0.135, 0, seg=36)
cyl('Bench bucket rim', 0.155, 0.155, 0.015, M['orange'], bk, 0, 0.27, 0, seg=36)
# a little bench-top life: rag, tape measure, spray-trigger-free clutter
box('Bench rag', 0.22, 0.012, 0.16, M['grey_cloth'], bench, -1.25, BH + 0.006, 0.14, ry=0.4, bevel=0.005)
use('Props')

use('Products')
f11 = slot('foam-cannon', 'boxed', bench, -0.95, BH, 0.02, 0.22, 0.27)      # boxes are built further down
f5 = slot('foam-master', 'boxed', bench, -0.55, BH, -0.06, -0.14, 0.41)
towels(slot('towels', 'towel_stack', bench, 0.15, BH, 0.04, 0.12, 0.12))
slot('leather', 'spray_bottle', bench, 0.72, BH, 0.0, -0.2)

use('Furniture')
PW, PH, PY = BW, 1.15, 1.04
peg = empty('Pegboard', None, bench.location.x, PY, -HD + 0.03)
box('Pegboard panel', PW, PH, 0.02, M['peg'], peg, 0, PH / 2, 0, bevel=0)
box('Pegboard frame top', PW + 0.06, 0.04, 0.035, M['bench_leg'], peg, 0, PH + 0.02, 0.005)
box('Pegboard frame bottom', PW + 0.06, 0.04, 0.035, M['bench_leg'], peg, 0, -0.02, 0.005)
for sx, n in ((-1, 'L'), (1, 'R')):
    box(f'Pegboard frame {n}', 0.04, PH, 0.035, M['bench_leg'], peg, sx * (PW / 2 + 0.01), PH / 2, 0.005)
def hook(x, y):
    cyl('Peg hook', 0.004, 0.004, 0.05, M['steel'], peg, x, y, 0.035, axis='z', seg=8, bevel=0)
use('Products')
for sid, kind, fn, x, y, dy in (
    ('boar-brush', 'detail_brush', lambda e: detail_brush(e), -1.2, 0.86, -0.1),
    ('wheel-brush', 'wheel_brush', lambda e: wheel_brush(e, False), -0.66, 0.92, -0.17),
    ('mf-wheel-brush', 'wheel_brush', lambda e: wheel_brush(e, True), -0.4, 0.92, -0.17),
    ('tire-brush', 'tire_brush', tire_brush, -0.1, 0.84, -0.12),
):
    hook(x, y)
    fn(slot(sid, kind, peg, x, y - 0.005, 0.04, 0, dy))
use('Props')
for x, s in ((-1.06, 0.85), (-0.94, 0.7)):
    hook(x, 0.86)
    detail_brush(empty('Spare brush', peg, x, 0.855, 0.04), s)
box('Peg shelf', 1.0, 0.025, 0.16, M['shelf'], peg, 0.85, 0.5, 0.09)
for sx, n in ((-1, 'L'), (1, 'R')):
    box(f'Peg shelf bracket {n}', 0.02, 0.1, 0.12, M['black'], peg, 0.85 + sx * 0.42, 0.44, 0.07)
for i in range(4):
    box(f'Parts bin {i}', 0.11, 0.07, 0.08, M['black'] if i % 2 else M['orange'], peg, 0.42 + i * 0.13, 0.9, 0.05, bevel=0.006)
# paper-towel holder on the pegboard
cyl('Paper towel roll', 0.06, 0.06, 0.26, M['white'], peg, -1.2, 0.3, 0.1, axis='x', seg=28, bevel=0.004)
cyl('Paper towel bar', 0.008, 0.008, 0.34, M['steel'], peg, -1.2, 0.3, 0.1, axis='x', seg=10, bevel=0)
use('Slots')
slot('ceramic-detail', 'spray_bottle', peg, 0.55, 0.5125, 0.1, 0.15)
slot('graphene', 'spray_bottle', peg, 0.85, 0.5125, 0.1, 0.0)
slot('ceramic-wash', 'spray_bottle', peg, 1.15, 0.5125, 0.1, -0.15)

# pendant work lights
use('Lights')
for i, x in enumerate((-1.35, -0.35, 0.65)):
    p = empty(f'Pendant {i}', None, x, 2.02, -HD + 0.62)
    cord_h = H - 2.02 - 0.16
    cyl(f'Pendant cord {i}', 0.004, 0.004, cord_h, M['black'], p, 0, cord_h / 2 + 0.1, 0, seg=8, bevel=0)
    cyl(f'Pendant shade {i}', 0.035, 0.15, 0.11, M['dark_steel'], p, 0, 0.05, 0, seg=36, bevel=0)
    cyl(f'Pendant socket {i}', 0.025, 0.025, 0.06, M['black'], p, 0, 0.12, 0, seg=16)
    sphere(f'Pendant bulb {i}', 0.032, M['bulb'], p, 0, 0.0, 0)
    ld = bpy.data.lights.new(f'Pendant light {i}', 'POINT')
    ld.energy = 6
    ld.color = (1.0, 0.78, 0.55)
    ld.shadow_soft_size = 0.035
    lo = bpy.data.objects.new(f'Pendant light {i}', ld)
    lo.location = V(0, -0.06, 0)
    lo.parent = p
    COL.objects.link(lo)

# extendable wash mitt leaning in the back-left corner
use('Products')
mitt = slot('wash-mitt', 'wash_mitt', None, -HW + 0.22, 0, -HD + 0.2, 0.6, 1.36)
mitt.rotation_euler = (-0.06, -0.07, 0.6)
wash_mitt(mitt)

# tall cabinet, back-right
use('Furniture')
tall = empty('Tall cabinet', None, HW - 0.5, 0, -HD + 0.27)
box('Tall cabinet body', 0.9, 1.9, 0.5, M['cabinet'], tall, 0, 0.95, 0, bevel=0.006)
for sx, n in ((-1, 'L'), (1, 'R')):
    box(f'Tall cabinet door {n}', 0.43, 1.82, 0.012, M['cabinet_front'], tall, sx * 0.222, 0.95, 0.253, bevel=0.003)
    box(f'Tall cabinet handle {n}', 0.014, 0.14, 0.02, M['steel'], tall, sx * 0.04, 1.0, 0.268, bevel=0.005)
use('Props')
box('Cabinet carton', 0.4, 0.22, 0.32, M['cardboard'], tall, -0.15, 2.01, 0, ry=-0.1, bevel=0.003)
box('Cabinet tote', 0.34, 0.2, 0.3, M['black'], tall, 0.24, 2.0, 0.02, bevel=0.02)

# ---------------------------------------------------------------- left wall
use('Furniture')
shelves = empty('Wall shelves', None, -HW, 0, -1.55)
SL, SD = 1.9, 0.26
for i, y in enumerate((1.18, 1.62)):
    box(f'Wall shelf {i}', SD, 0.035, SL, M['shelf'], shelves, SD / 2, y, 0)
    for j, z_ in enumerate((-0.75, 0, 0.75)):
        box(f'Shelf bracket arm {i}{j}', 0.2, 0.012, 0.03, M['black'], shelves, 0.1, y - 0.024, z_, bevel=0.003)
        box(f'Shelf bracket back {i}{j}', 0.012, 0.16, 0.03, M['black'], shelves, 0.006, y - 0.1, z_, bevel=0.003)
use('Slots')
for sid, z_ in (('iron-remover', -0.72), ('bug-tar', -0.4), ('tire-cleaner', -0.08), ('tire-dressing', 0.24)):
    slot(sid, 'spray_bottle', shelves, 0.14, 1.1975, z_, PI / 2)
use('Products')
xc = slot('xc-wash', 'jug', shelves, 0.14, 1.1975, 0.66, PI / 2, 0.32)     # the jug itself is built further down
use('Props')
box('Shelf tote', 0.2, 0.18, 0.36, M['black'], shelves, 0.13, 1.73, -0.6, bevel=0.02)
box('Shelf carton', 0.2, 0.14, 0.3, M['cardboard'], shelves, 0.13, 1.71, -0.12, bevel=0.003)
# (the two spare jugs on the top shelf are built further down, with the XC jug)
def import_obj(filename):
    """Import a supplied OBJ (Rhino export, Y-up) and return its single mesh object."""
    before = set(bpy.data.objects)
    bpy.ops.wm.obj_import(filepath=os.path.join(ASSETS, filename), forward_axis='NEGATIVE_Z', up_axis='Y')
    ob = next(o for o in bpy.data.objects if o not in before)
    for c in list(ob.users_collection):
        c.objects.unlink(ob)
    # the importer puts the Y-up -> Z-up conversion on the object; bake it into the mesh
    ob.data.transform(ob.matrix_world)
    ob.matrix_world = Matrix.Identity(4)
    return ob

# Supplied model: three stacked tyres plus one leaning against them. Its units
# are not metres; scaled so a tyre is ~0.66 m across.
tyres = import_obj('stacked-tires.obj')
tyres.name = 'Stacked tyres'
TYRE_SCALE = 0.66 / 29.0
tyres.data.transform(Matrix.Scale(TYRE_SCALE, 4) @ Matrix.Translation((11.0, 105.0, 0.0)))   # stack centre -> origin
tyres.data.materials.clear()
tyres.data.materials.append(plain('Tyre rubber', '#2e2e2e', 0.78))
tyres.location = V(-HW + 0.37, 0, -2.2)
tyres.rotation_euler = (0, 0, -PI / 2)          # leaning tyre runs along the wall, toward the door
COL.objects.link(tyres)
CART_ROT = PI / 2

# sunlit zone by the door: hose reel, wash bucket, pressure washer
reel = empty('Hose reel', None, -HW, 1.15, 1.55)
box('Reel plate', 0.03, 0.34, 0.3, M['dark_steel'], reel, 0.015, 0, 0)
box('Reel arm', 0.2, 0.03, 0.05, M['dark_steel'], reel, 0.11, 0, 0)
HOSE = plain('Hose orange', '#d4571a', 0.7)
cyl('Reel drum', 0.13, 0.13, 0.23, M['dark_steel'], reel, 0.22, 0, 0, axis='z', seg=32)
for k in range(6):
    torus(f'Reel hose coil {k}', 0.15, 0.018, HOSE, reel, 0.22, 0, (k - 2.5) * 0.036, axis='z', seg=48, rseg=12)
cyl('Reel hose tail', 0.018, 0.018, 0.38, HOSE, reel, 0.37, -0.19, 0.09, seg=14, bevel=0)
cyl('Reel hose nozzle', 0.015, 0.02, 0.07, M['brass'], reel, 0.37, -0.41, 0.09, seg=14)
for sz, n in ((-1, 'a'), (1, 'b')):
    cyl(f'Reel flange {n}', 0.2, 0.2, 0.012, M['dark_steel'], reel, 0.22, 0, sz * 0.118, axis='z', seg=40)
    cyl(f'Reel hub {n}', 0.035, 0.035, 0.02, M['steel'], reel, 0.22, 0, sz * 0.13, axis='z', seg=20)
_prev = COL
use('Live props')
wb = empty('Wash bucket', None, -HW + 0.38, 0, 0.75)
cyl('Wash bucket body', 0.16, 0.13, 0.3, M['orange'], wb, 0, 0.15, 0, seg=40)
cyl('Wash bucket rim', 0.166, 0.166, 0.018, M['orange'], wb, 0, 0.3, 0, seg=40)
torus('Wash bucket handle', 0.165, 0.005, M['steel'], wb, 0, 0.29, 0, arc=PI, seg=28, rseg=8, rot=(PI / 2 - 1.1, 0, 0))
COL = _prev
# Supplied model: rolling storage cart (inches, 40" tall), long side along the wall.
cart = import_obj('storage-cart.obj')
cart.name = 'Storage cart'
cart.data.transform(Matrix.Scale(0.0254, 4) @ Matrix.Translation((-48.0, 66.0, 0.0)))   # footprint centre -> origin
CART_MATS = {'orange': M['orange'], 'black': M['black'], 'wheels': M['rubber']}
for i, m in enumerate(cart.data.materials):
    cart.data.materials[i] = next(v for k, v in CART_MATS.items() if k in m.name.lower())
cart.location = V(-HW + 0.27, 0, 2.4)
cart.rotation_euler = (0, 0, CART_ROT)
COL.objects.link(cart)

# ---------------------------------------------------------------- right wall
use('Furniture')
chest = empty('Tool chest', None, HW - 0.3, 0, -1.25)
box('Chest body', 0.5, 0.78, 1.05, M['chest'], chest, 0, 0.51, 0, bevel=0.008)
box('Chest top', 0.54, 0.03, 1.09, M['bench_top'], chest, 0, 0.915, 0, bevel=0.005)
for i, y in enumerate((0.2, 0.34, 0.48, 0.62, 0.76)):
    tallr = i < 2
    box(f'Chest drawer {i}', 0.012, 0.16 if tallr else 0.12, 1.0, M['chest_front'], chest, -0.253, y + (0.02 if tallr else 0.03), 0, bevel=0.003)
    box(f'Chest pull {i}', 0.016, 0.014, 0.9, M['orange'], chest, -0.262, y + (0.08 if tallr else 0.07), 0, bevel=0.004)
k = 0
for sz in (-1, 1):
    for sx in (-1, 1):
        cyl(f'Chest caster {k}', 0.05, 0.05, 0.035, M['rubber'], chest, sx * 0.19, 0.05, sz * 0.44, axis='x', seg=20, bevel=0.005)
        k += 1
use('Products')
jd = slot('blower', 'boxed', chest, -0.02, 0.93, 0.1, -PI / 2 + 0.25, 0.36)     # the boxed blower is built further down

use('Furniture')
# Supplied model: wooden swivel stool (inches, 30" tall).
stool = import_obj('garage-stool.obj')
stool.name = 'Stool'
stool.data.transform(Matrix.Scale(0.0254, 4) @ Matrix.Translation((-50.0, 0.0, 0.0)))   # seat axis -> origin
# its oak veneer texture uses the model's own UVs, kept as a layer named 'src' so the bake can find them
while len(stool.data.uv_layers) > 1:
    stool.data.uv_layers.remove(stool.data.uv_layers[-1])
stool.data.uv_layers[0].name = 'src'
STOOL_OAK, nt, bsdf = new_mat('Stool oak')
bsdf.inputs['Roughness'].default_value = 0.6
oak_tex = nt.nodes.new('ShaderNodeTexImage')
oak_tex.image = bpy.data.images.load(os.path.join(ASSETS, 'Poliigon_WoodVeneerOak_7760_BaseColor.jpg'))
oak_uv = nt.nodes.new('ShaderNodeUVMap')
oak_uv.uv_map = 'src'
nt.links.new(oak_uv.outputs['UV'], oak_tex.inputs['Vector'])
nt.links.new(oak_tex.outputs['Color'], bsdf.inputs['Base Color'])
for i, m in enumerate(stool.data.materials):
    stool.data.materials[i] = M['black'] if 'black' in m.name.lower() else STOOL_OAK
stool.location = V(1.15, 0, -2.05)
stool.rotation_euler = (0, 0, 0.5)
COL.objects.link(stool)

use('Props')
# two stacked cartons on the floor between the tool chest and the door
TAPE = plain('Packing tape', '#c9a877', 0.6)
box('Floor carton lower', 0.5, 0.4, 0.6, M['cardboard'], None, HW - 0.3, 0.2, -0.3, ry=0.04, bevel=0.004)
box('Floor carton lower tape', 0.06, 0.002, 0.6, TAPE, None, HW - 0.3, 0.401, -0.3, ry=0.04, bevel=0)
box('Floor carton upper', 0.42, 0.32, 0.46, M['cardboard'], None, HW - 0.31, 0.56, -0.33, ry=-0.16, bevel=0.004)
box('Floor carton upper tape', 0.06, 0.002, 0.46, TAPE, None, HW - 0.31, 0.721, -0.33, ry=-0.16, bevel=0)
box('Sign board', 0.02, 0.57, 1.74, M['sign'], None, HW - 0.011, 1.85, 0.75, bevel=0.004)
# The real logo, from SudsLabLogo.svg in the project root: raised lettering,
# "suds." in white and "lab" (everything right of LOGO_SPLIT_X) in orange.
LOGO_SVG = os.path.join(os.path.dirname(HERE), 'SudsLabLogo.svg')
LOGO_WIDTH = 1.45           # metres on the wall
LOGO_SPLIT_X = 280.0        # SVG units; the "l" of "lab" starts at ~282 of 428
before = set(bpy.data.objects)
bpy.ops.import_curve.svg(filepath=LOGO_SVG)
logo_parts = [o for o in bpy.data.objects if o not in before]
bpy.context.view_layer.update()
def world_bounds(ob):
    pts = [ob.matrix_world @ Vector(c) for c in ob.bound_box]
    return Vector([min(p[i] for p in pts) for i in range(3)]), Vector([max(p[i] for p in pts) for i in range(3)])
bounds = [world_bounds(o) for o in logo_parts]
lo = Vector([min(b[0][i] for b in bounds) for i in range(3)])
hi = Vector([max(b[1][i] for b in bounds) for i in range(3)])
logo_scale = LOGO_WIDTH / (hi.x - lo.x)
svg_per_unit = 428.03 / (hi.x - lo.x)
# logo local +X -> Blender -Y (viewer +z), upright, facing -X into the room
logo = empty('Sign logo', None, HW - 0.021, 1.85, 0.75)
logo.rotation_euler = (PI / 2, 0, -PI / 2)
logo_xf = Matrix.Scale(logo_scale, 4) @ Matrix.Translation(-(lo + hi) / 2)
for o in logo_parts:                      # give the flat outlines some thickness (in SVG units for now)
    o.data.dimensions = '2D'
    o.data.fill_mode = 'BOTH'
    o.data.extrude = 0.007 / logo_scale
dg = bpy.context.evaluated_depsgraph_get()
for o, (bmin, bmax) in zip(logo_parts, bounds):
    is_lab = ((bmin.x + bmax.x) / 2 - lo.x) * svg_per_unit > LOGO_SPLIT_X
    # bake to a mesh: simpler to scale reliably than the curve, and it is what the web export needs anyway
    me = bpy.data.meshes.new_from_object(o.evaluated_get(dg))
    me.transform(logo_xf @ o.matrix_world)
    me.materials.clear()
    me.materials.append(M['orange'] if is_lab else M['sign_white'])
    part = bpy.data.objects.new('Sign logo ' + ('lab' if is_lab else 'suds'), me)
    part.parent = logo
    COL.objects.link(part)
for o in logo_parts:
    cu = o.data
    bpy.data.objects.remove(o)
    bpy.data.curves.remove(cu)
for c in [c for c in bpy.data.collections if c.name.startswith('SudsLabLogo')]:
    bpy.data.collections.remove(c)
broom = empty('Broom', None, HW - 0.1, 0, 2.6)
cyl('Broom stick', 0.012, 0.012, 1.35, M['bench_leg'], broom, 0, 0.72, 0, seg=10, bevel=0, rot=(0, 0.06, 0))
box('Broom head', 0.05, 0.1, 0.3, M['black'], broom, 0.04, 0.06, 0, bevel=0.006)
ext = empty('Fire extinguisher', None, HW - 0.1, 0.55, 3.0)
cyl('Extinguisher tank', 0.06, 0.06, 0.38, M['red'], ext, 0, 0.19, 0, seg=28, bevel=0.012)
cyl('Extinguisher neck', 0.02, 0.03, 0.05, M['dark_steel'], ext, 0, 0.405, 0, seg=16)
box('Extinguisher lever', 0.025, 0.02, 0.1, M['black'], ext, 0, 0.44, 0.02, bevel=0.004)
torus('Extension cord', 0.14, 0.03, M['orange'], None, HW - 0.04, 1.35, 2.05, axis='x', seg=40, rseg=12)
cyl('Cord hook', 0.008, 0.008, 0.08, M['steel'], None, HW - 0.04, 1.49, 2.05, axis='x', seg=10, bevel=0)
# anti-fatigue mat in front of the bench
box('Bench mat', 1.6, 0.015, 0.6, M['rubber'], None, -0.35, 0.0075, -HD + BD + 0.45, bevel=0.006)

# ---------------------------------------------------------------- spray bottles (supplied model)
BOTTLES = {   # slot: (label code, label lines, liquid colour)
    'leather': ('LC', ['LEATHER', 'CLEANER'], '#f0a460'),
    'ceramic-detail': ('CD', ['CERAMIC', 'DETAIL SPRAY'], '#f08a1c'),
    'graphene': ('GC', ['GRAPHENE', 'SPRAY COATING'], '#5f8291'),
    'ceramic-wash': ('CP', ['CERAMIC', 'PROTECTIVE WASH'], '#2fb83a'),
    'iron-remover': ('FE', ['IRON PARTICLE', 'REMOVER'], '#eef2f2'),
    'bug-tar': ('BT', ['BUG + TAR', 'REMOVER'], '#e8e4da'),
    'tire-cleaner': ('TC', ['TIRE + RIM', 'CLEANER'], '#2f7fd6'),
    'tire-dressing': ('T3', ['TIRE + TRIM', 'DRESSING'], '#b7a3e8'),
}

CLEAR_LIQUIDS = {'CP', 'LC', 'FE'}       # label codes whose liquid is see-through

def render_labels():
    """Draw each wrap-around label (text and all) with Blender itself, once."""
    os.makedirs(TEXTURES, exist_ok=True)
    todo = {k: v for k, v in BOTTLES.items() if not os.path.exists(os.path.join(TEXTURES, f'label_{v[0]}.png'))}
    if not todo:
        return
    font = None
    for f in ('arialbd.ttf',):       # static font; variable fonts (Bahnschrift) tessellate badly
        fp = os.path.join(os.environ.get('WINDIR', 'C:/Windows'), 'Fonts', f)
        if os.path.exists(fp):
            font = bpy.data.fonts.load(fp)
            break
    sc = bpy.data.scenes.new('Label render')
    w = bpy.data.worlds.new('Label bg')
    try:
        w.use_nodes = True
    except Exception:
        pass
    next(n for n in w.node_tree.nodes if n.bl_idname == 'ShaderNodeBackground').inputs['Color'].default_value = lin('#141414')
    sc.world = w
    cd = bpy.data.cameras.new('Label cam')
    cd.type = 'ORTHO'
    cd.ortho_scale = 2.0
    cam = bpy.data.objects.new('Label cam', cd)
    cam.location = (0, 0, 5)
    sc.collection.objects.link(cam)
    sc.camera = cam
    sc.render.engine = 'CYCLES'
    sc.cycles.samples = 16
    sc.cycles.use_denoising = False
    sc.render.resolution_x, sc.render.resolution_y = 1536, 736
    try:
        sc.view_settings.view_transform = 'Standard'
    except Exception:
        pass
    ink = {c: emission('Label ink ' + c, c, 1.0) for c in ('#ffffff', '#ff5b00')}
    for m in ink.values():
        next(n for n in m.node_tree.nodes if n.bl_idname == 'ShaderNodeBsdfPrincipled').inputs['Base Color'].default_value = (0, 0, 0, 1)
    made = []
    def text(body, size, x, y, color, align='CENTER'):
        cu = bpy.data.curves.new('Label text', 'FONT')
        cu.body, cu.size, cu.align_x = body, size, align
        if font:
            cu.font = font
        cu.materials.append(ink[color])
        ob = bpy.data.objects.new('Label text', cu)
        ob.location = (x, y, 0)
        sc.collection.objects.link(ob)
        made.append(ob)
    def bar(y, h, color):
        me = bpy.data.meshes.new('Label bar')
        me.from_pydata([(-1, y, 0), (1, y, 0), (1, y + h, 0), (-1, y + h, 0)], [], [(0, 1, 2, 3)])
        me.materials.append(ink[color])
        ob = bpy.data.objects.new('Label bar', me)
        sc.collection.objects.link(ob)
        made.append(ob)
    for code, lines, _ in todo.values():
        bar(-0.479, 0.07, '#ff5b00')
        text('suds.', 0.085, 0.045, 0.34, '#ffffff', 'RIGHT')
        text('lab', 0.085, 0.05, 0.34, '#ff5b00', 'LEFT')
        text(code, 0.44, 0, -0.04, '#ff5b00')
        for i, ln in enumerate(lines):
            text(ln, 0.07, 0, -0.18 - i * 0.09, '#ffffff')
        sc.render.filepath = os.path.join(TEXTURES, f'label_{code}.png')
        bpy.ops.render.render(write_still=True, scene=sc.name)
        for ob in made:
            bpy.data.objects.remove(ob)
        made.clear()
    bpy.data.scenes.remove(sc)

render_labels()
use('Products')
bottle_src = import_obj('12oz-spray-bottle.obj')
bottle_me = bottle_src.data
bottle_me.name = 'Spray bottle'
# model is in millimetres, standing at x = -200; move its axis to the origin and convert to metres
bottle_me.transform(Matrix.Scale(0.001, 4) @ Matrix.Translation((200.0, 0.0, 0.0)))
slots_by_name = {m.name: i for i, m in enumerate(bottle_me.materials)}
BODY, LABEL = slots_by_name['Plastic'], slots_by_name['Physically Based']

# Lighten it for the web: the supplied model is ~82k faces, most of them in the
# trigger sprayer. Drop the dip tube (hidden inside the opaque bottle) and
# decimate everything except the label band, which is already very light.
BOTTLE_KEEP = 0.12
full_faces = len(bottle_me.polygons)
bm = bmesh.new()
bm.from_mesh(bottle_me)
tube = slots_by_name['Default']
bmesh.ops.delete(bm, geom=[f for f in bm.faces if f.material_index == tube], context='FACES')
bm.to_mesh(bottle_me)
bm.free()
label_verts = {v for p in bottle_me.polygons if p.material_index == LABEL for v in p.vertices}
vg = bottle_src.vertex_groups.new(name='decimate')
vg.add([v.index for v in bottle_me.vertices if v.index not in label_verts], 1.0, 'REPLACE')
dec = bottle_src.modifiers.new('Decimate', 'DECIMATE')
dec.ratio = BOTTLE_KEEP
dec.vertex_group = vg.name
COL.objects.link(bottle_src)
light = bpy.data.meshes.new_from_object(bottle_src.evaluated_get(bpy.context.evaluated_depsgraph_get()))
light.name = 'Spray bottle'
bottle_me = light
# the simplified body cuts corners, so ease the label band out 1.5 mm to stay clear of it
for vi in {v for p in bottle_me.polygons if p.material_index == LABEL for v in p.vertices}:
    co = bottle_me.vertices[vi].co
    k = 1 + 0.0015 / max(math.hypot(co.x, co.y), 1e-6)
    co.x *= k
    co.y *= k
print(f'BOTTLE faces {full_faces} -> {len(bottle_me.polygons)}')
# A real bottle is clear plastic holding liquid up to the shoulder. Split the shell at the
# fill line: liquid colour below, clear plastic above (neck and threads), and a flat
# liquid surface just inside the wall where the two meet.
FILL_Z = 0.186
bottle_me.materials.append(plain('Bottle plastic', '#f4f6f5', 0.08))
bottle_me.materials.append(None)
CLEAR, SURFACE = len(bottle_me.materials) - 2, len(bottle_me.materials) - 1
next(n for n in bottle_me.materials[CLEAR].node_tree.nodes
     if n.bl_idname == 'ShaderNodeBsdfPrincipled').inputs['Transmission Weight'].default_value = 1.0
bm = bmesh.new()
bm.from_mesh(bottle_me)
shell = [f for f in bm.faces if f.material_index == BODY]
cut = bmesh.ops.bisect_plane(
    bm, geom=shell + list({e for f in shell for e in f.edges}) + list({v for f in shell for v in f.verts}),
    plane_co=(0, 0, FILL_Z), plane_no=(0, 0, 1))
for f in bm.faces:
    if f.material_index == BODY and f.calc_center_median().z > FILL_Z:
        f.material_index = CLEAR
rim = max(math.hypot(v.co.x, v.co.y) for v in cut['geom_cut'] if isinstance(v, bmesh.types.BMVert))
disc = bmesh.ops.create_circle(bm, cap_ends=True, segments=32, radius=rim * 0.97,
                               matrix=Matrix.Translation((0, 0, FILL_Z - 0.0005)))
for f in {f for v in disc['verts'] for f in v.link_faces}:
    f.material_index = SURFACE
bm.to_mesh(bottle_me)
bm.free()
# wrap-around UVs for the label band: u runs around the bottle with the front (trigger side, -Y) at 0.5
uv = bottle_me.uv_layers.active or bottle_me.uv_layers.new()
zs = [bottle_me.vertices[v].co.z for p in bottle_me.polygons if p.material_index == LABEL for v in p.vertices]
z0, z1 = min(zs), max(zs)
for p in bottle_me.polygons:
    if p.material_index != LABEL:
        continue
    ref = None
    for li in p.loop_indices:
        co = bottle_me.vertices[bottle_me.loops[li].vertex_index].co
        ang = math.atan2(co.x, -co.y)
        if ref is None:
            ref = ang
        elif ang - ref > PI:
            ang -= 2 * PI
        elif ref - ang > PI:
            ang += 2 * PI
        uv.data[li].uv = (0.5 + ang / (2 * PI), (co.z - z0) / (z1 - z0))
sprayer = plain('Sprayer plastic', '#0d0d0d', 0.3, 0, 0.4)
bottle_me.materials[slots_by_name['Plastic (1)']] = sprayer
bottle_me.materials[slots_by_name['Plastic (1) [imported]']] = sprayer
bottle_me.materials[slots_by_name['Default']] = plain('Dip tube', '#e8e8e2', 0.4)
bpy.data.objects.remove(bottle_src)

def liquid_mat(code, color):
    m, nt, b = new_mat(f'Bottle {code}')
    b.inputs['Base Color'].default_value = lin(color)
    b.inputs['Roughness'].default_value = 0.28
    b.inputs['Coat Weight'].default_value = 0.4
    m.diffuse_color = lin(color)
    if code in CLEAR_LIQUIDS:        # see-through; the viewer adds the depth tint (src/garage.js)
        b.inputs['Transmission Weight'].default_value = 1.0
        b.inputs['Roughness'].default_value = 0.05
    return m

def label_mat(code):
    m, nt, b = new_mat(f'Label {code}')
    # supplied print artwork ('2D assets/CPAsset 1.png') wins over the drawn placeholder
    art = sorted(glob.glob(os.path.join(HERE, '2D assets', f'{code}Asset*.png')))
    img = bpy.data.images.load(art[0] if art else os.path.join(TEXTURES, f'label_{code}.png'), check_existing=True)
    img.pack()
    tex = nt.nodes.new('ShaderNodeTexImage')
    tex.image = img
    nt.links.new(tex.outputs['Color'], b.inputs['Base Color'])
    b.inputs['Roughness'].default_value = 0.4
    return m

for e in SLOTS:
    if e['kind'] != 'spray_bottle':
        continue
    code, _, color = BOTTLES[e['slot']]
    ob = bpy.data.objects.new(f"Bottle {code}", bottle_me)        # all eight share one mesh
    ob.parent = e
    COL.objects.link(ob)
    for idx, mat in ((BODY, liquid_mat(code, color)), (LABEL, label_mat(code)),
                     (SURFACE, plain(f'Liquid top {code}', color, 0.15))):
        ob.material_slots[idx].link = 'OBJECT'                  # per-bottle colour and label
        ob.material_slots[idx].material = mat

# ---------------------------------------------------------------- 64 oz jug (XC X-Treme Conditions Wash)
# A blow-moulded jug: cap on the left, hollow handle on the right. It is built as a coarse
# block cage (grid cells switched on or off) and rounded by subdivision, which gives the
# soft moulded corners and the handle hole in one go. Viewer coords, front = +z.
def block_cage(xs, ys, zs, filled):
    nx, ny, nz = len(xs) - 1, len(ys) - 1, len(zs) - 1
    on = lambda i, j, k: 0 <= i < nx and 0 <= j < ny and 0 <= k < nz and filled(i, j, k)
    bm = bmesh.new()
    vs = {}
    def vert(i, j, k):
        if (i, j, k) not in vs:
            vs[i, j, k] = bm.verts.new(V(xs[i], ys[j], zs[k]))
        return vs[i, j, k]
    for i in range(nx):
        for j in range(ny):
            for k in range(nz):
                if not on(i, j, k):
                    continue
                for axis in range(3):
                    for side in (0, 1):
                        nb = [i, j, k]
                        nb[axis] += 1 if side else -1
                        if on(*nb):
                            continue                    # shared wall between two cells: no skin
                        c = [i, j, k]
                        c[axis] += side
                        a, b = [x for x in range(3) if x != axis]
                        quad = []
                        for da, db in ((0, 0), (1, 0), (1, 1), (0, 1)):
                            p = list(c)
                            p[a] += da
                            p[b] += db
                            quad.append(vert(*p))
                        bm.faces.new(quad)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    return bm

JUG_X = [-0.072, -0.061, -0.030, 0.000, 0.046, 0.061, 0.072]     # width: cap side ... handle side
JUG_Y = [0.0, 0.011, 0.150, 0.170, 0.192, 0.206, 0.226]          # height: base, body, shoulder, handle hole (2 rows), handle bar
JUG_Z = [-0.044, -0.033, -0.015, 0.015, 0.033, 0.044]            # depth
JUG_CAP_X = -0.047
def jug_cell(i, j, k):
    if j <= 2:
        return True                                               # body: the full block
    if j == 3:
        return i <= 2 or (i >= 4 and k == 2)                      # full-depth neck side | hole | pinched handle upright
    if j == 4:
        return (i <= 2 and 1 <= k <= 3) or (i >= 4 and k == 2)    # shoulder doming in under the cap
    return i >= 2 and k == 2                                      # handle bar, from the neck to the upright

def smoothstep(a, b, x):
    t = min(1.0, max(0.0, (x - a) / (b - a)))
    return t * t * (3 - 2 * t)

def jug_mesh(name, liquid, milk, levels=3):
    """The moulded shell. Natural (milky) plastic shows the liquid through it, so colour is
    stored per vertex: liquid colour up to the fill line, paler in the thin handle, milky above."""
    bm = block_cage(JUG_X, JUG_Y, JUG_Z, jug_cell)
    cage = bpy.data.meshes.new(name + ' cage')
    bm.to_mesh(cage)
    bm.free()
    src = bpy.data.objects.new(name + ' cage', cage)
    COL.objects.link(src)
    mod = src.modifiers.new('Subdivision', 'SUBSURF')
    mod.levels = mod.render_levels = levels
    me = bpy.data.meshes.new_from_object(src.evaluated_get(bpy.context.evaluated_depsgraph_get()))
    bpy.data.objects.remove(src)
    bpy.data.meshes.remove(cage)
    me.name = name
    me.polygons.foreach_set('use_smooth', [True] * len(me.polygons))
    full, milk = lin(liquid)[:3], lin(milk)[:3]
    attr = me.color_attributes.new('Col', 'FLOAT_COLOR', 'POINT')
    for v in me.vertices:
        x, h = v.co.x, v.co.z
        empty_above = smoothstep(0.196, 0.214, h)
        thin_handle = 0.55 * smoothstep(0.02, 0.045, x) * smoothstep(0.160, 0.182, h)
        t = max(empty_above, thin_handle)
        attr.data[v.index].color = (*[a + (b - a) * t for a, b in zip(full, milk)], 1.0)
    me.color_attributes.active_color = attr
    me.color_attributes.render_color_index = 0
    return me

def jug_mat():
    if 'Jug plastic' in bpy.data.materials:
        return bpy.data.materials['Jug plastic']
    m, nt, b = new_mat('Jug plastic')
    vc = nt.nodes.new('ShaderNodeVertexColor')
    vc.layer_name = 'Col'
    nt.links.new(vc.outputs['Color'], b.inputs['Base Color'])
    b.inputs['Roughness'].default_value = 0.4
    # half see-through, like natural plastic with liquid behind it. Plain alpha, matching
    # the viewer (JUG.opacity in src/garage.js); here it sets how solid the baked shadow is
    b.inputs['Alpha'].default_value = 0.5
    return m

def jug_label(name, body, mat, parent, w, h, y0, r=0.003, n=12, lift=0.0006):
    """Front label: a w x h sheet starting y0 above the base, pressed onto the moulded shell
    so it follows the slight curve of the panel. Corners are rounded like the die-cut sticker."""
    tree = BVHTree.FromPolygons([v.co.copy() for v in body.vertices], [tuple(p.vertices) for p in body.polygons])
    us = sorted({0.0, r / w, 1 - r / w, 1.0} | {i / n for i in range(1, n)})
    ws = sorted({0.0, r / h, 1 - r / h, 1.0} | {i / n for i in range(1, n)})
    verts, uvs = [], []
    for t in ws:
        for u in us:
            if u in (0.0, 1.0) and t in (0.0, 1.0):               # pull each corner onto its arc
                u += (0.29 * r / w) * (1 if u == 0.0 else -1)
                t += (0.29 * r / h) * (1 if t == 0.0 else -1)
            x, y = (u - 0.5) * w, y0 + t * h
            hit = tree.ray_cast(Vector((x, -0.3, y)), Vector((0, 1, 0)))[0]
            verts.append((x, (hit.y if hit else -JUG_Z[-1]) - lift, y))
            uvs.append((u, t))
    cols = len(us)
    faces = [(j * cols + i, j * cols + i + 1, (j + 1) * cols + i + 1, (j + 1) * cols + i)
             for j in range(len(ws) - 1) for i in range(cols - 1)]
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    uv = me.uv_layers.new()
    for lp in me.loops:
        uv.data[lp.index].uv = uvs[lp.vertex_index]
    me.polygons.foreach_set('use_smooth', [True] * len(me.polygons))
    me.materials.append(mat)
    ob = bpy.data.objects.new(name, me)
    ob.parent = parent
    COL.objects.link(ob)
    return ob

def ribbed_cap(name, r, h, mat, parent, x, y, z, ribs=44):
    """Screw cap with moulded grip ribs: a saw-tooth outline, flat shaded so each rib catches light."""
    bm = bmesh.new()
    n = ribs * 2
    def ring(rad, zz):
        return [bm.verts.new((rad(k) * math.cos(2 * PI * k / n), rad(k) * math.sin(2 * PI * k / n), zz)) for k in range(n)]
    rib = lambda k: r if k % 2 else r - 0.0009
    lo, hi, top = ring(rib, -h / 2), ring(rib, h / 2 - 0.0025), ring(lambda k: r - 0.003, h / 2)
    for a, b in ((lo, hi), (hi, top)):
        for k in range(n):
            bm.faces.new((a[k], a[(k + 1) % n], b[(k + 1) % n], b[k]))
    bm.faces.new(top)
    bm.faces.new(lo[::-1])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    return finish(name, bm, mat, parent, (x, y, z), smooth=lambda p: False)

def brand_label():
    """Label for the unbranded spare jugs: only the brand block (flask, wordmark, size) cut
    from the top of the XC artwork and centred on black. Drawn once, then kept in textures/."""
    path = os.path.join(TEXTURES, 'label_JUG.png')
    art = sorted(glob.glob(os.path.join(HERE, '2D assets', 'XC64ozAsset*.png')))
    if not os.path.exists(path):
        if not art:
            return M['sign']                    # no artwork to cut from: plain black panel
        src = bpy.data.images.load(art[0], check_existing=True)
        w, h = src.size
        px = np.empty(w * h * 4, dtype=np.float32)
        src.pixels.foreach_get(px)
        px = px.reshape(h, w, 4)                # row 0 is the bottom of the picture
        band = int(h * 0.34)                    # everything above the red product box
        out = np.zeros((h, w, 4), np.float32)
        out[..., 3] = 1.0
        y0 = (h - band) // 2
        top = px[h - band:, :, :3] * px[h - band:, :, 3:4]
        c = w // 40
        top[-c:, :c] = top[-c:, -c:] = 0        # the artwork's white corners outside its rounded edge
        out[y0:y0 + band, :, :3] = top
        out = out[:h // 2 * 2, :w // 2 * 2].reshape(h // 2, 2, w // 2, 2, 4).mean((1, 3))   # half size is plenty for a prop
        img = bpy.data.images.new('label_JUG', out.shape[1], out.shape[0], alpha=False)
        img.pixels.foreach_set(out.ravel())
        img.filepath_raw = path
        img.file_format = 'PNG'
        img.save()
        bpy.data.images.remove(img)
    return label_mat('JUG')

def moulded_jug(name, e, liquid, label, milk='#efe6e8'):
    """A whole jug on the anchor e: shell, front label, neck, tamper band and cap."""
    me = jug_mesh(name, liquid, milk)
    me.materials.append(jug_mat())
    body = bpy.data.objects.new(name + ' body', me)
    body.parent = e
    COL.objects.link(body)
    jug_label(name + ' label', me, label, e, 0.100, 0.1058, 0.042)
    # the shell is see-through, so the neck stops at the shoulder instead of plugging into it
    cyl(name + ' neck', 0.016, 0.016, 0.009, M['white'], e, JUG_CAP_X, 0.208, 0, seg=24, bevel=0)
    cyl(name + ' cap band', 0.0195, 0.0195, 0.005, M['white'], e, JUG_CAP_X, 0.2085, 0, seg=32, bevel=0.0008)
    ribbed_cap(name + ' cap', 0.019, 0.023, M['white'], e, JUG_CAP_X, 0.2235, 0)

use('Products')
moulded_jug('XC jug', xc, '#f178b6', label_mat('XC64oz'))     # hot pink; set by eye in the browser, where the shelf light is warm and dim
use('Live props')        # see-through, so lit live like the products rather than baked
for name, z_, ry, liquid in (('Jug orange', 0.35, PI / 2, '#f5c79a'), ('Jug clear', 0.55, PI / 2 + 0.2, '#dfe7e8')):
    moulded_jug(name, empty(name, shelves, 0.14, 1.6375, z_, ry), liquid, brand_label(), milk='#eeebe4')

# ---------------------------------------------------------------- retail boxes
# A product shown in its shop packaging: a plain carton with the flat print artwork (the
# die line, all panels on one sheet) folded onto it. Each face takes its own rectangle of
# the sheet, given in the artwork's pixels as (left, top, right, bottom).
def carton(name, e, art, sheet, panels, w, h, d, inset=2):
    """w across the front, h tall, d front to back (metres). panels: front, right, back,
    left, top, bottom. Left and right are as seen when facing the front."""
    sw, sh = sheet
    x0, x1, z0, z1 = -w / 2, w / 2, -d / 2, d / 2
    corners = {   # each face anticlockwise seen from outside, starting bottom-left as printed
        'front': ((x0, 0, z1), (x1, 0, z1), (x1, h, z1), (x0, h, z1)),
        'right': ((x1, 0, z1), (x1, 0, z0), (x1, h, z0), (x1, h, z1)),
        'back': ((x1, 0, z0), (x0, 0, z0), (x0, h, z0), (x1, h, z0)),
        'left': ((x0, 0, z0), (x0, 0, z1), (x0, h, z1), (x0, h, z0)),
        'top': ((x0, h, z1), (x1, h, z1), (x1, h, z0), (x0, h, z0)),
        'bottom': ((x0, 0, z0), (x1, 0, z0), (x1, 0, z1), (x0, 0, z1)),
    }
    bm = bmesh.new()
    uv = bm.loops.layers.uv.new()
    for face, quad in corners.items():
        l, t, r, b = panels[face]
        l, t, r, b = l + inset, t + inset, r - inset, b - inset      # stay clear of the fold lines and the next panel
        f = bm.faces.new([bm.verts.new(V(*c)) for c in quad])
        for loop, (px, py) in zip(f.loops, ((l, b), (r, b), (r, t), (l, t))):
            loop[uv].uv = (px / sw, 1 - py / sh)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    m, nt, bsdf = new_mat(f'Carton {name}')
    img = bpy.data.images.load(art, check_existing=True)
    img.pack()
    tex = nt.nodes.new('ShaderNodeTexImage')
    tex.image = img
    nt.links.new(tex.outputs['Color'], bsdf.inputs['Base Color'])
    bsdf.inputs['Roughness'].default_value = 0.38       # laminated print: a soft sheen, not a mirror
    bsdf.inputs['Coat Weight'].default_value = 0.25
    return finish(name, bm, m, e, (0, 0, 0))

use('Products')
# Panel edges are read off each die line (fold lines in the artwork's pixels). The folded
# height is the one measurement supplied; it fixes the millimetres per pixel, and the width
# and depth follow from the folds. F11's bottom flaps are shorter than the box is deep, so
# its bottom panel is only part-covered - it is the face nobody sees.
BOXES = [   # anchor, artwork name, sheet size, fold columns (4 panels), top fold rows, bottom rows, w, h, d
    (jd, 'JD Jet Car Blower', (2500, 1641), (8, 385, 1227, 1603, 2446), (70, 446), (1198, 1574), 0.291, 0.260, 0.130),
    (f11, 'F11 Dual Chamber Foam Cannon', (2500, 1567), (9, 456, 1216, 1663, 2423), (82, 526), (1256, 1553), 0.195, 0.187, 0.1145),
    (f5, 'f5 Foam Master', (2500, 2145), (11, 609, 1207, 1805, 2397), (93, 691), (1742, 2138), 0.186, 0.327, 0.186),
]
for anchor, art_name, sheet, (xa, xf, xr, xb, xe), (yt0, yt1), (yb0, yb1), bw, bh, bd in BOXES:
    art = sorted(glob.glob(os.path.join(HERE, '2D assets', art_name + 'Asset*.png')))
    label = art_name.split(' ')[0].upper() + ' box'
    if art:
        carton(label, anchor, art[0], sheet, dict(
            left=(xa, yt1, xf, yb0), front=(xf, yt1, xr, yb0), right=(xr, yt1, xb, yb0), back=(xb, yt1, xe, yb0),
            top=(xf, yt0, xr, yt1), bottom=(xf, yb0, xr, yb1)), bw, bh, bd)
    else:
        box(label, bw, bh, bd, M['sign'], anchor, 0, bh / 2, 0, bevel=0.002)      # no artwork here: plain black carton

# ---------------------------------------------------------------- architect's lamp on the tool chest
# A black swing-arm desk lamp aimed at the boxed blower. Lit live like the products (so it
# shows without a rebake); 'Lamp spot' and 'Lamp aim' tell the browser where its light sits
# and points, and the Blender spot below gives the baked chest top and wall the same pool.
def rod(name, a, b, r, mat, parent, seg=12, r2=None):
    """Round bar from a to b (viewer coords relative to the parent); r2 = radius at b."""
    a, b = Vector(a), Vector(b)
    d = Vector(V(*b)) - Vector(V(*a))
    mid = (a + b) / 2
    return cyl(name, r if r2 is None else r2, r, d.length, mat, parent, mid.x, mid.y, mid.z,
               seg=seg, bevel=0, rot=d.to_track_quat('Z', 'Y').to_euler())

def arch_lamp(parent, base, elbow, head, target):
    blk, spring_mat = M['black_gloss'], M['dark_steel']
    bx, by, bz = base
    cyl('Lamp base', 0.078, 0.086, 0.016, blk, parent, bx, by + 0.008, bz, seg=40, bevel=0.003)
    foot = Vector((bx, by + 0.075, bz))
    rod('Lamp post', (bx, by + 0.016, bz), foot, 0.007, blk, parent)
    elbow, head, target = Vector(elbow), Vector(head), Vector(target)
    def arm(tag, a, b, spring):
        along = (b - a).normalized()
        side = along.cross(Vector((0, 1, 0))).normalized() * 0.011
        for k, sgn in enumerate((-1, 1)):
            rod(f'Lamp {tag} bar {k}', a + side * sgn, b + side * sgn, 0.0035, blk, parent, seg=8)
        lift = side.cross(along).normalized() * 0.013
        rod(f'Lamp {tag} spring', a.lerp(b, spring[0]) + lift, a.lerp(b, spring[1]) + lift, 0.0045, spring_mat, parent, seg=8)
    arm('lower', foot, elbow, (0.04, 0.42))
    arm('upper', elbow, head, (0.02, 0.38))
    for tag, p in (('foot', foot), ('elbow', elbow), ('head', head)):
        sphere(f'Lamp joint {tag}', 0.014, blk, parent, p.x, p.y, p.z)
    aim = (target - head).normalized()
    neck = head + aim * 0.03
    cap = neck + aim * 0.045
    mouth = cap + aim * 0.085
    rod('Lamp neck', head, neck, 0.011, blk, parent)
    rod('Lamp shade cap', neck, cap, 0.028, blk, parent, seg=28)
    rod('Lamp shade', cap, mouth, 0.028, blk, parent, seg=40, r2=0.078)
    rod('Lamp glow', mouth - aim * 0.004, mouth + aim * 0.0006, 0.069, M['bulb'], parent, seg=32)
    empty('Lamp spot', parent, mouth.x, mouth.y, mouth.z)
    empty('Lamp aim', parent, target.x, target.y, target.z)
    prev = COL
    use('Lights')
    ld = bpy.data.lights.new('Lamp light', 'SPOT')
    ld.energy = 8.96
    ld.color = (1.0, 0.85, 0.68)
    ld.spot_size = math.radians(100)
    ld.spot_blend = 0.45
    ld.shadow_soft_size = 0.03
    lo = bpy.data.objects.new('Lamp light', ld)
    lo.location = V(*(mouth + aim * 0.01))
    lo.rotation_euler = Vector(V(*aim)).to_track_quat('-Z', 'Y').to_euler()
    lo.parent = parent
    COL.objects.link(lo)
    globals()['COL'] = prev

use('Live props')
# chest-top coordinates: the lamp stands to the left of the box (seen from the room) and
# reaches forward past the front edge, so its light falls across the front and the left side
arch_lamp(chest, base=(-0.02, 0.93, -0.3254), elbow=(0.0, 1.32, -0.4454), head=(-0.32, 1.44, -0.1654), target=(-0.083, 1.06, 0.116))

# ---------------------------------------------------------------- sun, sky, cameras
use('Lights')
sun_dir = V(math.sin(SUN_AZIM) * math.cos(SUN_ELEV), math.sin(SUN_ELEV), math.cos(SUN_AZIM) * math.cos(SUN_ELEV))
sd = bpy.data.lights.new('Sun', 'SUN')
sd.energy = 7.0
sd.color = (1.0, 0.83, 0.62)
sd.angle = math.radians(0.6)        # near-real sun size -> crisp shadow edges
so = bpy.data.objects.new('Sun', sd)
so.location = V(0, 6, 9)
so.rotation_euler = sun_dir.to_track_quat('Z', 'Y').to_euler()
COL.objects.link(so)

world = bpy.data.worlds.new('Sky')
try:
    world.use_nodes = True
except Exception:
    pass
bg = next(n for n in world.node_tree.nodes if n.bl_idname == 'ShaderNodeBackground')
bg.inputs['Color'].default_value = (0.42, 0.62, 1.0, 1.0)
bg.inputs['Strength'].default_value = 1.6
scene.world = world

use('Cameras')
def camera(name, pos, yaw, pitch, lens=16.0):
    cd = bpy.data.cameras.new(name)
    cd.lens = lens
    cd.sensor_width = 36
    cd.clip_start = 0.05
    co = bpy.data.objects.new(name, cd)
    co.location = V(*pos)
    cp = math.cos(pitch)
    fwd = V(math.sin(yaw) * cp, math.sin(pitch), -math.cos(yaw) * cp)
    co.rotation_euler = (-fwd).to_track_quat('Z', 'Y').to_euler()
    COL.objects.link(co)
    return co
cams = {
    'spawn': camera('Cam spawn', (0.3, EYE, 0.5), -0.3, -0.08),
    'bench': camera('Cam bench', (-0.2, EYE, -1.35), -0.1, -0.1),
    'door': camera('Cam door view', (-0.6, EYE, -1.6), 2.75, -0.06),
    # close-ups for checking details
    'yard': camera('Cam overview yard', (11.0, 27.0, -13.0), -2.72, -0.76),
    'rightwall': camera('Cam detail right wall', (-0.6, 1.4, 0.3), 1.249, -0.24, 20),
    'reel': camera('Cam detail reel', (-0.9, 1.3, 2.3), -1.04, -0.1, 50),
    'tyres': camera('Cam detail tyres', (0.8, 1.4, 0.8), -0.785, -0.25, 22),
    'cart': camera('Cam detail cart', (0.3, 1.25, 1.6), -1.89, -0.28, 24),
    'stool': camera('Cam detail stool', (0.3, 1.1, -0.9), 0.636, -0.455, 30),
    'sign': camera('Cam detail sign', (0.2, 1.65, 0.75), PI / 2, 0.09, 24),
    'bottles': camera('Cam detail bottles', (0.5, 1.78, -2.55), 0.0, -0.14, 35),
    'brushes': camera('Cam detail brushes', (-0.73, 1.74, -2.3), 0.0, 0.0, 35),
    'mitt': camera('Cam detail mitt', (-1.35, 1.4, -2.2), -0.63, -0.08, 40),
    'tirebrush': camera('Cam detail tire brush', (-0.62, 1.74, -2.85), 0.34, -0.12, 50),
    'box': camera('Cam detail box', (0.7, 1.45, -0.95), 1.499, -0.21, 32),
    'corner': camera('Cam back-left corner', (-1.95, 1.65, -2.45), 2.730, -0.055),
    'cornerin': camera('Cam door-side corner looking in', (-2.05, 1.65, 3.05), 0.413, -0.068),
    'outside': camera('Cam driveway looking in', (0.0, 1.6, 6.6), 0.0, -0.03, 20),
    'boxes': camera('Cam detail bench boxes', (-1.05, 1.35, -1.95), 0.0, -0.2, 30),
    'towels': camera('Cam detail towels', (-0.2, 1.22, -2.5), 0.0, -0.5, 40),
    'jug': camera('Cam detail jug', (-HW + 0.9, 1.36, -0.80), -1.453, -0.065, 40),
    'jugside': camera('Cam detail jug side', (-HW + 0.6, 1.36, -0.35), -0.705, -0.07, 40),
}
scene.camera = cams['spawn']

# ---------------------------------------------------------------- render settings
scene.render.engine = 'CYCLES'
scene.cycles.samples = 160
scene.cycles.use_denoising = True
scene.cycles.max_bounces = 10
scene.render.resolution_x, scene.render.resolution_y = 1600, 800
try:
    scene.view_settings.view_transform = 'AgX'
    scene.view_settings.look = 'AgX - Medium High Contrast'
except Exception as e:
    print('view transform:', e)
scene.view_settings.exposure = 1.6

# ---------------------------------------------------------------- slots manifest + save
bpy.context.view_layer.update()
manifest = []
for e in SLOTS:
    p = e.matrix_world.translation
    manifest.append({
        'slot': e['slot'], 'kind': e['kind'], 'object': e.name,
        'position': [round(p.x, 4), round(p.z, 4), round(-p.y, 4)],     # back to viewer coords
        'rotationY': round(e.matrix_world.to_euler().z, 4),
        'hotspotOffsetY': round(e['hotspot_dy'], 3),
        'model': '12oz-spray-bottle.obj' if e['kind'] == 'spray_bottle' else None,
    })
with open(os.path.join(HERE, 'slots.json'), 'w') as f:
    json.dump(manifest, f, indent=2)

blend_path = os.path.join(HERE, 'garage.blend')
bpy.ops.wm.save_as_mainfile(filepath=blend_path)
print(f'SAVED {blend_path}  objects={len(bpy.data.objects)}  slots={len(SLOTS)}')

if '--render' in ARGS:
    try:
        prefs = bpy.context.preferences.addons['cycles'].preferences
        prefs.compute_device_type = 'OPTIX'
        prefs.get_devices()
        for d in prefs.devices:
            d.use = d.type == 'OPTIX'
        scene.cycles.device = 'GPU'
    except Exception as e:
        print('GPU setup failed, using CPU:', e)
    only = [a.split('=')[1] for a in ARGS if a.startswith('--cam=')]
    for a in ARGS:
        if a.startswith('--samples='):
            scene.cycles.samples = int(a.split('=')[1])
        if a.startswith('--scale='):
            scene.render.resolution_percentage = int(a.split('=')[1])
    for key, cam in cams.items():
        if only and key not in only:
            continue
        scene.camera = cam
        scene.render.filepath = os.path.join(HERE, 'renders', f'preview_{key}.png')
        bpy.ops.render.render(write_still=True)
        print('RENDERED', scene.render.filepath)
    scene.camera = cams['spawn']
