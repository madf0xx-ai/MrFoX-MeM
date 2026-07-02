# MrFoX-MeM Web UI — Security Audit (output encoding of untrusted ingested content)

**Scope:** `ui/index.html`, `ui/app.js`, `ui/style.css`, served same-origin by `core/api.py`.
**Threat model:** A user ingests a *malicious repo*. Node `label`/`summary`/`path`/`snippet` and
event `content`/`kind`/`refs` are attacker-controlled and flow `/tree`, `/search`, `/context`,
`/health` → DOM. The UI's entire security rests on output encoding.

**Headline result:** **No confirmed XSS sink.** All API-derived strings reach the DOM via
`textContent` / `setAttribute`, never via `innerHTML`/`insertAdjacentHTML`/`outerHTML`/
`document.write`/`new Function`/`eval`. Cytoscape renders labels on a `<canvas>`, not as HTML, and
no HTML-label extension is loaded. Findings below are **defense-in-depth gaps**, ranked.

---

## 1. DOM XSS / Stored XSS — traced every sink (CONFIRMED SAFE)

Central DOM builder `el()` (`app.js:31-40`) sets text only via `node.textContent = String(...)`
(line 35) and attributes via `node.setAttribute(k, String(...))` (line 37). It never assigns
`innerHTML` and never sets event-handler/`href`/`src` attributes. Every renderer uses it or
`textContent` directly:

| Untrusted field | Render site | Sink | Verdict |
|---|---|---|---|
| node `label` | `onNodeSelected` `app.js:232` | `textContent` | SAFE |
| node `kind` | `app.js:235` | `textContent` (+ `colorFor` for style) | SAFE |
| node `path` | `app.js:239` | `textContent` | SAFE |
| node `summary` | `app.js:240` | `textContent` | SAFE |
| related `label`/`snippet`/`kind` | `relatedItem` `app.js:307,310,317` | `el()` `text` → `textContent` | SAFE |
| search `snippet`/`label` | via `relatedItem`/details | `textContent` | SAFE |
| event `content` | `timelineItem` `app.js:387` | `el()` `text` → `textContent` | SAFE |
| event `kind` | `app.js:383-384` | `textContent` + class (see Finding 3) | SAFE for script |
| event `refs[]` | `app.js:389` | `.map(String).join(", ")` → `textContent` | SAFE |
| `health.embed_backend`/`version` | `app.js:79-82` | `textContent` | SAFE |
| error `data.error` | `app.js:65`, `:458`, `:375` | `textContent` | SAFE |

`grep -niE "innerHTML|outerHTML|insertAdjacentHTML|document.write|\.html\(|eval\(|new Function"`
over `ui/` returns only the reassuring comment at `app.js:2`. **No sink exists.**

PoC that does **NOT** fire (demonstrates the control works): ingest a file whose node summary is
`<img src=x onerror=alert(1)>`. On node click it lands at `app.js:240`
`$("d-summary").textContent = d.summary` → rendered as literal text, no execution. Same for an
event `content` of `<script>...</script>` (`app.js:387`).

## 2. Cytoscape sinks (CONFIRMED SAFE)

`initCy()` (`app.js:107-162`) uses `"label": "data(label)"` (`app.js:120`). Cytoscape's core
canvas renderer draws labels as text on `<canvas>` — not HTML — so markup in a label cannot form
DOM. No `cytoscape-node-html-label` / popper / tippy / HTML-tooltip extension is loaded
(`index.html` loads only `cytoscape.min.js`). Node `data` (`app.js:184-194`) is plain strings used
for canvas label, color lookup, and selection logic; none is concatenated into innerHTML. **SAFE.**

## 3. `?project=` / URL & param handling (CONFIRMED SAFE)

`initialProject()` (`app.js:438-442`) reads `?project=` and `localStorage`. The value is:
(a) passed to `api()` where `URLSearchParams.set` percent-encodes it (`app.js:48-53`);
(b) shown in empty-state/error text via `textContent` (`app.js:178,447,458`);
(c) stored in `localStorage`; (d) used as an `<option>` value/text via `el()` (`app.js:432,469`).
It is **never** written to `location`, `href`, `window.open`, or innerHTML. **No reflected XSS, no
open redirect.** (Minor: `?project=` is attacker-supplyable via a crafted link but only triggers a
same-origin `/tree` fetch for a project name — no injection, low impact on a localhost tool.)

## 4. Fetch safety (CONFIRMED SAFE)

`api()` (`app.js:46-72`): relative paths only (no user-controlled base/host), `GET`,
`URLSearchParams`-encoded query, `AbortController` 15 s timeout (`app.js:55`),
`credentials:"same-origin"`. No SSRF/cross-origin egress from the UI layer. **SAFE.**

---

## 5. CSP / headers / SRI — **the real gaps (Medium)**

- **No Content-Security-Policy.** `core/api.py` has only `CORSMiddleware` (`api.py:33-39`); UI is
  served by `StaticFiles` (`api.py:227-228`) with no security-headers middleware, and `index.html`
  has no `<meta http-equiv="Content-Security-Policy">`. There is **zero second line of defense**: if
  any future edit introduces an `innerHTML`, or the CDN is compromised, nothing constrains script
  execution, inline handlers, or exfiltration.  **SUSPECTED-impact / CONFIRMED-absent.**
- **No Subresource Integrity (SRI) on the CDN script.** `index.html:8` loads
  `https://cdn.jsdelivr.net/npm/cytoscape@3.30.2/dist/cytoscape.min.js` with **no `integrity=` and
  no `crossorigin=`**. Version *is* pinned (3.30.2 — good), but a jsDelivr/CDN compromise or
  account/registry tampering yields full same-origin script execution. **CONFIRMED.**

## 6. Clickjacking / misc (Low)

- No `X-Frame-Options` / CSP `frame-ancestors`. The page is framable. Low risk given 127.0.0.1-only
  binding (`CONTRACT.md:84`) and `allow_credentials=False`, but a malicious page could frame the
  local UI; impact limited (no state-changing GETs in the UI). **CONFIRMED-absent.**
- **CSS class injection via `event.kind` (Low):** `timelineItem` builds
  `class: "tl-kind kind-" + kind` (`app.js:383`) and `el()` assigns it verbatim to `className`
  (`app.js:35`... actually `:36`). A `kind` containing spaces injects arbitrary CSS class *names*
  (e.g. `"x hidden"` could apply the `.hidden { display:none }` rule). No script execution and no
  attribute breakout (it's `className`, not innerHTML), so impact is cosmetic/UI-spoofing only.
  Same pattern, lower exposure, at `app.js:383` for the `kind-<x>` styling hook.
- `dt`/`dd`, `summary`, `tl-content` use `white-space:pre-wrap; word-break:break-word`
  (`style.css:186,224`) so long/hostile strings can't break layout meaningfully.

---

## Ranked remediation

1. **(Med) Add SRI to the Cytoscape CDN tag** — `index.html:8`:
   `integrity="sha384-…" crossorigin="anonymous"` (compute hash for 3.30.2), or vendor the file
   locally and drop the CDN entirely (no build step required). Closes the supply-chain path.
2. **(Med) Add a strict CSP** as a server response header in `core/api.py` (middleware on the UI
   mount) or a `<meta http-equiv>` in `index.html`, e.g.
   `default-src 'none'; script-src 'self' https://cdn.jsdelivr.net; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'`.
   This both blocks future-regression XSS and provides `frame-ancestors 'none'` (anti-clickjacking).
   (If you vendor Cytoscape per #1, tighten `script-src` to `'self'`.)
3. **(Low) Add `X-Frame-Options: DENY`** (or rely on CSP `frame-ancestors 'none'`) and
   `X-Content-Type-Options: nosniff`.
4. **(Low) Sanitize `kind` before class concatenation** — whitelist
   (`["decision","work","note","prompt"]`, else `"note"`) at `app.js:383`, or strip non-`[a-z-]`.
   Prevents CSS-class injection / UI spoofing.

## Attack-surface map

| Surface | Control present? | Gap |
|---|---|---|
| Ingested text → DOM | `textContent`/`setAttribute` everywhere | none (robust) |
| Cytoscape labels/data | canvas text, no HTML-label ext | none |
| `?project=` reflection | encoded into fetch + textContent | none |
| Fetch egress | relative+same-origin+timeout | none |
| Script integrity | version pinned | **no SRI** (Med) |
| Defense-in-depth | — | **no CSP** (Med) |
| Framing | — | no X-Frame-Options/frame-ancestors (Low) |
| `kind` → className | — | CSS-class injection (Low) |
