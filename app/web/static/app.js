// Dependency-free client for the knowledge engine API.
// Everything shown comes from the endpoints documented at /docs; there is no private rendering
// path to keep in sync, and no data is fabricated in the browser.

const state = {
  token: localStorage.getItem("token") || "",
  collections: [],
  mode: "hybrid",
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

const MODE_HINTS = {
  lexical: "Lexical counts exact term occurrences — what technical strings like QLoRA or BM25 need.",
  semantic: "Semantic embeds the query and searches the stored evidence vectors.",
  hybrid: "Hybrid fuses the lexical and dense rankings with reciprocal rank fusion, then reranks.",
};

function headers() {
  // A bearer token wins when present; otherwise the development identity header is sent.
  return state.token
    ? { "Content-Type": "application/json", Authorization: `Bearer ${state.token}` }
    : { "Content-Type": "application/json", "X-User-Id": $("#user-id").value.trim() || "local-user" };
}

async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: headers() });
  const text = await response.text();
  const body = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const detail = body && body.detail ? body.detail : response.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return body;
}

let toastTimer;
function toast(message, isError = false) {
  const element = $("#toast");
  element.textContent = message;
  element.classList.toggle("error", isError);
  element.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => element.classList.add("hidden"), 4500);
}

const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const pill = (status) => `<span class="pill pill-${esc(status)}">${esc(String(status).replace(/_/g, " "))}</span>`;
const chip = (text, extra = "") => `<span class="chip ${extra}">${esc(text)}</span>`;
const tile = (label, value, attention = false) =>
  `<div class="tile${attention ? " attention" : ""}"><div class="label">${esc(label)}</div><div class="value">${esc(value)}</div></div>`;

// ---------------------------------------------------------------- navigation

function show(view) {
  $$("nav button").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
  $$(".view").forEach((section) => section.classList.toggle("hidden", section.id !== `view-${view}`));
  if (view === "pipeline") loadPipeline();
  if (view === "review") loadReview();
}

$$("nav button").forEach((button) => button.addEventListener("click", () => show(button.dataset.view)));
$$("[data-goto]").forEach((button) => button.addEventListener("click", () => show(button.dataset.goto)));

// -------------------------------------------------------------- collections

async function loadCollections() {
  try {
    state.collections = await api("/api/collections");
  } catch (error) {
    toast(error.message, true);
    return;
  }

  // The listing carries reel_count, so this renders without a request per collection.
  const card = (collection, manageable) => `<li class="result" data-collection="${collection.id}">
      <div class="result-head">
        <strong>${esc(collection.name)}</strong>
        <span class="score">${collection.reel_count} Reel${collection.reel_count === 1 ? "" : "s"}</span>
      </div>
      ${collection.description ? `<div class="source-line">${esc(collection.description)}</div>` : ""}
      ${
        manageable
          ? `<div class="row">
              <button class="ghost small" data-expand="${collection.id}">View Reels</button>
              <button class="ghost small danger" data-delete-collection="${collection.id}"
                      data-name="${esc(collection.name)}" data-count="${collection.reel_count}">Delete collection</button>
             </div>
             <div class="nested hidden" data-reels-for="${collection.id}"></div>`
          : ""
      }
    </li>`;

  $("#dash-collections").innerHTML = state.collections.length
    ? state.collections.map((c) => card(c, false)).join("")
    : '<li class="empty">No collections yet.</li>';
  $("#collection-list").innerHTML = state.collections.length
    ? state.collections.map((c) => card(c, true)).join("")
    : '<li class="empty">No collections yet.</li>';

  for (const id of ["#reel-collection", "#upload-collection", "#search-collection", "#ask-collection", "#workout-collection"]) {
    const select = $(id);
    const previous = select.value;
    select.innerHTML = state.collections
      .map((collection) => `<option value="${collection.id}">${esc(collection.name)}</option>`)
      .join("");
    if (previous) select.value = previous;
  }
}

$("#new-collection").addEventListener("submit", async (event) => {
  event.preventDefault();
  const name = new FormData(event.target).get("name");
  try {
    await api("/api/collections", { method: "POST", body: JSON.stringify({ name }) });
    event.target.reset();
    toast(`Created "${name}"`);
    await refresh();
  } catch (error) {
    toast(error.message, true);
  }
});

$("#new-reel").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  try {
    await api("/api/reels", {
      method: "POST",
      body: JSON.stringify({
        collection_id: Number(form.get("collection_id")),
        instagram_url: form.get("instagram_url"),
      }),
    });
    event.target.reset();
    toast("Reel queued for processing");
    await refresh();
  } catch (error) {
    toast(error.message, true);
  }
});

$("#refresh-collections").addEventListener("click", refresh);

// ------------------------------------------------- browsing and deleting

async function showCollectionReels(collectionId) {
  const panel = $(`[data-reels-for="${collectionId}"]`);
  if (!panel) return;
  if (!panel.classList.contains("hidden")) {
    panel.classList.add("hidden");
    return;
  }
  panel.classList.remove("hidden");
  panel.innerHTML = '<p class="empty">Loading…</p>';

  let reels;
  try {
    reels = await api(`/api/reels/collection/${collectionId}?limit=100`);
  } catch (error) {
    panel.innerHTML = `<p class="empty">${esc(error.message)}</p>`;
    return;
  }
  if (!reels.length) {
    panel.innerHTML = '<p class="empty">This collection has no Reels yet.</p>';
    return;
  }
  panel.innerHTML = `<ul class="cards">${reels
    .map(
      (reel) => `<li class="result">
        <div class="result-head">
          <strong>Reel #${reel.id}</strong>
          ${pill(reel.status)}
        </div>
        <div class="chips">
          ${reel.creator_username ? chip(reel.creator_username) : ""}
          ${chip(reel.processing_version)}
        </div>
        ${reel.caption ? `<div class="source-line">${esc(reel.caption)}</div>` : ""}
        ${reel.error_message ? `<div class="snippet">${esc(reel.error_message)}</div>` : ""}
        <div class="row">
          <button class="ghost small" data-audit="${reel.id}">Evidence</button>
          ${reel.status === "pending" ? `<button class="small" data-process="${reel.id}">Process</button>` : ""}
          <button class="ghost small danger" data-delete-reel="${reel.id}"
                  data-collection="${collectionId}">Delete</button>
          <a href="${esc(reel.instagram_url)}" target="_blank" rel="noopener noreferrer">Open</a>
        </div>
      </li>`
    )
    .join("")}</ul>`;
}

// A URL-only Reel has no media and can never be processed, so this is the only route from the
// interface into the actual pipeline. It posts multipart rather than JSON, which means bypassing
// the `api()` helper: the browser must set its own Content-Type boundary.
$("#upload-reel").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const file = form.media.files[0];
  if (!file) return toast("Choose a video file first", true);

  const status = $("#upload-status");
  const button = form.querySelector("button");
  button.disabled = true;
  status.textContent = `Uploading ${file.name} (${(file.size / 1048576).toFixed(1)} MB)…`;

  const body = new FormData();
  body.append("collection_id", form.collection_id.value);
  body.append("media", file, file.name);

  const auth = state.token
    ? { Authorization: `Bearer ${state.token}` }
    : { "X-User-Id": $("#user-id").value.trim() || "local-user" };

  try {
    const response = await fetch("/api/reels/upload", { method: "POST", headers: auth, body });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail?.message || JSON.stringify(payload.detail));

    const reelId = payload.reel_id;
    const url = form.instagram_url.value.trim();
    if (url) {
      // Recorded as the caption so the audit view can cite where the video came from.
      await api(`/api/reels/${reelId}`, { method: "PATCH", body: JSON.stringify({ caption: url }) });
    }
    form.reset();
    status.textContent = `Uploaded as Reel #${reelId}. Queueing analysis…`;
    await dispatchReel(reelId, status);
    await refresh();
  } catch (error) {
    status.textContent = `Upload failed: ${error.message}`;
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
});

async function dispatchReel(reelId, status) {
  try {
    const job = await api(`/api/jobs/reel/${reelId}`);
    await api(`/api/jobs/${job.id}/dispatch`, { method: "POST" });
    status.textContent = `Reel #${reelId} queued. Watch it progress under Recent processing.`;
  } catch (error) {
    // The common case on a local install: no Celery worker or Redis running. Say so plainly
    // rather than leaving a Reel sitting at "pending" with no explanation.
    status.textContent =
      `Reel #${reelId} uploaded, but queueing failed. ${error.message} ` +
      `The media is stored, so press Process on the dashboard once the worker is running.`;
  }
}

// --------------------------------------------------------- dashboard tiles

async function loadDashboard() {
  let metrics = null;
  try {
    metrics = await api("/api/system/metrics");
  } catch (error) {
    $("#tiles").innerHTML = `<div class="empty">${esc(error.message)}</div>`;
    return;
  }
  const reels = metrics.reels_by_status || {};
  const total = Object.values(reels).reduce((sum, count) => sum + count, 0);
  const analysed = (reels.completed || 0) + (reels.needs_review || 0);
  $("#tiles").innerHTML =
    tile("Total Reels", total) +
    tile("Analysed", analysed) +
    tile("Needs review", metrics.review_queue_size || 0, (metrics.review_queue_size || 0) > 0) +
    tile("Collections", metrics.collections || 0);
}

async function loadJobs() {
  const body = $("#jobs tbody");
  let jobs;
  try {
    jobs = await api("/api/jobs?limit=10");
  } catch (error) {
    body.innerHTML = `<tr><td colspan="5" class="empty">${esc(error.message)}</td></tr>`;
    return;
  }
  body.innerHTML = jobs.length
    ? jobs
        .map(
          (job) => `<tr>
            <td>#${job.reel_id}</td>
            <td>${esc(job.current_stage)}</td>
            <td>${pill(job.status)}</td>
            <td class="num tabular">${job.attempt_count}</td>
            <td class="num">
              ${job.current_stage === "ingestion" ? `<button class="small" data-process="${job.reel_id}">Process</button> ` : ""}
              <button class="ghost small" data-audit="${job.reel_id}">Audit</button>
            </td>
          </tr>`
        )
        .join("")
    : '<tr><td colspan="5" class="empty">Nothing processing yet.</td></tr>';
}

// ------------------------------------------------------------------ search

$$("#mode-select button").forEach((button) =>
  button.addEventListener("click", () => {
    state.mode = button.dataset.mode;
    $$("#mode-select button").forEach((other) => other.classList.toggle("active", other === button));
    $("#mode-hint").textContent = MODE_HINTS[state.mode];
  })
);

$("#reindex").addEventListener("click", async () => {
  const collectionId = $("#search-collection").value;
  if (!collectionId) return toast("Create a collection first", true);
  try {
    const result = await api(`/api/collections/${collectionId}/index`, { method: "POST" });
    toast(`Indexed ${result.indexed_chunks} chunks from ${result.indexed_reels} Reels`);
  } catch (error) {
    toast(error.message, true);
  }
});

$("#search-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  const payload = {
    collection_id: Number(form.get("collection_id")),
    query: form.get("query"),
    mode: state.mode,
    limit: 20,
  };
  for (const key of ["creator", "topic", "content_type"]) {
    const value = (form.get(key) || "").trim();
    if (value) payload[key] = value;
  }
  try {
    renderResults(await api("/api/search", { method: "POST", body: JSON.stringify(payload) }));
  } catch (error) {
    toast(error.message, true);
  }
});

function renderResults(results) {
  const list = $("#search-results");
  if (!results.length) {
    list.innerHTML = '<li class="empty">No matches. Try another mode, adjust the filters, or rebuild the index.</li>';
    return;
  }
  list.innerHTML = results
    .map(
      (result) => `<li class="result">
        <div class="result-head">
          <strong>Reel #${result.reel_id}</strong>
          <span class="score">score ${result.score}</span>
        </div>
        <div class="chips">
          ${chip(result.match_type, "chip-match")}
          ${result.creator ? chip(result.creator) : ""}
          ${(result.topics || []).map((topic) => chip(topic)).join("")}
        </div>
        ${(result.snippets || []).map((snippet) => `<div class="snippet">${esc(snippet)}</div>`).join("")}
        <div class="row">
          <button class="ghost small" data-audit="${result.reel_id}">Evidence</button>
          <a href="${esc(result.instagram_url)}" target="_blank" rel="noopener noreferrer">Open Reel</a>
        </div>
      </li>`
    )
    .join("");
}

// --------------------------------------------------------------------- ask

$("#ask-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  const collectionId = form.get("collection_id");
  try {
    const answer = await api(`/api/collections/${collectionId}/ask`, {
      method: "POST",
      body: JSON.stringify({ question: form.get("question") }),
    });
    const sources = (answer.sources || [])
      .map(
        (source) => `<li class="result">
          <div class="result-head">
            <strong>Reel #${source.reel_id}</strong>
            <span class="score">${source.score}</span>
          </div>
          ${source.content_type ? `<div class="chips">${chip(source.content_type)}</div>` : ""}
          <div class="snippet">${esc(source.snippet)}</div>
          <div class="row">
            <button class="ghost small" data-audit="${source.reel_id}">Evidence</button>
            <a href="${esc(source.instagram_url)}" target="_blank" rel="noopener noreferrer">Open Reel</a>
          </div>
        </li>`
      )
      .join("");

    $("#answer-panel").innerHTML = `
      <div class="panel-head">
        <h2>Answer</h2>
        <span class="badge ${answer.grounded ? "tier-accepted" : "tier-review"}">
          ${answer.grounded ? "Grounded" : "Not grounded"} · ${esc(answer.model)}
        </span>
      </div>
      <p>${esc(answer.answer).replace(/\n/g, "<br>")}</p>
      ${
        answer.grounded
          ? `<h3 style="margin-top:16px">Sources</h3><ul class="cards">${sources}</ul>`
          : '<p class="hint">No sources — nothing in this collection was cited.</p>'
      }`;
  } catch (error) {
    toast(error.message, true);
  }
});

$("#load-report").addEventListener("click", async () => {
  const collectionId = $("#ask-collection").value;
  if (!collectionId) return toast("Create a collection first", true);
  try {
    const data = await api(`/api/reports/collections/${collectionId}/narrative`);
    const counts = (pairs) =>
      pairs.length ? pairs.map(([label, count]) => chip(`${label} ×${count}`)).join("") : '<span class="empty">none yet</span>';
    $("#report").innerHTML = `
      <p>${esc(data.summary)}</p>
      ${
        data.narrated && data.themes.length
          ? `<div class="chips" style="margin:8px 0">${data.themes.map((theme) => chip(theme)).join("")}</div>`
          : ""
      }
      <div class="source-line" style="margin:10px 0">
        ${data.report.total_reels} Reels · ${data.report.analyzed_reels} analysed ·
        avg confidence ${data.report.average_extraction_confidence ?? "—"}
      </div>
      <dl class="kv">
        <dt>Topics</dt><dd><div class="chips">${counts(data.report.top_topics)}</div></dd>
        <dt>Technologies</dt><dd><div class="chips">${counts(data.report.top_technologies)}</div></dd>
        <dt>Creators</dt><dd><div class="chips">${counts(data.report.top_creators)}</div></dd>
      </dl>`;
  } catch (error) {
    toast(error.message, true);
  }
});

// ----------------------------------------------------------------- workout

$("#workout-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  const list = (value) => (value || "").split(",").map((entry) => entry.trim()).filter(Boolean);
  try {
    const plan = await api("/api/workouts/generate", {
      method: "POST",
      body: JSON.stringify({
        collection_id: Number(form.get("collection_id")),
        days: Number(form.get("days")),
        exercises_per_day: Number(form.get("exercises_per_day")),
        available_equipment: list(form.get("available_equipment")),
        target_muscles: list(form.get("target_muscles")),
        allow_repeats: form.get("allow_repeats") === "on",
      }),
    });

    const seen = new Set();
    const exercise = (item) => {
      // Flag a repeat rather than presenting it as a fresh recommendation. The cause is not
      // stated, because with allow_repeats off the planner reuses the same top slice on every
      // day regardless of how many eligible exercises remain.
      const key = `${item.source_reel_id}:${item.name}`;
      const repeated = seen.has(key);
      seen.add(key);
      return `<li class="result${repeated ? " repeated" : ""}">
        <div class="result-head">
          <strong>${esc(item.name)}</strong>
          <span class="score tabular">${item.sets ?? "?"} × ${esc(item.reps ?? "?")}</span>
        </div>
        <div class="chips">
          ${(item.equipment || []).map((entry) => chip(entry)).join("")}
          ${(item.target_muscles || []).map((entry) => chip(entry)).join("")}
        </div>
        ${repeated ? '<div class="source-line">Repeated from an earlier day</div>' : ""}
        <div class="row">
          <span class="source-line">From Reel #${item.source_reel_id}</span>
          <button class="ghost small" data-audit="${item.source_reel_id}">Evidence</button>
          <a href="${esc(item.source_url)}" target="_blank" rel="noopener noreferrer">Open Reel</a>
        </div>
      </li>`;
    };

    $("#workout-plan").innerHTML = plan.days.length
      ? plan.days
          .map(
            (day) => `<div class="panel">
              <div class="day-head">Day ${day.day}</div>
              <ul class="cards">${day.exercises.map(exercise).join("")}</ul>
            </div>`
          )
          .join("")
      : '<div class="panel"><p class="empty">No extracted exercises match those filters yet.</p></div>';
  } catch (error) {
    toast(error.message, true);
  }
});

// ------------------------------------------------------------------ review

async function loadReview() {
  const list = $("#review-list");
  let reels;
  try {
    reels = await api("/api/reels/review/queue");
  } catch (error) {
    list.innerHTML = `<li class="empty">${esc(error.message)}</li>`;
    return;
  }
  list.innerHTML = reels.length
    ? reels
        .map(
          (reel) => `<li class="result">
            <div class="result-head">
              <strong>Reel #${reel.id}</strong>
              ${pill(reel.status)}
            </div>
            <div class="chips">
              ${reel.creator_username ? chip(reel.creator_username) : ""}
              ${chip(`version ${reel.processing_version}`)}
            </div>
            ${reel.caption ? `<div class="source-line">${esc(reel.caption)}</div>` : ""}
            ${reel.error_message ? `<div class="snippet">${esc(reel.error_message)}</div>` : ""}
            <div class="row">
              <button class="ghost small" data-audit="${reel.id}">Evidence</button>
              <button class="small" data-review="${reel.id}" data-decision="accepted">Accept</button>
              <button class="ghost small" data-review="${reel.id}" data-decision="rejected">Reject</button>
            </div>
          </li>`
        )
        .join("")
    : '<li class="empty">Nothing is awaiting review.</li>';
}

$("#refresh-review").addEventListener("click", loadReview);

// ---------------------------------------------------------------- pipeline

async function loadPipeline() {
  let data;
  try {
    data = await api("/api/system/metrics/pipeline");
  } catch (error) {
    toast(error.message, true);
    return;
  }
  const runs = data.stages.reduce((sum, stage) => sum + stage.runs, 0);
  $("#pipeline-tiles").innerHTML =
    tile("Analysed Reels", data.analyzed_reels) +
    tile("Degraded runs", data.degraded_reels, data.degraded_reels > 0) +
    tile("Stage runs", runs);

  $("#stage-table tbody").innerHTML = data.stages.length
    ? data.stages
        .map(
          (stage) => `<tr>
            <td>${esc(stage.stage)}</td>
            <td class="num tabular">${stage.runs}</td>
            <td class="num tabular">${stage.succeeded}</td>
            <td class="num tabular">${stage.degraded}</td>
            <td class="num tabular">${stage.skipped}</td>
            <td class="num tabular">${stage.failed}</td>
            <td class="num tabular${stage.failure_rate > 0 ? " flag" : ""}">${(stage.failure_rate * 100).toFixed(0)}%</td>
            <td class="num tabular">${stage.average_duration_ms}</td>
            <td class="num tabular">${stage.p95_duration_ms}</td>
            <td>${(stage.models || []).map((model) => chip(model)).join("") || '<span class="empty">—</span>'}</td>
          </tr>`
        )
        .join("")
    : '<tr><td colspan="10" class="empty">No analysis runs recorded yet.</td></tr>';

  const order = ["transcription", "ocr", "vision", "extraction"];
  const byStage = Object.fromEntries(data.stages.map((stage) => [stage.stage, stage]));
  $("#stage-sequence").innerHTML = order
    .map((name, index) => {
      const stage = byStage[name];
      // Dominant outcome, so the strip reflects what usually happens at this stage.
      let status = "skipped";
      if (stage) {
        if (stage.failed > 0) status = "failed";
        else if (stage.succeeded > 0) status = "succeeded";
      }
      const node = `<div class="stage-node ${status}">
          <div class="name">${esc(name)}</div>
          <div class="ms">${stage ? `${stage.average_duration_ms} ms` : "no runs"}</div>
        </div>`;
      return index < order.length - 1 ? `${node}<div class="stage-arrow">→</div>` : node;
    })
    .join("");
}

$("#refresh-pipeline").addEventListener("click", loadPipeline);

// ------------------------------------------------------- reel audit drawer

const EVIDENCE_ORDER = ["visual", "ocr", "transcript", "metadata", "inferred"];

function evidenceTag(kind) {
  const known = EVIDENCE_ORDER.includes(String(kind).toLowerCase());
  const weak = String(kind).toLowerCase() === "inferred" || !known;
  return `<span class="evidence${weak ? " evidence-inferred" : ""}">${esc(kind)}</span>`;
}

function confidenceBlock(confidence) {
  if (confidence === null || confidence === undefined) {
    return `<div class="confidence">
      <div class="value">—</div>
      <div class="tier tier-review">No confidence recorded</div>
      <div class="rule">Absent confidence routes to review.</div>
    </div>`;
  }
  // Mirrors the worker's three tiers so the UI never implies a different decision than the one made.
  const tier = confidence >= 0.85 ? "accepted" : confidence >= 0.6 ? "verified" : "review";
  const wording = {
    accepted: ["Accepted", "Above the 0.85 auto-accept threshold."],
    verified: ["Verified", "Between the thresholds — corroborated against stored OCR and transcript text."],
    review: ["Needs review", "Below the 0.60 review threshold."],
  }[tier];
  return `<div class="confidence">
    <div class="value">${confidence.toFixed(2)}</div>
    <div class="tier tier-${tier}">${wording[0]}</div>
    <div class="rule">${wording[1]}</div>
  </div>`;
}

function extractedPanel(item) {
  const evidence = (item.data && item.data.evidence) || {};
  const rows = Object.entries(item.data || {})
    .filter(([field]) => field !== "evidence")
    .map(([field, value]) => {
      const shown = Array.isArray(value) ? value.join(", ") : String(value);
      return `<tr>
        <td>${esc(field)}</td>
        <td>${esc(shown)}</td>
        <td>${evidence[field] ? evidenceTag(evidence[field]) : '<span class="empty">—</span>'}</td>
      </tr>`;
    })
    .join("");

  return `<div class="panel">
    <div class="panel-head">
      <h2>Extracted knowledge — ${esc(item.content_type)}</h2>
    </div>
    <div class="row" style="align-items:flex-start; gap:24px">
      <div class="grow scroll-x">
        <table><thead><tr><th>Field</th><th>Value</th><th>Evidence</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="3" class="empty">No fields.</td></tr>'}</tbody></table>
      </div>
      ${confidenceBlock(item.confidence)}
    </div>
  </div>`;
}

async function openAudit(reelId) {
  const backdrop = $("#drawer-backdrop");
  const drawer = $("#drawer");
  drawer.innerHTML = '<p class="empty">Loading evidence…</p>';
  backdrop.classList.remove("hidden");

  let analysis;
  let reel;
  try {
    [analysis, reel] = await Promise.all([api(`/api/reels/${reelId}/analysis`), api(`/api/reels/${reelId}`)]);
  } catch (error) {
    drawer.innerHTML = `<div class="drawer-head"><h1>Reel #${reelId}</h1><button class="ghost small" id="close-drawer">Close</button></div>
      <p class="empty">${esc(error.message)}</p>`;
    return;
  }

  // OCR observations and transcript segments both carry timestamps, so they read against the same
  // moment in the video.
  const timeline = [
    ...analysis.ocr_results.map((item) => ({ at: item.timestamp_seconds, text: item.text, kind: "ocr" })),
    ...((analysis.transcript && analysis.transcript.segments) || []).map((segment) => ({
      at: segment.start,
      text: segment.text,
      kind: "transcript",
    })),
  ].sort((a, b) => (a.at ?? 0) - (b.at ?? 0));

  const provenance = analysis.provenance
    ? Object.entries(analysis.provenance.models)
        .map(([key, value]) => `<dt>${esc(key)}</dt><dd>${value === null ? "—" : esc(value)}</dd>`)
        .join("") + `<dt>processing_version</dt><dd>${esc(analysis.provenance.processing_version)}</dd>`
    : "";

  drawer.innerHTML = `
    <div class="drawer-head">
      <div>
        <h1>Reel #${reel.id}</h1>
        <p class="source-line" style="margin-top:6px">
          ${reel.creator_username ? esc(reel.creator_username) + " · " : ""}${esc(reel.caption || "No caption")}
        </p>
        <div class="chips" style="margin-top:8px">
          ${pill(reel.status)}
          ${analysis.transcript ? chip("has speech") : chip("silent Reel — no speech detected")}
        </div>
      </div>
      <button class="ghost small" id="close-drawer">Close</button>
    </div>

    ${
      analysis.extracted_content.length
        ? analysis.extracted_content.map(extractedPanel).join("")
        : '<div class="panel"><p class="empty">No structured knowledge was extracted.</p></div>'
    }

    <div class="panel">
      <h2>Evidence timeline</h2>
      ${
        timeline.length
          ? `<div class="timeline">${timeline
              .map(
                (item) => `<div class="timeline-row">
                  <div class="at">${item.at === null || item.at === undefined ? "—" : Number(item.at).toFixed(1) + "s"}</div>
                  <div class="what"><span>${esc(item.text)}</span>${evidenceTag(item.kind)}</div>
                </div>`
              )
              .join("")}</div>`
          : '<p class="empty">No timestamped observations.</p>'
      }
      ${!analysis.transcript ? '<p class="hint">No transcript — this Reel has no speech.</p>' : ""}
    </div>

    ${
      analysis.visual_analysis
        ? `<div class="panel">
            <div class="panel-head">
              <h2>Visual analysis</h2>
              <span class="source-line">${esc(analysis.visual_analysis.model)}${
              analysis.visual_analysis.confidence !== null
                ? ` · confidence ${analysis.visual_analysis.confidence}`
                : ""
            }</span>
            </div>
            <div class="snippet">${esc(analysis.visual_analysis.description)}</div>
          </div>`
        : ""
    }

    ${
      provenance
        ? `<div class="panel"><h2>Processing provenance</h2><dl class="kv">${provenance}</dl>
           <p class="hint">Read back from what the run persisted, not from configuration as it stands now.</p></div>`
        : ""
    }`;
}

function closeDrawer() {
  $("#drawer-backdrop").classList.add("hidden");
}

$("#drawer-backdrop").addEventListener("click", (event) => {
  if (event.target.id === "drawer-backdrop" || event.target.id === "close-drawer") closeDrawer();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeDrawer();
});

// Delegated so buttons rendered into any list keep working.
document.addEventListener("click", async (event) => {
  const auditButton = event.target.closest("[data-audit]");
  if (auditButton) return openAudit(auditButton.dataset.audit);

  const expandButton = event.target.closest("[data-expand]");
  if (expandButton) {
    await showCollectionReels(expandButton.dataset.expand);
    expandButton.textContent =
      $(`[data-reels-for="${expandButton.dataset.expand}"]`).classList.contains("hidden")
        ? "View Reels"
        : "Hide Reels";
    return;
  }

  const deleteReelButton = event.target.closest("[data-delete-reel]");
  if (deleteReelButton) {
    const id = deleteReelButton.dataset.delete_reel || deleteReelButton.dataset.deleteReel;
    // Deletion removes the Reel, everything derived from it, and its local media. Say so before
    // doing it, because none of it comes back.
    if (!confirm(`Delete Reel #${id}?\n\nThis removes its transcript, OCR, visual analysis, extracted knowledge, embeddings and downloaded media. This cannot be undone.`)) {
      return;
    }
    try {
      await api(`/api/reels/${id}`, { method: "DELETE" });
      toast(`Deleted Reel #${id}`);
      const collectionId = deleteReelButton.dataset.collection;
      await loadCollections();
      if (collectionId) {
        // Re-open the list the user was looking at, rather than collapsing it under them.
        const panel = $(`[data-reels-for="${collectionId}"]`);
        if (panel) panel.classList.add("hidden");
        await showCollectionReels(collectionId);
      }
      await Promise.all([loadDashboard(), loadJobs()]);
    } catch (error) {
      toast(error.message, true);
    }
    return;
  }

  const deleteCollectionButton = event.target.closest("[data-delete-collection]");
  if (deleteCollectionButton) {
    const { name, count } = deleteCollectionButton.dataset;
    const id = deleteCollectionButton.dataset.delete_collection || deleteCollectionButton.dataset.deleteCollection;
    const reelWord = count === "1" ? "Reel" : "Reels";
    if (!confirm(`Delete the collection "${name}"?\n\nThis also deletes its ${count} ${reelWord} and everything extracted from them, including downloaded media. This cannot be undone.`)) {
      return;
    }
    try {
      await api(`/api/collections/${id}`, { method: "DELETE" });
      toast(`Deleted "${name}"`);
      await refresh();
    } catch (error) {
      toast(error.message, true);
    }
    return;
  }

  const processButton = event.target.closest("[data-process]");
  if (processButton) {
    await dispatchReel(processButton.dataset.process, $("#upload-status"));
    toast($("#upload-status").textContent);
    await loadJobs();
    return;
  }

  const reviewButton = event.target.closest("[data-review]");
  if (reviewButton) {
    try {
      await api(`/api/reels/${reviewButton.dataset.review}/review`, {
        method: "POST",
        body: JSON.stringify({ decision: reviewButton.dataset.decision }),
      });
      toast(`Reel #${reviewButton.dataset.review} ${reviewButton.dataset.decision}`);
      await Promise.all([loadReview(), loadDashboard(), loadJobs()]);
    } catch (error) {
      toast(error.message, true);
    }
  }
});

// ------------------------------------------------------------------ account

function renderAccount(message) {
  $("#account-state").textContent = message;
  $("#sign-out").classList.toggle("hidden", !state.token);
  $("#register").classList.toggle("hidden", Boolean(state.token));
  $("#user-id").disabled = Boolean(state.token);
  $("#who-note").textContent = state.token ? "Authenticated" : "Development identity";
}

async function authenticate(path) {
  const form = new FormData($("#login-form"));
  try {
    const result = await api(path, {
      method: "POST",
      body: JSON.stringify({ email: form.get("email"), password: form.get("password") }),
    });
    state.token = result.access_token;
    localStorage.setItem("token", state.token);
    $("#who").textContent = form.get("email");
    renderAccount(`Signed in as ${form.get("email")}`);
    toast("Signed in");
    await refresh();
  } catch (error) {
    toast(error.message, true);
  }
}

$("#login-form").addEventListener("submit", (event) => {
  event.preventDefault();
  authenticate("/api/auth/login");
});

$("#register").addEventListener("click", () => authenticate("/api/auth/register"));

$("#sign-out").addEventListener("click", async () => {
  state.token = "";
  localStorage.removeItem("token");
  $("#who").textContent = $("#user-id").value.trim() || "local-user";
  renderAccount("Using the development identity header.");
  await refresh();
});

$("#load-caps").addEventListener("click", async () => {
  try {
    const caps = await api("/api/capabilities");
    $("#caps").innerHTML = `
      <dl class="kv">
        <dt>Analysis</dt><dd>${caps.analysis.map(esc).join(", ")}</dd>
        <dt>Retrieval</dt><dd>${esc(caps.retrieval.modes.join(", "))} · ${esc(caps.retrieval.embedding_provider)} · ${esc(caps.retrieval.vector_store)}</dd>
        <dt>Answering</dt><dd>${esc(caps.answering.provider)}</dd>
        <dt>Object store</dt><dd>${esc(caps.object_storage.backend)}</dd>
        <dt>Auth</dt><dd>${caps.authentication.required ? "required" : "optional"}</dd>
      </dl>
      <h3 style="margin-top:16px">Known limitations</h3>
      <ul class="cards" style="margin-top:8px">
        ${caps.known_limitations.map((item) => `<li class="source-line">${esc(item)}</li>`).join("")}
      </ul>`;
  } catch (error) {
    toast(error.message, true);
  }
});

$("#user-id").addEventListener("change", () => {
  $("#who").textContent = $("#user-id").value.trim() || "local-user";
  refresh();
});

// --------------------------------------------------------------------- boot

async function refresh() {
  await loadCollections();
  await Promise.all([loadDashboard(), loadJobs()]);
}

renderAccount(state.token ? "Signed in." : "Using the development identity header.");
$("#who").textContent = state.token ? "authenticated" : "local-user";
refresh();
