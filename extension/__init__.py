"""CR Bevel: constant-radius bevel as a Geometry Nodes modifier.

Adds "CR Bevel" to Add Modifier > Generate. The node group is appended from the
bundled cr_bevel_library.blend the first time it is used in a file; later uses
reuse the group already in the file (so edits to it are kept).
"""
import os

import bpy

GROUP = "CR Bevel"
LIBRARY = os.path.join(os.path.dirname(__file__), "cr_bevel_library.blend")


def ensure_group():
    """Return the CR Bevel node group of this file, appending it if needed."""
    ng = bpy.data.node_groups.get(GROUP)
    if ng is None or ng.bl_idname != "GeometryNodeTree":
        with bpy.data.libraries.load(LIBRARY, link=False) as (src, dst):
            dst.node_groups = [GROUP]
        ng = dst.node_groups[0]
    return ng


class OBJECT_OT_cr_bevel_add(bpy.types.Operator):
    """Add a constant-radius bevel modifier to the selected meshes"""

    bl_idname = "object.cr_bevel_add"
    bl_label = "CR Bevel"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return any(ob.type == "MESH" for ob in context.selected_objects)

    def execute(self, context):
        meshes = [ob for ob in context.selected_objects if ob.type == "MESH"]
        editable = [ob for ob in meshes if ob.library is None]   # linked objects can't take modifiers
        if not editable:
            self.report({"WARNING"}, "Only linked objects selected: make them local first")
            return {"CANCELLED"}
        ng = ensure_group()
        for ob in editable:
            mod = ob.modifiers.new(GROUP, "NODES")
            mod.node_group = ng
        skipped = len(meshes) - len(editable)
        note = f" ({skipped} linked object(s) skipped)" if skipped else ""
        self.report({"INFO"}, f"CR Bevel added to {len(editable)} object(s){note}")
        return {"FINISHED"}


def menu_entry(self, context):
    self.layout.operator(OBJECT_OT_cr_bevel_add.bl_idname, icon="MOD_BEVEL")


def _menu():
    # Add Modifier > Generate; fall back to the top-level Add Modifier menu.
    return getattr(bpy.types, "OBJECT_MT_modifier_add_generate", None) or bpy.types.OBJECT_MT_modifier_add


def register():
    bpy.utils.register_class(OBJECT_OT_cr_bevel_add)
    _menu().append(menu_entry)


def unregister():
    _menu().remove(menu_entry)
    bpy.utils.unregister_class(OBJECT_OT_cr_bevel_add)
