"""Immediate UI translation; stores, metadata and worker inputs stay internal."""
import json

from dash import Input, Output, MATCH, clientside_callback

from app.layouts.public_path_inputs import display_id, wrapped_input_ids
from app.services.public_paths import get_path_mappings


def path_sync_javascript(mappings):
    """Generate the lexical converter, including Windows-local/POSIX-host pairs."""
    return r"""function(display, internal) {
        const no = window.dash_clientside.no_update;
        const mappings = __MAPPINGS__;
        function parse(value) {
            if (typeof value !== 'string' || !value || value.includes('\u0000')) return null;
            let flavor, root, tail;
            if (/^[A-Za-z]:[\\/]/.test(value)) {
                flavor = 'windows'; root = value.slice(0, 2) + '\\'; tail = value.slice(3);
            } else if (/^\\\\[^\\]+\\[^\\]+/.test(value)) {
                const match = value.match(/^\\\\([^\\]+)\\([^\\]+)(?:\\|$)/);
                flavor = 'windows'; root = '\\\\' + match[1] + '\\' + match[2] + '\\';
                tail = value.slice(match[0].length);
            } else if (value.startsWith('/')) {
                flavor = 'posix'; root = '/'; tail = value.replace(/^\/+/, '');
            } else return null;
            const parts = [root];
            for (const part of tail.split(flavor === 'windows' ? /[\\/]+/ : /\/+/)) {
                if (!part || part === '.') continue;
                if (part === '..') { if (parts.length > 1) parts.pop(); }
                else parts.push(part);
            }
            return {flavor, parts};
        }
        function translate(value, outward) {
            const parsed = parse(value);
            if (!parsed) return value;
            const pairs = mappings.map(pair => outward ? pair : [pair[1], pair[0]])
                .sort((a, b) => parse(b[0]).parts.length - parse(a[0]).parts.length);
            for (const [source, destination] of pairs) {
                const prefix = parse(source), target = parse(destination);
                if (parsed.flavor !== prefix.flavor || parsed.parts.length < prefix.parts.length) continue;
                const equal = (a, b) => parsed.flavor === 'windows' ? a.toLowerCase() === b.toLowerCase() : a === b;
                if (!prefix.parts.every((part, i) => equal(part, parsed.parts[i]))) continue;
                const result = target.parts.concat(parsed.parts.slice(prefix.parts.length));
                const sep = target.flavor === 'windows' ? '\\' : '/';
                return result[0] + result.slice(1).join(sep);
            }
            return value;
        }
        const triggered = window.dash_clientside.callback_context.triggered || [];
        const displayTriggered = triggered.some(item => {
            const id = item.prop_id.slice(0, item.prop_id.lastIndexOf('.'));
            return id.endsWith('_display') || id.includes('"type":"extra_folder_path_display"');
        });
        if (displayTriggered) {
            const value = translate(display, false);
            // Keep text/cursor as typed. Do not normalize the visible input on
            // each keystroke or feed the change through a server round trip.
            return [value === internal ? no : value, no];
        }
        const value = translate(internal, true);
        return [no, value === display ? no : value];
    }""".replace("__MAPPINGS__", json.dumps(mappings, ensure_ascii=True))


def register_public_path_callbacks(layout):
    mappings = get_path_mappings()
    if not mappings:
        return
    script = path_sync_javascript(mappings)
    pairs = [(identifier, display_id(identifier)) for identifier in wrapped_input_ids(layout)
             if isinstance(identifier, str)]
    pairs.append(({"type": "extra_folder_path", "index": MATCH},
                  {"type": "extra_folder_path_display", "index": MATCH}))
    for internal, display in pairs:
        clientside_callback(
            script,
            Output(internal, "value", allow_duplicate=True), Output(display, "value"),
            Input(display, "value"), Input(internal, "value"),
            prevent_initial_call=True,
        )
        # Separate direction from the value callback: validation callbacks may
        # depend on internal.value and produce these flags.
        clientside_callback(
            "function(invalid, valid, disabled, readonly) {"
            "return [!!invalid, !!valid, !!disabled, !!readonly];}",
            [Output(display, prop) for prop in ("invalid", "valid", "disabled", "readonly")],
            [Input(internal, prop) for prop in ("invalid", "valid", "disabled", "readonly")],
        )
