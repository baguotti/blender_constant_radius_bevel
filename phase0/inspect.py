# SPDX-License-Identifier: CC0-1.0
import bpy
names=[n for n in dir(bpy.types) if 'Bevel' in n and n.startswith('GeometryNode')]
print("TYPES",names)
ng=bpy.data.node_groups.new("t","GeometryNodeTree")
n=ng.nodes.new(names[0])
print("PROPS",[p.identifier for p in n.bl_rna.properties if not p.is_readonly or p.identifier in()][-12:])
for s in n.inputs: print("IN",s.identifier,s.name,s.type,getattr(s,'default_value',None) if not hasattr(getattr(s,'default_value',None),'__len__') else tuple(s.default_value))
for s in n.outputs: print("OUT",s.identifier,s.name,s.type)
for p in n.bl_rna.properties:
    if p.type=='ENUM' and p.identifier not in('bl_icon','bl_static_type','warning_propagation','color_tag'): print("ENUM",p.identifier,[i.identifier for i in p.enum_items], getattr(n,p.identifier))
