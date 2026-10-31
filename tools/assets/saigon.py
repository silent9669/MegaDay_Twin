"""Original District 1 study. Run in Blender 5.x; preserves other scenes.
Exports a web-ready architectural kit, including a shared baked AO atlas.
"""
import bpy, math, os
from mathutils import Vector
ROOT = os.environ.get('MEGADAY_ROOT', '/Users/phucdang/Documents/MegaDay_Twin')
OUT = ROOT + '/web/public/assets/saigon'
scene = bpy.data.scenes.get('MegaDay District 1') or bpy.data.scenes.new('MegaDay District 1')
bpy.context.window.scene = scene
for obj in list(scene.objects):
    bpy.data.objects.remove(obj, do_unlink=True)
materials = {}
colors = {'ivory':'e7dfc9','ochre':'cfaa66','terracotta':'ac644b','forest':'3c6258','glass':'6e9693','stone':'b6b9a8','iron':'455853','plaster':'d6cab5','sage':'91a68a','gold':'bca06b','wood':'87664d','cream':'f5edda'}
def material(name):
    if name in materials: return materials[name]
    m = bpy.data.materials.new('Saigon '+name); m.use_nodes=True
    rgb=tuple(int(colors[name][i:i+2],16)/255 for i in (0,2,4))
    bsdf=next(n for n in m.node_tree.nodes if n.type=='BSDF_PRINCIPLED')
    # Convert display sRGB to linear for glTF PBR base colour.
    bsdf.inputs['Base Color'].default_value=tuple(((v+.055)/1.055)**2.4 if v>.04045 else v/12.92 for v in rgb)+(1,)
    bsdf.inputs['Roughness'].default_value=.38 if name=='glass' else .82
    bsdf.inputs['Metallic'].default_value=.25 if name=='glass' else 0
    m.diffuse_color=rgb+(1,);materials[name]=m;return m
objects=[]
def finish(obj,name,mat):
    obj.name=name;obj.data.materials.append(material(mat));objects.append(obj);return obj
# Coordinates use Three.js x,y,z; Blender Z is up, -Y is forward.
def box(name,x,y,z,w,h,d,mat,bevel=0):
    bpy.ops.mesh.primitive_cube_add(size=1,location=(x,-z,y));o=bpy.context.object;o.scale=(w,d,h)
    bpy.ops.object.transform_apply(location=False,rotation=False,scale=True)
    if bevel:
        mod=o.modifiers.new('Soft masonry edges','BEVEL');mod.width=bevel;mod.segments=1
        bpy.ops.object.modifier_apply(modifier=mod.name)
    return finish(o,name,mat)
def cyl(name,x,y,z,r,h,mat,vertices=16,r2=None):
    bpy.ops.mesh.primitive_cone_add(vertices=vertices,radius1=r,radius2=r if r2 is None else r2,depth=h,location=(x,-z,y))
    return finish(bpy.context.object,name,mat)
def roof(name,x,y,z,w,h,d):
    verts=[(x-w/2,-z-d/2,y),(x+w/2,-z-d/2,y),(x+w/2,-z+d/2,y),(x-w/2,-z+d/2,y),(x,-z-d/2,y+h),(x,-z+d/2,y+h)]
    mesh=bpy.data.meshes.new(name);mesh.from_pydata(verts,[],[(0,1,4),(3,5,2),(0,4,5,3),(1,2,5,4),(0,3,2,1)]);mesh.update();o=bpy.data.objects.new(name,mesh);scene.collection.objects.link(o);return finish(o,name,'terracotta')
def arch(name,x,y,z,w,h,depth,mat):
    # Filled recessed arch, segmented semicircular crown, facing south.
    outline=[(-w/2,0),(w/2,0)]+[(math.cos(a)*w/2,h-w/2+math.sin(a)*w/2) for a in [i*math.pi/12 for i in range(13)]]
    vs=[(x+u,-z+v,y+t) for v in [-depth/2,depth/2] for u,t in outline];n=len(outline)
    faces=[tuple(range(n-1,-1,-1)),tuple(range(n,n*2))]+[(i,(i+1)%n,(i+1)%n+n,i+n) for i in range(n)]
    mesh=bpy.data.meshes.new(name);mesh.from_pydata(vs,[],faces);mesh.update();o=bpy.data.objects.new(name,mesh);scene.collection.objects.link(o);return finish(o,name,mat)
def sign(text,x,y,z,size):
    curve=bpy.data.curves.new(text,'FONT');curve.body=text;curve.align_x='CENTER';curve.size=size;curve.extrude=.006
    o=bpy.data.objects.new(text,curve);scene.collection.objects.link(o);o.location=(x,-z,y);o.rotation_euler=(math.pi/2,0,0)
    bpy.ops.object.select_all(action='DESELECT');o.select_set(True);bpy.context.view_layer.objects.active=o;bpy.ops.object.convert(target='MESH');finish(bpy.context.object,text,'forest')
# Bitexco-inspired tapered tower, HQ at the centre. Architectural study, not survey geometry.
for floor in range(25):
    y=.7+floor*.8;r=3.0-(floor/25)**2*1.35
    o=cyl('Bitexco glass',0,y+.36,-3,r,.76,'glass',32);o.scale.y=.72
    o=cyl('Bitexco floor band',0,y+.76,-3,r+.05,.06,'ivory',32);o.scale.y=.72
for a in range(0,360,30):
    ang=math.radians(a)
    for f in range(0,24,3):
        r=3-(f/25)**2*1.35
        box('Tower mullion',math.cos(ang)*r,.7+f*.8+1.2,-3+math.sin(ang)*r*.72,.05,2.4,.05,'iron')
cyl('Helipad cantilever',3.1,15.8,-2.3,2.5,.18,'stone',32)
cyl('Helipad landing circle',3.1,15.91,-2.3,1.8,.025,'gold',32)
sign('H',3.1,16,-.5,.8)
box('HQ podium',0,.65,-3,7,.7,6,'ivory',.1)
# Bitexco is a separate riverside landmark. HQ is a modest independent office.
for o in objects:
    o.location.x += 40
    o.location.y += 16
box('MegaDay Twin office',0,2.7,-3,7,4.8,5.5,'ivory',.08)
box('HQ flat roof',0,5.2,-3,7.6,.25,6.1,'forest')
for x in [-2.4,-.8,.8,2.4]:
    for y in [1.5,3.8]:box('HQ window',x,y,-.2,1.05,1.3,.1,'glass')
box('HQ sign board',0,5.75,-.15,7,.85,.2,'ivory')
sign('MEGADAY TWIN',0,5.52,0,.55)
box('HQ entry',0,1.2,-.06,1.1,2.1,.1,'forest')
for x in [-3,3]:box('HQ planter',x,.6,.7,1.1,.7,1.1,'stone')
# Ben Thanh market in the southeast, clock tower and clay-tiled roofs.
for dx in [-5.6,5.6]:
    box('Market hall',20+dx,2.4,16,8.3,4.2,8,'ochre',.06)
    roof('Market tile roof',20+dx,4.6,16,9.2,1.9,9)
    for j in [-2.7,0,2.7]:
        arch('Market arcade',20+dx+j,.55,20.06,1.9,3.1,.05,'forest')
        box('Market cornice',20+dx,4.3,20.1,8.6,.22,.3,'ivory')
box('Ben Thanh clock tower',20,4.2,20,3.6,7.4,3.5,'ochre',.06)
box('Tower cornice',20,7.7,20,4.3,.3,4.2,'ivory')
roof('Tower hip silhouette',20,7.9,20,4.4,1.9,4.2)
arch('Market entrance',20,.5,21.78,2.1,3.7,.06,'forest')
clock=cyl('Clock face',20,6.15,21.82,1,.07,'cream',32);clock.rotation_euler.x=math.pi/2
box('Clock minute hand',20,6.5,21.89,.06,.7,.04,'forest');box('Clock hour hand',20.22,6.15,21.89,.5,.06,.04,'forest')
for a in range(12):
    ang=a*math.pi/6;box('Clock markers',20+math.sin(ang)*.8,6.15+math.cos(ang)*.8,21.9,.07,.09,.03,'forest')
sign('CHO BEN THANH',20,4.55,21.85,.31)
# Central Post Office on northwestern plot; symmetrical pilasters and arched windows.
box('Post Office',-20,3.1,-21,17,5.5,7,'ochre',.06)
roof('Post Office roof',-20,5.9,-21,17.5,2.1,7.6)
for i in range(9):
    x=-27.4+i*1.85
    arch('Post Office window',x,1,-17.45,1.15,3.6,.09,'forest')
    box('Post Office pilaster',x+.83,3.1,-17.3,.28,5.5,.3,'ivory')
box('Post Office cornice',-20,5.7,-17.2,17.8,.38,.5,'cream')
box('Post Office pediment',-20,6.5,-17.2,5.4,1.2,.45,'ivory')
sign('BUU DIEN',-20,6.4,-16.93,.47)
# Fulfilment warehouse: corrugated canopy, open loading bay, racks and packing tables.
box('Warehouse slab',-20,.55,18,17,.5,10,'stone',.08)
box('Warehouse back',-20,3.6,13.3,17,6,.3,'sage')
box('Warehouse side',-28.4,3.6,18,.3,6,9.7,'sage')
for x in [-28,-24,-20,-16,-12]: box('Warehouse frame',x,3.6,21.5,.22,6.2,.22,'iron')
roof('Warehouse canopy',-20,6.7,16.2,18,1.2,6.9)
for j in range(32):box('Roof seam',-28.7+j*.56,6.77,18.9,.05,.08,1.4,'terracotta')
for rack in range(4):
    x=-26+rack*4
    for dx in [-1.5,1.5]:box('Rack post',x+dx,2.7,17.6,.12,4.8,2.8,'iron')
    for level in range(3):box('Rack shelf',x,1.05+level*1.6,18,3.4,.12,3,'wood')
# Shophouses: narrow frontages, deep plans, balconies, shutters, striped cafe awnings.
for x,z,i in [(-26,-10,0),(-20,-10,1),(-14,-10,2),(15,0,3),(21,0,4),(27,0,5),(-20,-28,6),(-14,-28,7),(-44,-22,8),(-44,-15,9),(-44,14,10),(-44,21,11),
                  (14,-41,12),(20,-41,13),(26,-41,14),(38,12,15),(38,19,16),
                  (73,-32,17),(79,-32,18),(73,26,19),(79,26,20)]:
    h=5.8+(i%3)*1.2;mat=['plaster','sage','ochre'][i%3]
    box('Shophouse plaster',x,h/2+.4,z,4.7,h,3.5,mat,.04)
    roof('Shophouse tiles',x,h+.4,z,5.1,.9,3.9)
    box('Shopfront glazing',x,1.6,z+1.78,3.8,2.4,.1,'forest')
    for floor in range(2):
        fy=3.5+floor*1.8
        for dx in [-1.1,1.1]:
            box('Shutters',x+dx,fy,z+1.8,.9,1.25,.13,'forest')
            box('Window reveal',x+dx,fy+.72,z+1.86,1.1,.12,.2,'ivory')
        box('Balcony slab',x,fy-.8,z+2.05,4.5,.16,.8,'ivory')
        box('Balcony rail',x,fy-.15,z+2.45,4.4,.07,.07,'iron')
        for dx in range(13):box('Balcony spindle',x-2+dx/3,fy-.45,z+2.45,.035,.6,.035,'iron')
    for stripe in range(8):box('Cafe awning',x-2.1+stripe*.6,2.9,z+2.25,.6,.12,1.3,'ivory' if stripe%2 else 'forest')
    sign(['CA PHE','TIEM BANH','SAIGON'][i%3],x,2.55,z+2.94,.28)
    for dx in [-1,1]:
        cyl('Cafe table',x+dx,.95,z+3.1,.38,.1,'wood');cyl('Table stem',x+dx,.6,z+3.1,.06,.7,'iron')
# Lamps, benches, planters: repeatable small streetscape assets.
for x,z in [(-10,-25),(-10,4),(11,5),(11,26),(-30,9),(30,-8),(4,11),(-4,23)]:
    cyl('Streetlamp',x,2.3,z,.09,4.4,'iron',8);box('Lamp arm',x+.35,4.45,z,.8,.1,.1,'iron');box('Lamp glass',x+.7,4.35,z,.42,.24,.32,'cream',.03)
for x,z in [(-4,15),(4,20),(-4,20),(4,15)]:
    for i in range(4):box('Bench slat',x,.8,z+i*.14,1.8,.1,.1,'wood')
    for dx in [-.65,.65]:box('Bench foot',x+dx,.5,z+.2,.1,.6,.5,'iron')
# Riverfront retaining walls, promenade bollards and broad stairs.
for x in [47.5,65.5]:
    box('River quay',x,-.25,0,1.2,1.6,96,'stone',.06)
    for z in range(-44,45,4):cyl('Quay bollard',x,.7,z,.13,.8,'iron',8)
for i in range(5):box('Riverfront steps',44.5+i*.55,.5-i*.12,25,.6,.4,10,'stone')
# Join by shared material, then bake AO atlas once across the static kit.
bpy.ops.object.select_all(action='DESELECT')
for o in objects:o.select_set(True)
bpy.context.view_layer.objects.active=objects[0]
bpy.ops.object.join();city=bpy.context.object;city.name='District1_Architecture'
bpy.ops.object.transform_apply(location=True,rotation=True,scale=True)
bpy.ops.object.mode_set(mode='EDIT');bpy.ops.mesh.select_all(action='SELECT');bpy.ops.uv.smart_project(angle_limit=1.15,island_margin=.008);bpy.ops.object.mode_set(mode='OBJECT')
ao=bpy.data.images.new('District1 baked AO',width=2048,height=2048,alpha=False);ao.colorspace_settings.name='Non-Color'
for m in city.data.materials:
    nodes=m.node_tree.nodes;n=nodes.new('ShaderNodeTexImage');n.image=ao;nodes.active=n
scene.render.engine='CYCLES';scene.cycles.samples=8;scene.render.bake.margin=3
bpy.ops.object.bake(type='AO')
# glTF exporter recognises this node group's Occlusion input.
group=bpy.data.node_groups.new('glTF Material Output','ShaderNodeTree')
group.interface.new_socket(name='Occlusion',in_out='INPUT',socket_type='NodeSocketFloat')
for m in city.data.materials:
    nodes=m.node_tree.nodes;tex=next(n for n in nodes if n.type=='TEX_IMAGE' and n.image==ao);out=nodes.new('ShaderNodeGroup');out.node_tree=group;m.node_tree.links.new(tex.outputs['Color'],out.inputs['Occlusion'])
os.makedirs(OUT,exist_ok=True)
bpy.ops.export_scene.gltf(filepath=ROOT+'/tools/assets/district1.raw.glb',export_format='GLB',use_selection=True,use_active_scene=True,export_yup=True)
# Save only the dedicated scene and its dependencies; no changes to user's original project.
ao.pack()
bpy.data.libraries.write(ROOT+'/tools/assets/district1.blend',{scene},fake_user=True)
print('Exported',len(city.data.polygons),'faces,',len(city.data.materials),'materials;',os.path.getsize(ROOT+'/tools/assets/district1.raw.glb'),'bytes')
