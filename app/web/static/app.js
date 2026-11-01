// Want Monitor: row selection, bulk bar, copy-to-clipboard. No dependencies.
(function () {
  "use strict";

  function checks() {
    return Array.from(document.querySelectorAll(".row-check"));
  }

  // Bulk bar visibility follows the checked .row-check boxes; the bulk buttons
  // send their ids via hx-include=".row-check:checked" (each box is name="ids").
  function syncSelection() {
    var bar = document.getElementById("bulk-bar");
    if (!bar) return;
    var all = checks();
    var selected = all.filter(function (c) { return c.checked; }).length;
    bar.hidden = selected === 0;
    var count = document.getElementById("bulk-count");
    if (count) count.textContent = String(selected);
    var master = document.getElementById("check-all");
    if (master) {
      master.checked = selected > 0 && selected === all.length;
      master.indeterminate = selected > 0 && selected < all.length;
    }
  }

  function clearSelection() {
    checks().forEach(function (c) { c.checked = false; });
    syncSelection();
  }

  document.addEventListener("change", function (e) {
    var t = e.target;
    if (t.id === "check-all") {
      checks().forEach(function (c) { c.checked = t.checked; });
      syncSelection();
    } else if (t.classList && t.classList.contains("row-check")) {
      syncSelection();
    }
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") clearSelection();
  });

  document.addEventListener("click", function (e) {
    if (e.target.closest("[data-clear-selection]")) {
      clearSelection();
      return;
    }
    var el = e.target.closest("[data-copy]");
    if (!el) return;
    e.preventDefault();
    var text = el.getAttribute("data-copy");
    var done = function () {
      el.classList.add("copied");
      setTimeout(function () { el.classList.remove("copied"); }, 1500);
    };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done, function () { fallbackCopy(text) && done(); });
    } else if (fallbackCopy(text)) {
      done();
    }
  });

  // Clipboard API needs a secure context; the app is often served over plain http on a LAN.
  function fallbackCopy(text) {
    var area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    var ok = false;
    try { ok = document.execCommand("copy"); } catch (err) { ok = false; }
    document.body.removeChild(area);
    return ok;
  }

  // Rows are replaced by htmx swaps: recompute selection and fade out old flash messages.
  document.addEventListener("htmx:afterSettle", function (e) {
    syncSelection();
    var flash = document.getElementById("flash");
    if (flash && flash.contains(e.target) && flash.firstElementChild) {
      var msg = flash.firstElementChild;
      setTimeout(function () { if (msg.parentNode) msg.remove(); }, 5000);
    }
  });

  // A poster that fails to load falls back to the neutral box instead of a broken-image icon.
  document.addEventListener("error", function (e) {
    if (e.target.tagName === "IMG" && e.target.closest(".poster")) e.target.remove();
  }, true);

  document.addEventListener("DOMContentLoaded", syncSelection);
})();
