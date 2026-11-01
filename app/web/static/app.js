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
    copyText(el.getAttribute("data-copy"), el);
  });

  function copyText(text, el) {
    var done = function () {
      el.classList.add("copied");
      setTimeout(function () { el.classList.remove("copied"); }, 1500);
    };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done, function () { fallbackCopy(text) && done(); });
    } else if (fallbackCopy(text)) {
      done();
    }
  }

  // Queue "Copy all as text": fetch the plain list, then copy it like any data-copy box.
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-copy-url]");
    if (!btn) return;
    fetch(btn.getAttribute("data-copy-url"))
      .then(function (r) { return r.ok ? r.text() : Promise.reject(new Error(String(r.status))); })
      .then(function (text) { copyText(text, btn); })
      .catch(function () {});
  });

  // Settings cards come back as 422/502 with the re-rendered card; swap those (and only those).
  document.addEventListener("htmx:beforeSwap", function (e) {
    var xhr = e.detail.xhr;
    if (xhr && (xhr.status === 422 || xhr.status === 502) && xhr.getResponseHeader("X-WM-Card")) {
      e.detail.shouldSwap = true;
      e.detail.isError = false;
    }
  });

  // Queue session counter: Got it on a row makes it done, Undo takes it back.
  var sessionDone = 0;
  document.addEventListener("htmx:beforeSwap", function (e) {
    var cfg = e.detail.requestConfig, row = cfg && cfg.elt && cfg.elt.closest(".qrow");
    if (!row || e.detail.target !== row || e.detail.isError) return;
    var willBeDone = /class="qrow done"/.test(e.detail.serverResponse || "");
    var change = willBeDone && !row.classList.contains("done") ? 1
      : !willBeDone && row.classList.contains("done") ? -1 : 0;
    if (change) { sessionDone = Math.max(0, sessionDone + change); showProgress(); }
  });

  function showProgress() {
    var el = document.getElementById("queue-progress");
    if (el) el.textContent = sessionDone + " of " + el.getAttribute("data-total") + " done this session";
  }

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

  // Sortable headings: switch direction if the column is active, else start ascending,
  // then let the form's hx-get on change re-fetch the rows (and the re-rendered head row).
  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".sort-head");
    if (!btn) return;
    var form = document.getElementById("filters");
    var input = form && form.querySelector('input[name="sort"]');
    if (!input) return;
    var asc = btn.getAttribute("data-sort-asc"), desc = btn.getAttribute("data-sort-desc");
    input.value = input.value === asc ? desc : asc;
    form.dispatchEvent(new Event("change", { bubbles: true }));
  });

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
