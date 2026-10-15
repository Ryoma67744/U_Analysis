"""Path aliases never relocate saved projects or rewrite their internal values."""
import json
import shutil
import subprocess

import dash_bootstrap_components as dbc
import pytest
from dash import html

from app import config
from app.services import public_paths as paths
from app.layouts import public_path_inputs as inputs
from app.callbacks import public_path_callbacks as callbacks


@pytest.fixture
def mappings(monkeypatch):
    for setting, environment in paths._ROOT_SETTINGS:
        monkeypatch.delenv(environment, raising=False)
    monkeypatch.setattr(config, "TIMS_DATA_DIR", "/app/Data/TIMS/Data")
    monkeypatch.setattr(config, "IMZML_CACHE_DIR", "/app/Data/Other/imzml_assets")
    monkeypatch.setenv("MSI_HOST_TIMS_DATA_DIR", "/srv/msi/tims_data")
    monkeypatch.setenv("MSI_HOST_IMZML_ASSETS_DIR", "/srv/msi/imzml_assets")
    return paths.get_path_mappings()


@pytest.mark.parametrize("internal,public", [
    ("/app/Data/TIMS/Data", "/srv/msi/tims_data"),
    ("/app/Data/TIMS/Data/apple/a.imzML", "/srv/msi/tims_data/apple/a.imzML"),
    ("/app/Data/Other/imzml_assets/new/file.parquet", "/srv/msi/imzml_assets/new/file.parquet"),
    ("/unmapped/old/project", "/unmapped/old/project"),
    ("/app/Data/TIMS/Database/a", "/app/Data/TIMS/Database/a"),
    ("relative/old/project", "relative/old/project"),
    ("", ""), (None, None),
])
def test_roundtrip_without_existence_requirement(mappings, internal, public):
    assert paths.to_display_path(internal) == public
    assert paths.to_internal_path(public) == internal
    assert paths.to_internal_path(internal) == internal


def test_normalize_before_matching(mappings):
    assert paths.to_internal_path("/srv/msi/tims_data/a/../b") == "/app/Data/TIMS/Data/b"
    assert paths.to_internal_path("/srv/msi/tims_data/../../other") == "/srv/msi/tims_data/../../other"
    assert paths.to_internal_path("/srv/msi/tims_data_backup/a") == "/srv/msi/tims_data_backup/a"
    assert paths.to_internal_path("/srv/msi/tims_data/a\x00b") == "/srv/msi/tims_data/a\x00b"


def test_nested_roots_use_longest_component_match(mappings, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DATA_DIR", "/app/Data/TIMS/Data/output")
    monkeypatch.setenv("MSI_HOST_OUTPUT_DATA_DIR", "/srv/results")
    assert paths.to_display_path("/app/Data/TIMS/Data/output/new") == "/srv/results/new"
    assert paths.to_internal_path("/srv/results/new") == "/app/Data/TIMS/Data/output/new"


@pytest.mark.parametrize("internal", [r"C:\Data\TIMS", r"\\server\share\TIMS"])
def test_windows_internal_and_posix_host(mappings, monkeypatch, internal):
    monkeypatch.setattr(config, "TIMS_DATA_DIR", internal)
    assert paths.to_display_path(internal.lower() + r"\apple\a") == "/srv/msi/tims_data/apple/a"
    assert paths.to_internal_path("/srv/msi/tims_data/apple/a") == internal + r"\apple\a"


def test_virtual_ancestors_expose_only_configured_branches(mappings):
    assert paths.virtual_children("/") == [{"name": "srv", "path": "/srv", "is_dir": True, "icon": "📁"}]
    assert paths.virtual_children("/srv") == [{"name": "msi", "path": "/srv/msi", "is_dir": True, "icon": "📁"}]
    assert [row["name"] for row in paths.virtual_children("/srv/msi")] == ["imzml_assets", "tims_data"]
    assert paths.virtual_children("/srv/msi/tims_data") is None
    assert paths.virtual_children("/srv/msi/tims_data/apple") is None
    assert paths.virtual_children("/srv/unrelated") is None


def test_empty_relative_and_conflicting_roots_are_not_activated(mappings, monkeypatch):
    monkeypatch.setenv("MSI_HOST_TIMS_DATA_DIR", "relative/root")
    monkeypatch.setenv("MSI_HOST_IMZML_ASSETS_DIR", "")
    assert not paths.has_mappings()
    monkeypatch.setenv("MSI_HOST_TIMS_DATA_DIR", "/srv/same")
    monkeypatch.setenv("MSI_HOST_IMZML_ASSETS_DIR", "/srv/same")
    assert not paths.has_mappings()


def test_disabled_mapping_preserves_original_dom_and_registers_nothing(mappings, monkeypatch):
    for _, environment in paths._ROOT_SETTINGS:
        monkeypatch.delenv(environment, raising=False)
    original = dbc.Input(id="data_folder", value="/old/project")
    layout = html.Div(original)
    assert inputs.wrap_public_path_inputs(layout) is layout
    assert layout.children is original
    assert not hasattr(original, "style")
    monkeypatch.setattr(callbacks, "clientside_callback", lambda *a, **k: pytest.fail("unexpected callback"))
    callbacks.register_public_path_callbacks(layout)


def test_wrapper_preserves_internal_id_value_and_visual_flags(mappings):
    original = dbc.Input(id="data_folder", value="/app/Data/TIMS/Data/apple", debounce=True,
                         invalid=True, disabled=True, readonly=True, style={"width": "90%"})
    wrapped = inputs.wrap_path_input(original)
    internal, display = wrapped.children
    assert internal is original and internal.id == "data_folder"
    assert internal.value == "/app/Data/TIMS/Data/apple"
    assert internal.style["display"] == "none" and internal.tabIndex == -1
    assert display.id == "data_folder_display" and display.value == "/srv/msi/tims_data/apple"
    assert display.invalid and display.disabled and display.readonly and display.debounce
    assert display.style == {"width": "90%"} and display.persistence is False
    assert inputs.wrap_public_path_inputs(wrapped) is wrapped
    assert inputs.wrapped_input_ids(wrapped) == ["data_folder"]


def test_dynamic_extra_folder_wraps_without_changing_store_identity(mappings):
    original = dbc.Input(id={"type": "extra_folder_path", "index": 3}, value="/app/Data/TIMS/Data/x")
    wrapped = inputs.wrap_path_input(original)
    assert wrapped.children[0].id == {"type": "extra_folder_path", "index": 3}
    assert wrapped.children[1].id == {"type": "extra_folder_path_display", "index": 3}
    assert wrapped.children[1].value == "/srv/msi/tims_data/x"


def test_label_tooltip_and_focus_follow_visible_input(mappings):
    layout = html.Div([dbc.Label("Data", html_for="data_folder"),
                       dbc.Input(id="data_folder", autoFocus=True),
                       dbc.Tooltip("path", target="data_folder")])
    wrapped = inputs.wrap_public_path_inputs(layout)
    assert wrapped.children[0].html_for == "data_folder_display"
    assert wrapped.children[1].children[0].autoFocus is False
    assert wrapped.children[1].children[1].autoFocus is True
    assert wrapped.children[2].target == "data_folder_display"


def test_registers_only_present_static_pairs_and_same_callback_value_cycle(mappings, monkeypatch):
    registrations = []
    monkeypatch.setattr(callbacks, "clientside_callback", lambda *args, **kwargs: registrations.append((args, kwargs)))
    layout = inputs.wrap_public_path_inputs(html.Div([dbc.Input(id="data_folder", value="x")]))
    callbacks.register_public_path_callbacks(layout)
    assert len(registrations) == 4  # one static pair and one dynamic MATCH pair; flags separate
    script, internal_out, display_out, display_in, internal_in = registrations[0][0]
    assert internal_out.component_id == internal_in.component_id == "data_folder"
    assert display_out.component_id == display_in.component_id == "data_folder_display"
    assert internal_out.allow_duplicate is True
    assert registrations[0][1]["prevent_initial_call"] is True
    outputs = registrations[1][0][1]
    assert {out.component_property for out in outputs} == {"invalid", "valid", "disabled", "readonly"}
    assert all(out.component_id == "data_folder_display" for out in outputs)


def test_main_layout_keeps_original_input_ids_unique(mappings):
    from app.layouts.main_layout import create_main_layout
    layout = create_main_layout()
    wrapped = inputs.wrapped_input_ids(layout)
    assert len(wrapped) == len(set(wrapped))
    assert {"data_folder", "imzml_batch_folder", "reanalysis_data_folder", "output_dir",
            "new_sub_data_folder", "edit_sub_output_dir", "imzml_target"} <= set(wrapped)


def _run_client(script, cases):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required to execute browser converter contract")
    code = "const f = (" + script + "); const cases = " + json.dumps(cases) + ";\n" + """
    const results = cases.map(c => {
      global.window = {dash_clientside: {no_update: '__NO_UPDATE__', callback_context: {
        triggered: c.trigger ? [{prop_id: c.trigger}] : []
      }}};
      return f(c.display, c.internal);
    }); process.stdout.write(JSON.stringify(results));
    """
    return json.loads(subprocess.run([node, "-e", code], check=True, capture_output=True, text=True).stdout)


def test_client_aliases_agree_with_server_converter(mappings, monkeypatch):
    cases = []
    expected = []
    for root in ["/app/Data/TIMS/Data", r"C:\Data\TIMS", r"\\server\share\TIMS"]:
        monkeypatch.setattr(config, "TIMS_DATA_DIR", root)
        current = paths.get_path_mappings()
        for value in ["/srv/msi/tims_data/a/b", "/srv/msi/tims_data/a/../b", "/srv/msi/tims_data2/a",
                      "/srv/msi/tims_data/../../outside", "relative/name", "", None, root + "/apple"]:
            case = {"display": value, "internal": "previous", "trigger": "data_folder_display.value"}
            assert _run_client(callbacks.path_sync_javascript(current), [case]) == [
                [paths.to_internal_path(value), "__NO_UPDATE__"]]
        for value in [root, root + "/apple", "/unmapped/old_project", "", None]:
            cases = [{"display": "previous", "internal": value, "trigger": "data_folder.value"}]
            expected = [["__NO_UPDATE__", paths.to_display_path(value)]]
            assert _run_client(callbacks.path_sync_javascript(current), cases) == expected


def test_client_same_value_no_update_and_pattern_trigger(mappings):
    script = callbacks.path_sync_javascript(mappings)
    public, internal = "/srv/msi/tims_data/apple", "/app/Data/TIMS/Data/apple"
    cases = [
        {"display": public, "internal": internal, "trigger": "data_folder_display.value"},
        {"display": public, "internal": internal, "trigger": "data_folder.value"},
        {"display": public, "internal": "old", "trigger": '{"index":2,"type":"extra_folder_path_display"}.value'},
        {"display": "old", "internal": internal, "trigger": None},
    ]
    assert _run_client(script, cases) == [["__NO_UPDATE__", "__NO_UPDATE__"],
        ["__NO_UPDATE__", "__NO_UPDATE__"], [internal, "__NO_UPDATE__"], ["__NO_UPDATE__", public]]
