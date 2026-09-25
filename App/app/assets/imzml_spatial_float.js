(function () {
  "use strict";
  const selector = ".imzml-spatial-float";
  // v2で初期サイズを拡大したため、旧520px幅の保存値を意図的に引き継がない。
  const storagePrefix = "ua-imzml-spatial-window:v2:";

  function limits() {
    const maxWidth = Math.max(320, window.innerWidth - 16);
    const maxHeight = Math.max(280, window.innerHeight - 72);
    return {
      minWidth: Math.min(680, maxWidth),
      minHeight: Math.min(520, maxHeight),
      maxWidth: maxWidth,
      maxHeight: maxHeight
    };
  }

  function clamp(panel) {
    const lim = limits();
    let rect = panel.getBoundingClientRect();
    if (rect.width > lim.maxWidth) panel.style.width = lim.maxWidth + "px";
    if (rect.height > lim.maxHeight) panel.style.height = lim.maxHeight + "px";
    rect = panel.getBoundingClientRect();
    const margin = 8;
    let left = rect.left;
    let top = rect.top;
    if (rect.right > window.innerWidth - margin) left -= rect.right - (window.innerWidth - margin);
    if (rect.bottom > window.innerHeight - margin) top -= rect.bottom - (window.innerHeight - margin);
    left = Math.max(margin, left);
    top = Math.max(margin, top);
    panel.style.left = left + "px";
    panel.style.top = top + "px";
    panel.style.right = "auto";
  }

  function save(panel) {
    if (!panel.id || panel.classList.contains("minimized")) return;
    const rect = panel.getBoundingClientRect();
    const state = {left: rect.left, top: rect.top, width: rect.width, height: rect.height};
    try { sessionStorage.setItem(storagePrefix + panel.id, JSON.stringify(state)); } catch (_) {}
  }

  function restore(panel) {
    if (!panel.id) return;
    try {
      const raw = sessionStorage.getItem(storagePrefix + panel.id);
      if (!raw) return;
      const state = JSON.parse(raw);
      const lim = limits();
      if (Number.isFinite(state.left)) panel.style.left = state.left + "px";
      if (Number.isFinite(state.top)) panel.style.top = state.top + "px";
      if (Number.isFinite(state.width)) {
        panel.style.width = Math.min(lim.maxWidth, Math.max(lim.minWidth, state.width)) + "px";
      }
      if (Number.isFinite(state.height)) {
        panel.style.height = Math.min(lim.maxHeight, Math.max(lim.minHeight, state.height)) + "px";
      }
      panel.style.right = "auto";
      requestAnimationFrame(() => {
        clamp(panel);
        window.dispatchEvent(new Event("resize"));
      });
    } catch (_) {}
  }

  function bind(panel) {
    if (panel.dataset.spatialFloatBound === "1") return;
    panel.dataset.spatialFloatBound = "1";
    restore(panel);
    const header = panel.querySelector(".imzml-spatial-float-header");
    const handle = panel.querySelector(".imzml-spatial-resize-handle");

    if (header) {
      header.addEventListener("pointerdown", function (event) {
        if (event.button !== 0 || event.target.closest("button")) return;
        event.preventDefault();
        const rect = panel.getBoundingClientRect();
        const startX = event.clientX;
        const startY = event.clientY;
        const startLeft = rect.left;
        const startTop = rect.top;
        panel.style.right = "auto";
        header.setPointerCapture(event.pointerId);
        function move(e) {
          panel.style.left = (startLeft + e.clientX - startX) + "px";
          panel.style.top = (startTop + e.clientY - startY) + "px";
        }
        function end(e) {
          header.releasePointerCapture(e.pointerId);
          header.removeEventListener("pointermove", move);
          header.removeEventListener("pointerup", end);
          header.removeEventListener("pointercancel", end);
          clamp(panel);
          save(panel);
        }
        header.addEventListener("pointermove", move);
        header.addEventListener("pointerup", end);
        header.addEventListener("pointercancel", end);
      });
    }

    if (handle) {
      handle.addEventListener("pointerdown", function (event) {
        if (event.button !== 0) return;
        event.preventDefault();
        event.stopPropagation();
        const rect = panel.getBoundingClientRect();
        const startX = event.clientX;
        const startY = event.clientY;
        const startWidth = rect.width;
        const startHeight = rect.height;
        handle.setPointerCapture(event.pointerId);
        function move(e) {
          const lim = limits();
          panel.style.width = Math.min(lim.maxWidth,
            Math.max(lim.minWidth, startWidth + e.clientX - startX)) + "px";
          panel.style.height = Math.min(lim.maxHeight,
            Math.max(lim.minHeight, startHeight + e.clientY - startY)) + "px";
        }
        function end(e) {
          handle.releasePointerCapture(e.pointerId);
          handle.removeEventListener("pointermove", move);
          handle.removeEventListener("pointerup", end);
          handle.removeEventListener("pointercancel", end);
          clamp(panel);
          save(panel);
          window.dispatchEvent(new Event("resize"));
        }
        handle.addEventListener("pointermove", move);
        handle.addEventListener("pointerup", end);
        handle.addEventListener("pointercancel", end);
      });
    }
  }

  function bindAll(root) {
    if (root && root.matches && root.matches(selector)) bind(root);
    (root || document).querySelectorAll(selector).forEach(bind);
  }

  document.addEventListener("DOMContentLoaded", () => bindAll(document));
  new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      mutation.addedNodes.forEach((node) => {
        if (node.nodeType === 1) bindAll(node);
      });
      if (mutation.type === "attributes") {
        const panel = mutation.target;
        if (panel.matches && panel.matches(selector) && panel.style.display !== "none") {
          if (!panel.style.left) restore(panel);
          if (mutation.attributeName === "class") {
            requestAnimationFrame(() => {
              clamp(panel);
              window.dispatchEvent(new Event("resize"));
            });
          }
        }
      }
    }
  }).observe(document.documentElement, {childList: true, subtree: true, attributes: true,
                                        attributeFilter: ["style", "class"]});
  window.addEventListener("resize", () => document.querySelectorAll(selector).forEach(clamp));
})();
