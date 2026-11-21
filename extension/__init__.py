# SPDX-License-Identifier: GPL-3.0-or-later
"""CR Bevel: constant-radius bevel as a Geometry Nodes modifier.

Adds "CR Bevel" and "CR Bevel (Scene Units)" to Add Modifier > Generate. The node
group is appended from the bundled cr_bevel_library.blend the first time it is
used in a file; later uses reuse the group already in the file (so edits to it
are kept).

"CR Bevel (Scene Units)" measures the radius in scene units and keeps rounds
circular on scaled objects; it re-evaluates whenever the object moves, so it is
a separate group rather than an option.
"""
import os

import bpy

GROUP = "CR Bevel"
SCENE_GROUP = "CR Bevel (Scene Units)"
LIBRARY = os.path.join(os.path.dirname(__file__), "cr_bevel_library.blend")


def ensure_group(name=GROUP):
    """Return the node group `name` of this file, appending it if needed."""
    ng = bpy.data.node_groups.get(name)
    if ng is None or ng.bl_idname != "GeometryNodeTree":
        with bpy.data.libraries.load(LIBRARY, link=False) as (src, dst):
            dst.node_groups = [name]
        ng = dst.node_groups[0]
    return ng


class OBJECT_OT_cr_bevel_add(bpy.types.Operator):
    """Add a constant-radius bevel modifier to the selected meshes"""

    bl_idname = "object.cr_bevel_add"
    bl_label = "CR Bevel"
    bl_options = {"REGISTER", "UNDO"}

    scene_units: bpy.props.BoolProperty(
        name="Scene Units",
        description="Radius in scene units, circular rounds on scaled objects (re-evaluates when the object moves)",
        default=False)

    @classmethod
    def poll(cls, context):
        return any(ob.type == "MESH" for ob in context.selected_objects)

    def execute(self, context):
        meshes = [ob for ob in context.selected_objects if ob.type == "MESH"]
        editable = [ob for ob in meshes if ob.library is None]   # linked objects can't take modifiers
        if not editable:
            self.report({"WARNING"}, "Only linked objects selected: make them local first")
            return {"CANCELLED"}
        name = SCENE_GROUP if self.scene_units else GROUP
        ng = ensure_group(name)
        for ob in editable:
            mod = ob.modifiers.new(name, "NODES")
            mod.node_group = ng
        skipped = len(meshes) - len(editable)
        note = f" ({skipped} linked object(s) skipped)" if skipped else ""
        self.report({"INFO"}, f"{name} added to {len(editable)} object(s){note}")
        return {"FINISHED"}


def menu_entry(self, context):
    self.layout.operator(OBJECT_OT_cr_bevel_add.bl_idname, icon="MOD_BEVEL")
    op = self.layout.operator(OBJECT_OT_cr_bevel_add.bl_idname, text=SCENE_GROUP, icon="MOD_BEVEL")
    op.scene_units = True


def _menu():
    # Add Modifier > Generate; fall back to the top-level Add Modifier menu.
    return getattr(bpy.types, "OBJECT_MT_modifier_add_generate", None) or bpy.types.OBJECT_MT_modifier_add


def register():
    bpy.utils.register_class(OBJECT_OT_cr_bevel_add)
    _menu().append(menu_entry)


def unregister():
    _menu().remove(menu_entry)
    bpy.utils.unregister_class(OBJECT_OT_cr_bevel_add)
