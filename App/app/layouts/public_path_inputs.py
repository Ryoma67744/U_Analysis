"""Host-facing inputs backed by unchanged internal Dash component IDs."""
from copy import deepcopy

import dash_bootstrap_components as dbc
from dash import html
from dash.development.base_component import Component

from app.services.public_paths import has_mappings, to_display_path


PATH_INPUT_IDS = frozenset({
    "data_folder", "annotation_path", "rds_folder", "rds_path",
    "rds_folder_reanalysis", "resume_reanalysis_dir", "reanalysis_data_folder",
    "reanalysis_annotation_path", "output_dir", "interactive_result_folder",
    "interactive_msi_folder", "desi_v8_script_path", "desi_cluster_filter_script_path",
    "tims_v8_script_path", "tims_cluster_filter_script_path", "default_desi_data_folder",
    "default_annotation_file", "default_desi_output_dir", "default_tims_data_folder",
    "default_annotation_csv", "default_tims_output_dir", "default_output_dir",
    "int_cal_annotation_path", "reann_annotation_path", "restore_scan_folder",
    "dm_move_src", "dm_move_dest_path", "edit_project_thumbnail", "scils_input_folder",
    "scils_output_folder", "env_tims_data_dir", "env_desi_data_dir", "env_r_home",
    "rds_maint_folder", "parquet_maint_folder", "imzml_source", "imzml_target",
    "imzml_batch_folder", "new_sub_data_folder", "new_sub_output_dir",
    "edit_sub_data_folder", "edit_sub_output_dir",
})
_WRAPPER_CLASS = "public-path-input"


def display_id(component_id):
    if isinstance(component_id, str) and component_id in PATH_INPUT_IDS:
        return component_id + "_display"
    if isinstance(component_id, dict) and component_id.get("type") == "extra_folder_path":
        return {**component_id, "type": "extra_folder_path_display"}
    return None


def wrap_path_input(component):
    """Keep every original value/ID; only the visible clone uses host aliases."""
    if not has_mappings() or not isinstance(component, dbc.Input):
        return component
    visible_id = display_id(getattr(component, "id", None))
    if visible_id is None:
        return component
    visible = deepcopy(component)
    visible.id = visible_id
    visible.value = to_display_path(getattr(component, "value", None))
    # Restoring a separately persisted display value could overwrite a project
    # loaded through its canonical ID. Persistence belongs to the original.
    visible.persistence = False
    component.style = {**getattr(component, "style", {}), "display": "none"}
    component.tabIndex = -1
    for property_name in ("autoFocus", "autofocus"):
        if hasattr(component, property_name):
            setattr(component, property_name, False)
    return html.Div([component, visible], className=_WRAPPER_CLASS,
                    style={"display": "flex", "flex": "1", "minWidth": 0})


def wrap_public_path_inputs(layout):
    """Mapping disabled: preserve the exact old DOM and callback graph."""
    if not has_mappings():
        return layout
    if isinstance(layout, (list, tuple)):
        return [wrap_public_path_inputs(child) for child in layout]
    if not isinstance(layout, Component):
        return layout
    if _WRAPPER_CLASS in str(getattr(layout, "className", "")).split():
        return layout
    wrapped = wrap_path_input(layout)
    if wrapped is not layout:
        return wrapped
    for property_name in ("htmlFor", "html_for", "target"):
        original_target = getattr(layout, property_name, None)
        if isinstance(original_target, str) and original_target in PATH_INPUT_IDS:
            setattr(layout, property_name, display_id(original_target))
    if hasattr(layout, "children"):
        layout.children = wrap_public_path_inputs(layout.children)
    return layout


def wrapped_input_ids(layout):
    """Collect only pairs present in this layout (no nonexistent Input IDs)."""
    if isinstance(layout, (list, tuple)):
        return [item for child in layout for item in wrapped_input_ids(child)]
    if not isinstance(layout, Component):
        return []
    if _WRAPPER_CLASS in str(getattr(layout, "className", "")).split():
        return [layout.children[0].id]
    return wrapped_input_ids(getattr(layout, "children", None))
