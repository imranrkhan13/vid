"use strict";

const API = "/api/v1";

const $ = (sel) => document.querySelector(sel);
const el = (tag, props = {}, ...children) => {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key in node) node[key] = value; // property (e.g. textContent, value)
    else node.setAttribute(key, value); // attribute (e.g. data-*, aria-*)
  }
  for (const c of children) node.append(c);
  return node;
};

async function api(path, options = {}) {
  const res = await fetch(API + path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${text}`);
  }
  return res.headers.get("content-type")?.includes("application/json")
    ? res.json()
    : res.text();
}

function setHealth(state, text) {
  const node = $("#health");
  node.textContent = text;
  node.className = `health ${state}`;
}

async function checkHealth() {
  try {
    const data = await fetch("/health").then((r) => r.json());
    setHealth("ok", `online · v${data.version}`);
  } catch {
    setHealth("bad", "offline");
  }
}

let currentProject = null;

function renderProject(project) {
  currentProject = project;
  $("#project-id").value = project.project_id;
  $("#project-status").textContent =
    `${project.project_id} — ${project.status} · ` +
    `${project.accepted_duration_seconds.toFixed(1)}/${project.target_duration_seconds}s`;
  const pct = project.target_duration_seconds
    ? (100 * project.accepted_duration_seconds) / project.target_duration_seconds
    : 0;
  $("#progress-bar").style.width = `${Math.min(100, pct)}%`;
}

function renderScenes(scenes) {
  const list = $("#scenes");
  list.replaceChildren(
    ...scenes.map((s) =>
      el(
        "li",
        {},
        el("span", { textContent: `#${s.index} · ${s.duration_seconds}s ${s.description.slice(0, 40)}` }),
        el("span", { className: `badge ${s.status}`, textContent: s.status })
      )
    )
  );
}

function renderProviders(providers) {
  const list = $("#providers");
  list.replaceChildren(
    ...providers.map((p) =>
      el(
        "li",
        {},
        el("span", { textContent: `${p.name} (${p.category})` }),
        el("span", {
          className: `badge ${p.is_mock ? "mock" : ""}`,
          textContent: p.is_mock ? "mock" : p.provider_id,
        })
      )
    )
  );
}

// --- unified API-key entry -------------------------------------------------

let keyRows = [];

function keyInput(provider) {
  const input = el("input", {
    type: "password",
    placeholder: provider.has_key
      ? `configured (${provider.source}) — enter to replace`
      : `paste ${provider.env_var || "API key"}`,
    autocomplete: "off",
    "data-provider": provider.provider_id,
  });
  input.addEventListener("input", () => {
    $("#keys-status").textContent = "unsaved changes";
  });
  return input;
}

function renderKeys(meta) {
  keyRows = meta.providers;
  $("#keys-encryption").textContent = meta.encrypted ? "encrypted at rest" : "plaintext file";
  $("#keys-encryption").className = `badge ${meta.encrypted ? "configured" : ""}`;

  const list = $("#keys");
  list.replaceChildren(
    ...meta.providers.map((p) => {
      const name = el(
        "span",
        { className: "name" },
        el("span", { textContent: p.name }),
        el("small", { textContent: `${p.provider_id} · ${p.category}${p.env_var ? " · " + p.env_var : ""}` })
      );
      const badges = el("div", { className: "state" });
      if (p.free) badges.append(el("span", { className: "badge free", textContent: "free" }));
      if (p.is_mock) badges.append(el("span", { className: "badge mock", textContent: "mock" }));
      if (p.has_key) badges.append(el("span", { className: "badge configured", textContent: "key set" }));
      const right = el("div", { className: "state" }, badges);
      if (p.requires_key) right.append(keyInput(p));
      return el("li", {}, name, right, el("span"));
    })
  );
  $("#keys-status").textContent =
    `${meta.configured_count} of ${meta.providers.length} providers have keys · ${meta.keys_file}`;
}

async function loadKeys() {
  renderKeys(await api("/keys"));
}

async function saveAllKeys() {
  const inputs = Array.from(document.querySelectorAll("#keys input[data-provider]"));
  const keys = {};
  for (const input of inputs) {
    const value = input.value.trim();
    if (value) keys[input.dataset.provider] = value;
  }
  if (Object.keys(keys).length === 0) {
    $("#keys-status").textContent = "nothing to save";
    return;
  }
  const res = await api("/keys/bulk", { method: "PUT", body: JSON.stringify({ keys }) });
  inputs.forEach((i) => (i.value = ""));
  $("#keys-status").textContent = `saved ${res.stored.length} key(s)`;
  await loadKeys();
}

function addEvent(evt) {
  const list = $("#events");
  const line = el("li", {
    textContent: `${evt.event_type}${evt.scene_id ? " · " + evt.scene_id : ""}`,
  });
  list.prepend(line);
  while (list.children.length > 40) list.lastChild.remove();
}

async function loadProject(id) {
  const project = await api(`/projects/${id}`);
  renderProject(project);
  const scenes = await api(`/projects/${id}/scenes`);
  renderScenes(scenes);
  return project;
}

async function refreshEvents(id) {
  const events = await api(`/projects/${id}/events`);
  $("#events").replaceChildren();
  events.slice(-40).forEach(addEvent);
}

async function createProject(form) {
  const data = Object.fromEntries(new FormData(form));
  data.target_duration_seconds = Number(data.target_duration_seconds);
  const project = await api("/projects", { method: "POST", body: JSON.stringify(data) });
  renderProject(project);
  $("#scenes").replaceChildren();
  $("#events").replaceChildren();
  return project;
}

async function plan(id) {
  const scenes = await api(`/projects/${id}/plan`, {
    method: "POST",
    body: JSON.stringify({}),
  });
  renderScenes(scenes);
  await loadProject(id);
  await refreshEvents(id);
}

async function generate(id) {
  const report = await api(`/projects/${id}/generate`, {
    method: "POST",
    body: JSON.stringify({ allow_paid_spend: false }),
  });
  $("#report").textContent = JSON.stringify(report, null, 2);
  await loadProject(id);
  await refreshEvents(id);
}

async function exportProject(id) {
  const job = await api(`/projects/${id}/export`, {
    method: "POST",
    body: JSON.stringify({}),
  });
  const note =
    job.status === "READY"
      ? `Export ready: /api/v1/exports/${job.export_id}/download`
      : `Export ${job.status} (composition is manifest-only without ffmpeg)`;
  $("#report").textContent = JSON.stringify(job, null, 2) + "\n\n" + note;
}

function wire() {
  $("#create-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await createProject(e.target);
    } catch (err) {
      setHealth("bad", String(err));
    }
  });

  $("#load-project").addEventListener("click", () => {
    const id = $("#project-id").value.trim();
    if (id) loadProject(id).catch((err) => setHealth("bad", String(err)));
  });

  $("#plan").addEventListener("click", () => {
    if (currentProject) plan(currentProject.project_id).catch((err) => setHealth("bad", String(err)));
  });

  $("#generate").addEventListener("click", () => {
    if (currentProject) generate(currentProject.project_id).catch((err) => setHealth("bad", String(err)));
  });

  $("#export").addEventListener("click", () => {
    if (currentProject) exportProject(currentProject.project_id).catch((err) => setHealth("bad", String(err)));
  });

  $("#save-all-keys").addEventListener("click", () => {
    saveAllKeys().catch((err) => ($("#keys-status").textContent = String(err)));
  });
}

(async function init() {
  await checkHealth();
  wire();
  try {
    renderProviders(await api("/providers"));
  } catch {
    /* providers endpoint optional at first paint */
  }
  try {
    await loadKeys();
  } catch (err) {
    $("#keys-status").textContent = String(err);
  }
})();
