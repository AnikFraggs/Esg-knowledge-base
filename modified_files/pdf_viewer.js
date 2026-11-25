/* PDF viewer + rectangle selection + evidence highlighting (pdf.js 3.x).
 *
 * Coordinate contract with the backend:
 *   bbox = {x0, y0, x1, y1} FRACTIONS of the page, origin TOP-LEFT.
 *   The canvas is rendered with the page's full aspect ratio, so
 *   fraction = pixel / canvas-size. The backend converts fractions into
 *   pdfplumber (points, top-left) / PyMuPDF (points, top-left) itself.
 *
 * API (window.PDFViewer):
 *   loadFiling(filingId)     fetch /api/filings/<id>/pdf and open it
 *   goToPage(n)              render page n (1-based)
 *   nextPage() / prevPage()
 *   highlight(boxes)         boxes = [{bbox:{x0,y0,x1,y1}, word?}, ...]
 *   clearHighlight()
 *   onSelection(callback)    callback({page, bbox}) after a drag ends
 */
(function () {
  "use strict";

  if (!window.pdfjsLib) {
    console.error("pdf.js failed to load (CDN blocked? dashboard needs it for the viewer)");
    return;
  }
  pdfjsLib.GlobalWorkerOptions.workerSrc =
    "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";

  const stage = document.getElementById("pdf-stage");
  if (!stage) return;

  let pdfDoc = null;
  let pageNo = 1;
  let rendering = false;
  let canvas = null;
  let overlay = null;      // drag surface
  let hlLayer = null;      // evidence highlight rectangles
  let pendingRender = false;
  let selectionCb = null;

  const API = {
    get pageCount() { return pdfDoc ? pdfDoc.numPages : 0; },
    get page() { return pageNo; },
    onSelection(cb) { selectionCb = cb; },
    loadFiling,
    goToPage,
    nextPage,
    prevPage,
    highlight,
    clearHighlight
  };
  window.PDFViewer = API;

  // ---------------------------------------------------------------- build DOM

  function buildSkeleton() {
    stage.innerHTML = "";
    stage.classList.add("loaded");

    const holder = document.createElement("div");
    holder.className = "pdf-holder";

    canvas = document.createElement("canvas");
    canvas.className = "pdf-canvas";

    hlLayer = document.createElement("div");
    hlLayer.className = "hl-layer";

    overlay = document.createElement("div");
    overlay.className = "sel-overlay";
    bindDrag(overlay);

    const selBox = document.createElement("div");
    selBox.className = "sel-box hidden";
    overlay.appendChild(selBox);

    holder.appendChild(canvas);
    holder.appendChild(hlLayer);
    holder.appendChild(overlay);
    stage.appendChild(holder);
  }

  // ---------------------------------------------------------------- loading

  async function loadFiling(filingId) {
    if (!filingId) return;
    stage.innerHTML = '<div class="pdf-empty">loading PDF&hellip;</div>';
    stage.classList.remove("loaded");
    pdfDoc = null;
    try {
      const resp = await fetch("/api/filings/" + filingId + "/pdf");
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.error || ("HTTP " + resp.status));
      }
      const buf = await resp.arrayBuffer();
      pdfDoc = await pdfjsLib.getDocument({ data: buf }).promise;
      buildSkeleton();
      pageNo = 1;
      await render();
    } catch (exc) {
      stage.innerHTML = '<div class="pdf-empty">Could not open PDF: ' +
        String(exc.message || exc) + "</div>";
    }
  }

  // ---------------------------------------------------------------- render

  function fitScale(page) {
    const avail = Math.max(stage.clientWidth - 24, 320);
    const base = page.getViewport({ scale: 1 });
    const s = avail / base.width;
    return s < 0.3 ? 0.3 : (s > 3 ? 3 : s);
  }

  async function render() {
    if (!pdfDoc || rendering) { pendingRender = true; return; }
    rendering = true;
    try {
      const page = await pdfDoc.getPage(pageNo);
      const viewport = page.getViewport({ scale: fitScale(page) });
      const ctx = canvas.getContext("2d");
      canvas.width = Math.floor(viewport.width);
      canvas.height = Math.floor(viewport.height);
      canvas.style.width = canvas.width + "px";
      canvas.style.height = canvas.height + "px";
      overlay.style.width = canvas.style.width;
      overlay.style.height = canvas.style.height;
      await page.render({ canvasContext: ctx, viewport: viewport }).promise;
      document.getElementById("pg-num").value = pageNo;
      document.getElementById("pg-total").textContent = "/ " + pdfDoc.numPages;
    } finally {
      rendering = false;
      if (pendingRender) { pendingRender = false; render(); }
    }
  }

  function goToPage(n) {
    if (!pdfDoc) return;
    const target = Math.min(Math.max(1, n | 0), pdfDoc.numPages);
    if (target === pageNo) return;
    pageNo = target;
    clearHighlight();
    render();
  }
  function nextPage() { goToPage(pageNo + 1); }
  function prevPage() { goToPage(pageNo - 1); }

  // ---------------------------------------------------------------- highlight

  function highlight(boxes) {
    if (!hlLayer) return;
    hlLayer.innerHTML = "";
    (boxes || []).forEach(function (b) {
      if (!b || !b.bbox) return;
      const r = document.createElement("div");
      r.className = "hl-box";
      r.style.left = (b.bbox.x0 * 100) + "%";
      r.style.top = (b.bbox.y0 * 100) + "%";
      r.style.width = ((b.bbox.x1 - b.bbox.x0) * 100) + "%";
      r.style.height = ((b.bbox.y1 - b.bbox.y0) * 100) + "%";
      if (b.word) r.title = b.word;
      hlLayer.appendChild(r);
    });
  }

  function clearHighlight() { if (hlLayer) hlLayer.innerHTML = ""; }

  // ---------------------------------------------------------------- drag select

  function bindDrag(surface) {
    let dragging = false;
    let startX = 0, startY = 0;
    const selBox = surface.querySelector(".sel-box");

    function pos(ev) {
      const rect = surface.getBoundingClientRect();
      return {
        x: Math.min(Math.max(ev.clientX - rect.left, 0), rect.width),
        y: Math.min(Math.max(ev.clientY - rect.top, 0), rect.height),
        w: rect.width,
        h: rect.height
      };
    }

    surface.addEventListener("mousedown", function (ev) {
      if (ev.button !== 0) return;
      dragging = true;
      const p = pos(ev);
      startX = p.x; startY = p.y;
      selBox.classList.remove("hidden");
      selBox.style.left = p.x + "px";
      selBox.style.top = p.y + "px";
      selBox.style.width = "0px";
      selBox.style.height = "0px";
      ev.preventDefault();
    });

    window.addEventListener("mousemove", function (ev) {
      if (!dragging) return;
      const p = pos(ev);
      const x = Math.min(startX, p.x), y = Math.min(startY, p.y);
      selBox.style.left = x + "px";
      selBox.style.top = y + "px";
      selBox.style.width = Math.abs(p.x - startX) + "px";
      selBox.style.height = Math.abs(p.y - startY) + "px";
    });

    window.addEventListener("mouseup", function (ev) {
      if (!dragging) return;
      dragging = false;
      const p = pos(ev);
      const w = Math.abs(p.x - startX), h = Math.abs(p.y - startY);
      // ignore accidental clicks (< ~12px) - they are page turns, not selections
      if (w < 12 || h < 10) { selBox.classList.add("hidden"); return; }
      const rect = surface.getBoundingClientRect();
      const bbox = {
        x0: Math.min(startX, p.x) / rect.width,
        y0: Math.min(startY, p.y) / rect.height,
        x1: Math.max(startX, p.x) / rect.width,
        y1: Math.max(startY, p.y) / rect.height
      };
      // clamp to [0,1] and keep 4 decimals
      ["x0", "y0", "x1", "y1"].forEach(function (k) {
        bbox[k] = Math.round(Math.min(1, Math.max(0, bbox[k])) * 10000) / 10000;
      });
      if (selectionCb) selectionCb({ page: pageNo, bbox: bbox });
      // keep the box visible until the capture form is cancelled/submitted
    });
  }

  // ---------------------------------------------------------------- pager wire-up

  document.getElementById("pg-prev").addEventListener("click", prevPage);
  document.getElementById("pg-next").addEventListener("click", nextPage);
  document.getElementById("pg-num").addEventListener("change", function () {
    goToPage(parseInt(this.value, 10) || 1);
  });
  window.addEventListener("resize", function () {
    if (pdfDoc) render();
  });
})();
