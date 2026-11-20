import bpy
ng=bpy.data.node_groups.new("CR Bevel test","GeometryNodeTree")
print("has is_modifier:", hasattr(ng,"is_modifier"), "has is_tool:", hasattr(ng,"is_tool"))
ng.is_modifier=True
ng.asset_mark()
print("asset:", ng.asset_data is not None, "is_modifier:", ng.is_modifier)
