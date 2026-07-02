/* MrFoX-MeM web UI controller.
 * - Same-origin fetches only (relative paths). No eval. No innerHTML with API text.
 * - All API-derived strings are inserted via textContent / DOM nodes.
 */
"use strict";

(function () {
  // ---------- Node-kind color scheme (must mirror style.css legend vars) ----------
  const KIND_COLORS = {
    dir:     "#f0b429",
    file:    "#58a6ff",
    module:  "#a371f7",
    symbol:  "#3fb950",
    concept: "#f08a3c",
    doc:     "#56d4dd",
  };
  const KIND_ORDER = ["dir", "file", "module", "symbol", "concept", "doc"];
  const DEFAULT_COLOR = "#7d8590";

  // ---------- State ----------
  let cy = null;
  let currentProject = null;
  let healthInfo = null;

  // Sessions panel (live retrieval feed + runs). Polling only runs while the
  // Sessions tab is the active panel and the document is visible.
  const SESSIONS_POLL_MS = 3000;
  let sessionsTimer = null;   // setInterval handle for the live feed fetch loop
  let updatedTicker = null;   // 1s interval that re-stamps the "updated Xs ago" label
  let sessionsLive = true;    // user Live/Pause preference
  let lastPollAt = null;      // ms timestamp of the last successful poll
  let activeFeedId = null;    // id of the currently expanded feed entry
  let activeRunId = null;     // id of the currently opened run

  // ---------- DOM helpers ----------
  const $ = (id) => document.getElementById(id);
  function show(el) { if (el) el.classList.remove("hidden"); }
  function hide(el) { if (el) el.classList.add("hidden"); }
  function clear(el) { while (el && el.firstChild) el.removeChild(el.firstChild); }

  function el(tag, opts) {
    const node = document.createElement(tag);
    if (opts) {
      if (opts.class) node.className = opts.class;
      if (opts.text != null) node.textContent = String(opts.text); // safe
      if (opts.title) node.title = String(opts.title);
      if (opts.attrs) for (const k in opts.attrs) node.setAttribute(k, String(opts.attrs[k]));
    }
    return node;
  }

  function colorFor(kind) {
    return Object.prototype.hasOwnProperty.call(KIND_COLORS, kind) ? KIND_COLORS[kind] : DEFAULT_COLOR;
  }

  // A distinct silhouette per kind gives the graph a visual vocabulary you can
  // read at a glance without hunting the legend (dirs are containers, docs are
  // tags, concepts are diamonds, modules are hexagons).
  const KIND_SHAPES = {
    dir:     "round-rectangle",
    file:    "ellipse",
    module:  "round-hexagon",
    symbol:  "ellipse",
    concept: "round-diamond",
    doc:     "round-tag",
  };
  function shapeFor(kind) {
    return Object.prototype.hasOwnProperty.call(KIND_SHAPES, kind) ? KIND_SHAPES[kind] : "ellipse";
  }

  // ---------- Fetch wrapper (same-origin, relative, timeout) ----------
  async function api(path, params) {
    const usp = new URLSearchParams();
    if (params) for (const k in params) {
      if (params[k] != null && params[k] !== "") usp.set(k, params[k]);
    }
    const qs = usp.toString();
    const url = path + (qs ? "?" + qs : "");
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 15000);
    try {
      const res = await fetch(url, {
        method: "GET",
        headers: { "Accept": "application/json" },
        signal: ctrl.signal,
        credentials: "same-origin",
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const msg = (data && data.error) ? data.error : ("HTTP " + res.status);
        throw new Error(msg);
      }
      return data;
    } finally {
      clearTimeout(t);
    }
  }

  // Same-origin POST helper (the read wrapper above is GET-only).
  async function apiPost(path, body) {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 15000);
    try {
      const res = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Accept": "application/json" },
        body: JSON.stringify(body),
        signal: ctrl.signal,
        credentials: "same-origin",
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error((data && data.error) ? data.error : "HTTP " + res.status);
      return data;
    } finally {
      clearTimeout(t);
    }
  }

  // ---------- Health ----------
  async function loadHealth() {
    const pill = $("health-pill");
    try {
      healthInfo = await api("/health");
      const backend = healthInfo.embed_backend || "?";
      const degraded = !!healthInfo.degraded;
      pill.textContent = (degraded ? "⚠ " : "") + "ok · " + backend +
        (healthInfo.version ? " · v" + healthInfo.version : "");
      pill.className = "pill " + (degraded ? "pill-warn" : "pill-good");
      pill.title = degraded
        ? (healthInfo.warning || "Degraded embedder (keyword-level recall only).")
        : "API healthy (embed: " + backend + ")";
      return true;
    } catch (e) {
      pill.textContent = "API offline";
      pill.className = "pill pill-bad";
      pill.title = String(e.message || e);
      return false;
    }
  }

  // ---------- Legend ----------
  function buildLegend() {
    const lg = $("legend");
    clear(lg);
    KIND_ORDER.forEach((kind) => {
      const item = el("div", { class: "legend-item" });
      const dot = el("span", { class: "legend-dot" });
      dot.style.background = colorFor(kind);
      item.appendChild(dot);
      item.appendChild(el("span", { text: kind }));
      lg.appendChild(item);
    });
  }

  // ---------- Cytoscape ----------
  function initCy() {
    cy = cytoscape({
      container: $("cy"),
      wheelSensitivity: 0.25,
      minZoom: 0.05,
      maxZoom: 4,
      style: [
        {
          selector: "node",
          style: {
            "background-color": (n) => colorFor(n.data("kind")),
            "background-opacity": 0.95,
            "shape": (n) => shapeFor(n.data("kind")),
            "label": "data(label)",
            "color": "#e6edf3",
            "font-size": 10,
            "font-weight": 500,
            "text-valign": "bottom",
            "text-halign": "center",
            "text-margin-y": 4,
            "text-wrap": "ellipsis",
            "text-max-width": 120,
            // Size tracks connectivity (degree) — a cheap centrality proxy, so
            // hubs (heavily-imported modules, busy dirs) visibly read as bigger.
            "width": "mapData(deg, 0, 8, 18, 46)",
            "height": "mapData(deg, 0, 8, 18, 46)",
            "border-width": 1,
            "border-color": "#0e1117",
            "border-opacity": 0.6,
            // Outline keeps the label crisp on any background without a heavy plate.
            "text-outline-color": "#0e1117",
            "text-outline-width": 2,
            "transition-property":
              "width height background-color border-color underlay-opacity opacity",
            "transition-duration": "160ms",
          },
        },
        {
          selector: "node:selected",
          style: {
            "border-width": 2, "border-color": "#ffffff", "border-opacity": 1,
            "underlay-color": "#ffffff", "underlay-opacity": 0.18, "underlay-padding": 10,
          },
        },
        {
          selector: "node.highlight",
          style: {
            "border-width": 2,
            "border-color": (n) => colorFor(n.data("kind")),
            "border-opacity": 1,
            // Warm halo around retrieval / search hits — the shared spotlight.
            "underlay-color": "#f08a3c", "underlay-opacity": 0.30, "underlay-padding": 12,
            "z-index": 20,
          },
        },
        {
          selector: "node.dim",
          style: { "opacity": 0.14 },
        },
        {
          selector: "edge",
          style: {
            "width": 1.1,
            "line-color": "#39435a",
            "line-opacity": 0.75,
            "target-arrow-color": "#39435a",
            "target-arrow-shape": "triangle",
            "arrow-scale": 0.7,
            "curve-style": "bezier",
          },
        },
        // Edge meaning by relationship: containment is quiet scaffolding; imports
        // and references are the load-bearing semantic links, so they get color.
        {
          selector: 'edge[rel = "contains"]',
          style: { "line-color": "#2a3242", "target-arrow-color": "#2a3242", "line-opacity": 0.45 },
        },
        {
          selector: 'edge[rel = "imports"]',
          style: {
            "line-color": "#f0b429", "target-arrow-color": "#f0b429",
            "line-style": "dashed", "width": 1.4, "line-opacity": 0.9,
          },
        },
        {
          selector: 'edge[rel = "references"]',
          style: { "line-color": "#58a6ff", "target-arrow-color": "#58a6ff", "width": 1.3 },
        },
        {
          selector: 'edge[rel = "decided_for"]',
          style: {
            "line-color": "#a371f7", "target-arrow-color": "#a371f7",
            "line-style": "dotted", "width": 1.3,
          },
        },
        {
          selector: "edge.highlight",
          style: {
            "line-color": "#f08a3c", "target-arrow-color": "#f08a3c",
            "width": 2, "line-opacity": 1, "z-index": 19,
          },
        },
        {
          selector: "edge.dim",
          style: { "opacity": 0.06 },
        },
      ],
    });

    cy.on("tap", "node", (evt) => onNodeSelected(evt.target));
    cy.on("tap", (evt) => { if (evt.target === cy) clearHighlights(); });
  }

  function layoutName(n) {
    // Radial breadthfirst (root at center, depths on concentric rings) spreads a
    // knowledge tree cleanly; very large graphs fall back to force-directed cose.
    return n > 800 ? "cose" : "breadthfirst";
  }

  function renderTree(tree) {
    const nodes = Array.isArray(tree.nodes) ? tree.nodes : [];
    const edges = Array.isArray(tree.edges) ? tree.edges : [];

    if (nodes.length === 0) {
      showEmptyState("Project \"" + (tree.project || currentProject) + "\" has no nodes yet.");
      return;
    }
    hideEmptyState();

    const idSet = new Set(nodes.map((n) => String(n.id)));

    // Degree = how many edges touch a node; drives node size (a cheap centrality
    // proxy so structural hubs stand out). Counted from both endpoints.
    const degree = Object.create(null);
    edges.forEach((e) => {
      const s = String(e.src), d = String(e.dst);
      degree[s] = (degree[s] || 0) + 1;
      degree[d] = (degree[d] || 0) + 1;
    });

    const cyNodes = nodes.map((n) => ({
      group: "nodes",
      data: {
        id: String(n.id),
        label: n.label != null ? String(n.label) : String(n.id),
        kind: n.kind != null ? String(n.kind) : "",
        path: n.path != null ? String(n.path) : "",
        summary: n.summary != null ? String(n.summary) : "",
        parent_id: n.parent != null ? String(n.parent) : "",
        deg: degree[String(n.id)] || 0,
      },
    }));

    const cyEdges = [];
    edges.forEach((e, i) => {
      const s = String(e.src), d = String(e.dst);
      if (idSet.has(s) && idSet.has(d)) {
        cyEdges.push({
          group: "edges",
          data: { id: "e" + i + "_" + s + "_" + d, source: s, target: d, rel: e.rel != null ? String(e.rel) : "" },
        });
      }
    });

    cy.elements().remove();
    cy.add(cyNodes);
    cy.add(cyEdges);

    const name = layoutName(cyNodes.length);
    const opts = { name, animate: false, fit: true, padding: 60 };
    if (name === "breadthfirst") {
      // circle:true lays depths on concentric rings (radial tree) instead of a
      // flat top-down band — the "spread out" look. directed follows edges.
      opts.directed = true;
      opts.circle = true;
      opts.spacingFactor = 1.3;
      opts.avoidOverlap = true;
    } else {
      // Force-directed organic spread for huge graphs.
      opts.idealEdgeLength = 70;
      opts.nodeRepulsion = 16000;
      opts.gravity = 0.15;
      opts.numIter = 1500;
      opts.componentSpacing = 90;
      opts.nodeOverlap = 12;
    }
    cy.layout(opts).run();
    cy.fit(undefined, 60);
  }

  function clearHighlights() {
    if (!cy) return;
    cy.elements().removeClass("highlight dim");
  }

  // Spotlight a set of node ids: dim the whole graph, then un-dim + highlight
  // the matched nodes and their immediate neighborhood. Factored out of search
  // so the Sessions feed can reuse the *exact* same affordance — clicking a
  // retrieval lights up precisely the nodes that retrieval injected.
  function highlightNodeIds(ids, fit) {
    if (!cy) return cy ? cy.collection() : null;
    const want = new Set((ids || []).map(String));
    const matched = cy.collection();
    cy.nodes().forEach((n) => { if (want.has(n.id())) matched.merge(n); });

    clearHighlights();
    if (matched.empty()) return matched;

    cy.elements().addClass("dim");
    matched.removeClass("dim").addClass("highlight");
    matched.connectedEdges().removeClass("dim").addClass("highlight");
    matched.neighborhood().removeClass("dim");
    if (fit) cy.animate({ fit: { eles: matched, padding: 80 } }, { duration: 300 });
    return matched;
  }

  // ---------- Node selection / details ----------
  async function onNodeSelected(node) {
    const d = node.data();
    show($("details-content"));
    hide($("details-empty"));
    activateTab("details");

    $("d-label").textContent = d.label || "(unlabeled)";

    const kindPill = $("d-kind");
    kindPill.textContent = d.kind || "node";
    kindPill.style.borderColor = colorFor(d.kind);
    kindPill.style.color = colorFor(d.kind);

    $("d-path").textContent = d.path || "—";
    $("d-summary").textContent = d.summary || "(no summary)";

    // Highlight node + its neighborhood in graph
    clearHighlights();
    const neighborhood = node.closedNeighborhood();
    cy.elements().addClass("dim");
    neighborhood.removeClass("dim");
    node.addClass("highlight");
    cy.animate({ center: { eles: node }, zoom: Math.max(cy.zoom(), 1) }, { duration: 250 });

    // Related: prefer graph edges; also fetch /search for semantic relatives.
    await loadRelated(node);
  }

  async function loadRelated(node) {
    const list = $("d-related");
    clear(list);
    hide($("d-related-empty"));

    const seen = new Set();
    const items = [];

    // 1) Graph neighbors (direct edges)
    node.connectedEdges().forEach((edge) => {
      const other = edge.source().id() === node.id() ? edge.target() : edge.source();
      const oid = other.id();
      if (seen.has(oid)) return;
      seen.add(oid);
      items.push({
        node_id: oid,
        label: other.data("label"),
        kind: other.data("kind"),
        snippet: "rel: " + (edge.data("rel") || "linked"),
        score: null,
      });
    });

    // 2) Semantic relatives via /search using the node label
    try {
      const q = node.data("label");
      if (q) {
        const res = await api("/search", { project: currentProject, q: q, k: 12 });
        (res.results || []).forEach((r) => {
          const rid = String(r.node_id);
          if (rid === node.id() || seen.has(rid)) return;
          seen.add(rid);
          items.push({
            node_id: rid,
            label: r.label,
            kind: r.kind,
            snippet: r.snippet || "",
            score: typeof r.score === "number" ? r.score : null,
          });
        });
      }
    } catch (e) { /* search optional; ignore failures */ }

    if (items.length === 0) {
      show($("d-related-empty"));
      return;
    }
    items.slice(0, 20).forEach((it) => list.appendChild(relatedItem(it)));
  }

  function relatedItem(it) {
    const li = el("li", { class: "related-item" });
    const top = el("div", { class: "ri-top" });
    const lbl = el("span", { class: "ri-label", text: it.label != null ? it.label : it.node_id });
    top.appendChild(lbl);
    if (it.kind) {
      const k = el("span", { class: "score", text: it.kind });
      k.style.color = colorFor(it.kind);
      top.appendChild(k);
    } else if (it.score != null) {
      top.appendChild(el("span", { class: "score", text: it.score.toFixed(3) }));
    }
    li.appendChild(top);
    if (it.snippet) li.appendChild(el("div", { class: "ri-snippet", text: it.snippet }));

    li.addEventListener("click", () => {
      const target = cy.getElementById(String(it.node_id));
      if (target && target.nonempty()) {
        cy.elements().unselect();
        target.select();
        onNodeSelected(target);
      }
    });
    return li;
  }

  // ---------- Search ----------
  async function runSearch(q) {
    if (!q || !currentProject) return;
    try {
      const res = await api("/search", { project: currentProject, q: q, k: 12 });
      const results = res.results || [];
      clearHighlights();
      if (results.length === 0) return;

      const matched = highlightNodeIds(results.map((r) => r.node_id), true);

      if (matched.nonempty()) {
        // Open details for the top hit.
        const top = cy.getElementById(String(results[0].node_id));
        if (top && top.nonempty()) { top.select(); onNodeSelected(top); }
      }
    } catch (e) {
      console.warn("search failed:", e.message);
    }
  }

  // ---------- Context timeline ----------
  async function loadContext() {
    const tl = $("ctx-timeline");
    const empty = $("ctx-empty");
    clear(tl);
    if (!currentProject) { show(empty); return; }
    try {
      const res = await api("/context", { project: currentProject, k: 20 });
      const events = res.events || [];
      if (events.length === 0) { show(empty); return; }
      hide(empty);
      events.forEach((ev) => tl.appendChild(timelineItem(ev)));
    } catch (e) {
      show(empty);
      empty.textContent = "Could not load context: " + (e.message || e);
    }
  }

  // Capture a user-typed note/decision/finding into the project's memory.
  async function saveContextNote() {
    const input = $("ctx-input");
    const content = (input.value || "").trim();
    if (!content || !currentProject) return;
    const kind = $("ctx-kind").value || "note";
    const btn = $("ctx-save");
    btn.disabled = true;
    try {
      await apiPost("/context", { project: currentProject, kind: kind, content: content });
      input.value = "";
      await loadContext();
    } catch (e) {
      show($("ctx-empty"));
      $("ctx-empty").textContent = "Could not save: " + (e.message || e);
    } finally {
      btn.disabled = false;
    }
  }

  function timelineItem(ev) {
    const li = el("li", { class: "tl-item" });
    const meta = el("div", { class: "tl-meta" });
    const kind = String(ev.kind || "note");
    const kindEl = el("span", { class: "tl-kind kind-" + kind, text: kind });
    meta.appendChild(kindEl);
    if (ev.ts) meta.appendChild(el("span", { class: "tl-ts", text: formatTs(ev.ts) }));
    li.appendChild(meta);
    li.appendChild(el("div", { class: "tl-content", text: ev.content != null ? String(ev.content) : "" }));
    if (Array.isArray(ev.refs) && ev.refs.length) {
      const refs = el("div", { class: "ri-snippet", text: "refs: " + ev.refs.map(String).join(", ") });
      li.appendChild(refs);
    }
    return li;
  }

  function formatTs(ts) {
    const d = new Date(ts);
    if (isNaN(d.getTime())) return String(ts);
    return d.toLocaleString();
  }

  // ---------- Sessions: live feed + runs (workflow) ----------

  // Relative timestamp, e.g. "12s ago" / "3m ago". It's cheap to recompute from
  // the absolute ts on every poll, which lets the feed re-stamp itself without
  // re-fetching anything.
  function relTime(ts) {
    const then = new Date(ts).getTime();
    if (isNaN(then)) return String(ts);
    const s = Math.max(0, Math.round((Date.now() - then) / 1000));
    if (s < 60) return s + "s ago";
    const m = Math.floor(s / 60);
    if (m < 60) return m + "m ago";
    const h = Math.floor(m / 60);
    if (h < 24) return h + "h ago";
    return Math.floor(h / 24) + "d ago";
  }

  // Source enum is fixed (see CONTRACT addendum), so the badge class is built
  // from a known key — never from free-form API text.
  const SOURCE_LABELS = {
    session_start: "session-start",
    user_prompt: "prompt",
    mcp: "mcp",
    ui: "ui",
    manual: "manual",
  };
  function sourceBadge(source) {
    const src = String(source || "ui");
    const known = Object.prototype.hasOwnProperty.call(SOURCE_LABELS, src);
    const cls = known ? src : "ui";
    return el("span", { class: "sx-badge src-" + cls, text: known ? SOURCE_LABELS[src] : src });
  }

  function sessionsTabActive() {
    return $("tab-sessions").classList.contains("active");
  }

  // ----- polling lifecycle -----

  // Start (or restart) the feed loop. Bails out unless the panel is actually
  // on-screen and visible, so a backgrounded tab or another panel never fires
  // requests at the local API for data the user can't see.
  function startSessionsPolling() {
    stopSessionsPolling();
    if (document.hidden || !sessionsTabActive()) return;
    startUpdatedTicker();           // the age indicator runs whenever the panel is visible
    if (!sessionsLive) return;      // paused: show data + aging stamp, but don't fetch
    pollSessions();                 // fetch immediately rather than waiting a full interval
    sessionsTimer = setInterval(pollSessions, SESSIONS_POLL_MS);
  }

  function stopSessionsPolling() {
    if (sessionsTimer) { clearInterval(sessionsTimer); sessionsTimer = null; }
    stopUpdatedTicker();
  }

  function startUpdatedTicker() {
    stopUpdatedTicker();
    updatedTicker = setInterval(renderUpdatedLabel, 1000);
  }
  function stopUpdatedTicker() {
    if (updatedTicker) { clearInterval(updatedTicker); updatedTicker = null; }
  }
  function renderUpdatedLabel() {
    const lbl = $("sx-updated");
    if (lastPollAt == null) { lbl.textContent = ""; return; }
    lbl.textContent = "updated " + Math.round((Date.now() - lastPollAt) / 1000) + "s ago";
  }

  async function pollSessions() {
    if (!currentProject) { renderFeed([]); renderRuns([]); return; }
    // Feed and runs are independent; one failing shouldn't blank the other.
    await Promise.all([loadFeed(), loadRuns()]);
  }

  function setSessionsLive(on) {
    sessionsLive = on;
    const btn = $("sx-live-toggle");
    btn.classList.toggle("paused", !on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    btn.textContent = on ? "● Live" : "● Paused";
    startSessionsPolling();
  }

  // API unreachable: don't throw and don't wipe whatever is already on screen —
  // just flag staleness. Pre-rendered empty-state hints stay as-is.
  function markSessionsOffline(e) {
    $("sx-updated").textContent = "offline";
    console.warn("sessions poll failed:", e && (e.message || e));
  }

  // ----- live feed -----
  async function loadFeed() {
    try {
      const res = await api("/retrievals", { project: currentProject, k: 30 });
      lastPollAt = Date.now();
      renderUpdatedLabel();
      renderFeed(res.retrievals || []);
    } catch (e) {
      markSessionsOffline(e);
    }
  }

  function renderFeed(items) {
    const list = $("sx-feed"), empty = $("sx-feed-empty");
    clear(list);
    if (!items.length) { show(empty); return; }
    hide(empty);
    // API returns newest first; keep that order, capped defensively.
    items.slice(0, 30).forEach((r) => list.appendChild(feedEntry(r)));
  }

  function feedEntry(r) {
    const li = el("li", { class: "sx-entry" });
    if (String(r.id) === activeFeedId) li.classList.add("active");

    const top = el("div", { class: "sx-entry-top" });
    top.appendChild(sourceBadge(r.source));
    top.appendChild(el("span", { class: "sx-ts", text: relTime(r.ts) }));
    li.appendChild(top);

    const prompt = String(r.prompt != null ? r.prompt : "").trim();
    li.appendChild(el("div", { class: "sx-entry-prompt", text: prompt || "(no prompt)" }));

    const nodeCount = Array.isArray(r.node_ids) ? r.node_ids.length : 0;
    const eventCount = Array.isArray(r.event_ids) ? r.event_ids.length : 0;
    const foot = el("div", { class: "sx-entry-foot" });
    foot.appendChild(el("span", { text: (r.token_estimate || 0) + " tok" }));
    foot.appendChild(el("span", { text: nodeCount + " nodes · " + eventCount + " events" }));
    li.appendChild(foot);

    li.addEventListener("click", () => onFeedEntryClick(r, li));
    return li;
  }

  function onFeedEntryClick(r, li) {
    activeFeedId = String(r.id);
    document.querySelectorAll("#sx-feed .sx-entry.active").forEach((n) => n.classList.remove("active"));
    if (li) li.classList.add("active");
    // Reuse the shared graph spotlight: the nodes this retrieval injected light up.
    highlightNodeIds(r.node_ids || [], true);
    renderInjected(r);
  }

  // List what a retrieval injected: resolved node labels (clickable) plus the
  // ids of any events it pulled in.
  function renderInjected(r) {
    const sec = $("sx-detail"), ol = $("sx-detail-events");
    clear(ol);
    show(sec);
    const nodes = Array.isArray(r.nodes) ? r.nodes : [];
    const eventIds = Array.isArray(r.event_ids) ? r.event_ids : [];
    if (!nodes.length && !eventIds.length) {
      ol.appendChild(el("li", { class: "tl-item muted", text: "Nothing was injected." }));
      return;
    }
    nodes.forEach((n) => ol.appendChild(injectedNodeItem(n)));
    eventIds.forEach((id) => {
      const item = el("li", { class: "tl-item" });
      const meta = el("div", { class: "tl-meta" });
      meta.appendChild(el("span", { class: "tl-kind kind-note", text: "event" }));
      item.appendChild(meta);
      item.appendChild(el("div", { class: "tl-content", text: "#" + String(id) }));
      ol.appendChild(item);
    });
  }

  function injectedNodeItem(n) {
    const li = el("li", { class: "tl-item" });
    const meta = el("div", { class: "tl-meta" });
    const kind = String(n.kind || "node");
    const k = el("span", { class: "tl-kind", text: kind });
    k.style.color = colorFor(kind);
    meta.appendChild(k);
    li.appendChild(meta);
    li.appendChild(el("div", { class: "tl-content", text: n.label != null ? String(n.label) : String(n.id) }));
    li.style.cursor = "pointer";
    li.addEventListener("click", () => {
      const target = cy.getElementById(String(n.id));
      if (target && target.nonempty()) { cy.elements().unselect(); target.select(); onNodeSelected(target); }
    });
    return li;
  }

  // ----- runs (workflow) -----
  async function loadRuns() {
    try {
      const res = await api("/runs", { project: currentProject, k: 20 });
      renderRuns(res.runs || []);
    } catch (e) {
      markSessionsOffline(e);
    }
  }

  function renderRuns(runs) {
    const list = $("sx-runs"), empty = $("sx-runs-empty");
    clear(list);
    if (!runs.length) { show(empty); return; }
    hide(empty);
    runs.forEach((run) => list.appendChild(runRow(run)));
  }

  function runRow(run) {
    const li = el("li", { class: "sx-entry" });
    if (String(run.id) === activeRunId) li.classList.add("active");

    const top = el("div", { class: "sx-entry-top" });
    top.appendChild(sourceBadge(run.source));
    const isActive = run.status === "active";
    top.appendChild(el("span", { class: "sx-run-status " + (isActive ? "active" : "done"),
                                 text: String(run.status || "done") }));
    top.appendChild(el("span", { class: "sx-ts", text: relTime(run.started) }));
    li.appendChild(top);

    const label = String(run.label != null ? run.label : "").trim();
    li.appendChild(el("div", { class: "sx-entry-prompt", text: label || String(run.id) }));

    const foot = el("div", { class: "sx-entry-foot" });
    foot.appendChild(el("span", {
      text: (run.retrieval_count || 0) + " retrievals · " + (run.event_count || 0) + " events",
    }));
    li.appendChild(foot);

    li.addEventListener("click", () => onRunClick(run.id, li));
    return li;
  }

  async function onRunClick(runId, li) {
    activeRunId = String(runId);
    document.querySelectorAll("#sx-runs .sx-entry.active").forEach((n) => n.classList.remove("active"));
    if (li) li.classList.add("active");
    try {
      const res = await api("/run/" + encodeURIComponent(runId));
      renderRunDetail(res.run || {}, res.steps || []);
    } catch (e) {
      renderRunDetail({ id: runId }, []);
      $("sx-run-steps").appendChild(el("li", { class: "tl-item muted", text: "Could not load run: " + (e.message || e) }));
    }
  }

  function renderRunDetail(run, steps) {
    show($("sx-run-detail"));
    $("sx-run-title").textContent = "Run · " + String(run.label || run.id || "run");
    const ol = $("sx-run-steps");
    clear(ol);
    if (!steps.length) {
      ol.appendChild(el("li", { class: "tl-item muted", text: "No steps recorded." }));
      return;
    }
    // Steps arrive ordered by ts; render the retrieval/event mix as one timeline.
    steps.forEach((s) => ol.appendChild(runStepItem(s)));
  }

  function runStepItem(step) {
    const li = el("li", { class: "tl-item" });
    const meta = el("div", { class: "tl-meta" });

    if (step.type === "retrieval") {
      meta.appendChild(sourceBadge(step.source));
      if (step.ts) meta.appendChild(el("span", { class: "tl-ts", text: relTime(step.ts) }));
      li.appendChild(meta);
      const prompt = String(step.prompt != null ? step.prompt : "").trim();
      li.appendChild(el("div", { class: "tl-content", text: prompt || "(retrieval)" }));

      const nodeCount = Array.isArray(step.node_ids) ? step.node_ids.length : 0;
      const eventCount = Array.isArray(step.event_ids) ? step.event_ids.length : 0;
      const foot = el("div", { class: "sx-entry-foot" });
      foot.appendChild(el("span", { text: (step.token_estimate || 0) + " tok" }));
      foot.appendChild(el("span", { text: nodeCount + " nodes · " + eventCount + " events" }));
      li.appendChild(foot);

      if (nodeCount) {
        // A retrieval step re-lights its injected nodes, same as the feed.
        li.style.cursor = "pointer";
        li.addEventListener("click", () => highlightNodeIds(step.node_ids, true));
      }
    } else {
      const kind = String(step.kind || "note");
      meta.appendChild(el("span", { class: "tl-kind kind-" + kind, text: kind }));
      if (step.ts) meta.appendChild(el("span", { class: "tl-ts", text: relTime(step.ts) }));
      li.appendChild(meta);
      li.appendChild(el("div", { class: "tl-content", text: step.content != null ? String(step.content) : "" }));
    }
    return li;
  }

  // ---------- Tabs ----------
  function activateTab(name) {
    document.querySelectorAll(".tab").forEach((b) => {
      const on = b.dataset.tab === name;
      b.classList.toggle("active", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
    });
    $("panel-details").classList.toggle("hidden", name !== "details");
    $("panel-context").classList.toggle("hidden", name !== "context");
    $("panel-sessions").classList.toggle("hidden", name !== "sessions");
    if (name === "context") loadContext();
    // Only the Sessions panel polls; leaving it tears the loop down.
    if (name === "sessions") startSessionsPolling(); else stopSessionsPolling();
  }

  // ---------- Empty / loading states ----------
  function showEmptyState(msg) {
    if (msg) $("empty-msg").textContent = msg;
    show($("empty-state"));
  }
  function hideEmptyState() { hide($("empty-state")); }
  function setLoading(on) { on ? show($("loading")) : hide($("loading")); }

  // ---------- Project handling ----------
  function populateProjects(names, selected) {
    const sel = $("project-select");
    clear(sel);
    if (!names || names.length === 0) {
      const opt = el("option", { text: "(no projects)", attrs: { value: "" } });
      sel.appendChild(opt);
      sel.disabled = true;
      return;
    }
    sel.disabled = false;
    names.forEach((n) => sel.appendChild(el("option", { text: n, attrs: { value: n } })));
    if (selected && names.includes(selected)) sel.value = selected;
  }

  // The contract has no "list projects" endpoint; derive the candidate from URL ?project=
  // or remember last used, then probe /tree. Allow manual entry fallback.
  function initialProject() {
    const fromUrl = new URLSearchParams(location.search).get("project");
    if (fromUrl) return fromUrl;
    try { return localStorage.getItem("mrfox.project") || ""; } catch (e) { return ""; }
  }

  async function loadTreeFor(project) {
    currentProject = project || null;
    if (!currentProject) {
      showEmptyState("No project selected. Ingest a project, then reload.");
      return;
    }
    try { localStorage.setItem("mrfox.project", currentProject); } catch (e) {}
    setLoading(true);
    try {
      const tree = await api("/tree", { project: currentProject });
      // Ensure the selector shows this project even if discovery was empty.
      ensureProjectOption(currentProject);
      renderTree(tree);
    } catch (e) {
      showEmptyState("Could not load tree for \"" + currentProject + "\": " + (e.message || e));
    } finally {
      setLoading(false);
    }
  }

  function ensureProjectOption(name) {
    const sel = $("project-select");
    const exists = Array.from(sel.options).some((o) => o.value === name);
    if (!exists) {
      sel.disabled = false;
      sel.appendChild(el("option", { text: name, attrs: { value: name } }));
    }
    sel.value = name;
  }

  // ---------- Wire up ----------
  function bindUI() {
    $("search-form").addEventListener("submit", (e) => {
      e.preventDefault();
      const q = $("search-input").value.trim();
      runSearch(q);
    });
    $("search-clear").addEventListener("click", () => {
      $("search-input").value = "";
      clearHighlights();
    });
    $("reload-btn").addEventListener("click", () => loadTreeFor($("project-select").value));
    $("empty-reload").addEventListener("click", () => boot());
    $("project-select").addEventListener("change", (e) => loadTreeFor(e.target.value));
    $("ctx-refresh").addEventListener("click", loadContext);
    $("ctx-add").addEventListener("submit", (e) => { e.preventDefault(); saveContextNote(); });
    document.querySelectorAll(".tab").forEach((b) =>
      b.addEventListener("click", () => activateTab(b.dataset.tab))
    );

    // Sessions controls.
    $("sx-live-toggle").addEventListener("click", () => setSessionsLive(!sessionsLive));
    $("sx-run-close").addEventListener("click", () => { hide($("sx-run-detail")); activeRunId = null; });

    // Pause polling for a backgrounded tab; resume when it (and the panel) return.
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) stopSessionsPolling();
      else if (sessionsTabActive()) startSessionsPolling();
    });
  }

  async function boot() {
    const ok = await loadHealth();
    if (!ok) {
      showEmptyState("Cannot reach the core API at this origin. Start it with: make serve");
      return;
    }
    // List every ingested project so the dropdown can switch between them.
    let names = [];
    try {
      const res = await api("/projects");
      names = (res.projects || []).map((p) => String(p.id));
    } catch (e) { /* fall back to single-project mode below */ }
    let proj = initialProject();
    if (!proj && names.length) proj = names[0];
    if (proj && !names.includes(proj)) names.unshift(proj);
    populateProjects(names, proj);
    await loadTreeFor(proj);
  }

  // ---------- Init ----------
  document.addEventListener("DOMContentLoaded", () => {
    initCy();
    buildLegend();
    bindUI();
    boot();
  });
})();
