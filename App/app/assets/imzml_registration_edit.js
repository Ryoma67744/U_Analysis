(function () {
  "use strict";

  // Dash DataTable clears its editing state on document mousedown, before
  // the browser's normal blur can commit the CellInput value. Commit first
  // when leaving an imzML metadata cell; keep clicks inside that input intact.
  document.addEventListener("mousedown", function (event) {
    const active = document.activeElement;
    if (event.button !== 0 || !active ||
        !active.matches("#imzml_batch_table td.dash-cell input")) return;
    if (active === event.target) {
      // A caret/selection click must not reset the table's editing state.
      // Preserve the browser's default selection behavior.
      event.stopPropagation();
      return;
    }
    // React 16 (used by the pinned Dash version) handles blur in capture.
    // Commit through CellInput.onBlur without moving native focus: blur()
    // makes CellInput refocus/scroll during its update and can move the
    // submit button between mousedown and mouseup, losing the click.
    active.dispatchEvent(new FocusEvent("blur", {
      relatedTarget: event.target
    }));
  }, true);
})();
