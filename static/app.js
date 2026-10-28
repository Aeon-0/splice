"use strict";

const $ = (id) => document.getElementById(id);
let mode = "reel";
let job = null;
let selectedId = null;
let drafts = {};
let busy = false;
let pollTimer = null;
let pollingFailures = 0;

function store(key, value) {
  try { sessionStorage.setItem(key, JSON.stringify(value)); } catch { /* Private browsing can disable storage. */ }
}
function retrieve(key, fallback) {
  try { return JSON.parse(sessionStorage.getItem(key)) ?? fallback; } catch { return fallback; }
}
function message(id, value) {
  $(id).textContent = value || "";
  $(id).hidden = !value;
}
function seconds(value) { return `${Number(value).toFixed(2)}s`; }
function timestamp(value) {
  const minutes = Math.floor(value / 60);
  return `${minutes}:${(value % 60).toFixed(2).padStart(5, "0")}`;
}
function setBusy(value) {
  busy = value;
  document.querySelectorAll(".analyse-button").forEach((button) => {
    button.disabled = value;
    button.firstChild.textContent = value ? "Working locally… " : "Find the cuts ";
  });
}
function setMode(nextMode, focus = false) {
  mode = nextMode;
  document.querySelectorAll("[data-mode]").forEach((button) => {
    const active = button.dataset.mode === mode;
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
    const panel = $(`panel-${button.dataset.mode}`);
    panel.hidden = !active;
    panel.querySelectorAll("input").forEach((input) => { input.disabled = !active; });
    if (active && focus) button.focus();
  });
}
document.querySelectorAll("[data-mode]").forEach((button, index, buttons) => {
  button.addEventListener("click", () => setMode(button.dataset.mode));
  button.addEventListener("keydown", (event) => {
    if (!["ArrowRight", "ArrowLeft", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + buttons.length) % buttons.length;
    setMode(buttons[next].dataset.mode, true);
  });
});
setMode(mode);

async function api(url, options) {
  const response = await fetch(url, options);
  let payload;
  try { payload = await response.json(); } catch { throw new Error("The server returned an unexpected response. Check that Splice is running."); }
  if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : "Check the video details and try again.");
  return payload;
}

$("source-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy) return;
  message("error-message", "");
  message("warning-message", "");
  let endpoint = "/api/analyze";
  let options;
  if (mode === "upload") {
    const file = $("video-file").files[0];
    if (!file) return message("error-message", "Choose a video to upload first.");
    if (file.size > 500 * 1024 * 1024) return message("error-message", "Choose a video smaller than 500 MiB.");
    const form = new FormData();
    form.append("file", file);
    endpoint = "/api/analyze-file";
    options = { method: "POST", body: form };
  } else {
    const value = $(mode === "reel" ? "reel-url" : "local-path").value.trim();
    if (!value) return message("error-message", mode === "reel" ? "Paste a public Instagram reel URL first." : "Enter a video path first.");
    const body = mode === "reel" ? { url: value } : { filePath: value };
    if (mode === "reel" && $("cookies-path").value.trim()) body.cookiesPath = $("cookies-path").value.trim();
    options = { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  }
  setBusy(true);
  $("clip-player").pause();
  clearTimeout(pollTimer);
  $("job-status").hidden = false;
  $("status-label").textContent = mode === "upload" ? "Uploading your video…" : "Starting your cut sheet…";
  $("status-percentage").textContent = "";
  $("job-progress").removeAttribute("value");
  try {
    const result = await api(endpoint, options);
    selectedId = null;
    drafts = {};
    store("splice-job", result.jobId);
    pollingFailures = 0;
    renderJob(result);
    if (!["complete", "failed"].includes(result.status)) schedulePoll(result.jobId);
  } catch (error) {
    setBusy(false);
    $("job-status").hidden = true;
    message("error-message", error.message);
  }
});

function schedulePoll(jobId) {
  pollTimer = setTimeout(async () => {
    try {
      const result = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
      pollingFailures = 0;
      message("error-message", "");
      renderJob(result);
      if (!["complete", "failed"].includes(result.status)) schedulePoll(jobId);
    } catch (error) {
      pollingFailures += 1;
      if (pollingFailures < 5) {
        message("error-message", "Connection interrupted. Trying to reconnect…");
        schedulePoll(jobId);
      } else {
        setBusy(false);
        message("error-message", `${error.message} Refresh to reconnect to this job.`);
      }
    }
  }, 1100);
}

function renderJob(result) {
  job = result;
  const finished = ["complete", "failed"].includes(job.status);
  setBusy(!finished);
  $("job-status").hidden = false;
  $("status-label").textContent = job.stage;
  $("status-percentage").textContent = job.status === "failed" ? "" : `${job.progress}%`;
  $("job-progress").value = job.progress;
  message("error-message", job.error);
  message("warning-message", job.warnings?.join(" "));
  $("workspace").hidden = !job.cuts.length;
  $("empty-state").hidden = Boolean(job.cuts.length);
  $("cut-count").textContent = job.cuts.length;
  $("total-duration").textContent = `${seconds(job.sourceDuration || 0)} in total`;
  renderCuts();
  if (job.cuts.length) {
    if (!job.cuts.some((cut) => cut.id === selectedId)) selectedId = job.cuts[0].id;
    selectCut(selectedId, false);
  }
}

function renderCuts() {
  $("cut-list").replaceChildren();
  for (const cut of job.cuts) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "cut-card";
    button.dataset.cutId = cut.id;
    button.setAttribute("aria-pressed", String(cut.id === selectedId));
    button.setAttribute("aria-label", `Play cut ${cut.index}, ${seconds(cut.duration)}`);
    const number = document.createElement("span");
    number.className = "cut-number";
    number.textContent = String(cut.index).padStart(2, "0");
    const description = document.createElement("span");
    description.className = "cut-description";
    const title = document.createElement("strong");
    title.textContent = `Cut ${String(cut.index).padStart(2, "0")}`;
    const preview = document.createElement("small");
    preview.textContent = cut.transcript || `${timestamp(cut.start)} – ${timestamp(cut.end)}`;
    description.append(title, preview);
    const length = document.createElement("span");
    length.className = "cut-length";
    length.textContent = seconds(cut.duration);
    button.append(number, description, length);
    button.addEventListener("click", () => selectCut(cut.id, true));
    $("cut-list").append(button);
  }
}

function selectCut(id, play) {
  const index = job.cuts.findIndex((cut) => cut.id === id);
  if (index < 0) return;
  const cut = job.cuts[index];
  selectedId = id;
  document.querySelectorAll(".cut-card").forEach((card) => card.setAttribute("aria-pressed", String(card.dataset.cutId === id)));
  $("selected-title").textContent = `Cut ${String(cut.index).padStart(2, "0")}`;
  $("selected-duration").textContent = seconds(cut.duration);
  $("selected-range").textContent = `${timestamp(cut.start)} – ${timestamp(cut.end)}`;
  const player = $("clip-player");
  if (player.getAttribute("src") !== cut.videoUrl) {
    player.pause();
    player.src = cut.videoUrl;
    player.load();
  }
  if (play) player.play().catch(() => {});
  $("download-clip").href = cut.videoUrl;
  $("download-clip").download = `splice-${String(cut.index).padStart(2, "0")}.mp4`;
  const value = drafts[id] ?? cut.transcript;
  if ($("transcript").value !== value) $("transcript").value = value;
  $("previous-cut").disabled = index === 0;
  $("next-cut").disabled = index === job.cuts.length - 1;
  updateDraftMeta();
}

function updateDraftMeta() {
  const words = $("transcript").value.trim().split(/\s+/).filter(Boolean).length;
  $("word-count").textContent = `${words} ${words === 1 ? "word" : "words"}`;
  $("draft-state").textContent = Object.hasOwn(drafts, selectedId) ? "Your draft · this tab" : "Original transcript";
}
$("transcript").addEventListener("input", () => {
  if (!job || !selectedId) return;
  drafts[selectedId] = $("transcript").value;
  store(`splice-drafts-${job.jobId}`, drafts);
  updateDraftMeta();
});
$("reset-transcript").addEventListener("click", () => {
  delete drafts[selectedId];
  store(`splice-drafts-${job.jobId}`, drafts);
  selectCut(selectedId, false);
});
function adjacentCut(offset) {
  const index = job.cuts.findIndex((cut) => cut.id === selectedId) + offset;
  if (job.cuts[index]) selectCut(job.cuts[index].id, true);
}
$("previous-cut").addEventListener("click", () => adjacentCut(-1));
$("next-cut").addEventListener("click", () => adjacentCut(1));
$("clip-player").addEventListener("error", () => message("error-message", "This clip could not be played. Try downloading it, or analyse the video again if the server has restarted."));

(async function restoreJob() {
  const jobId = retrieve("splice-job", null);
  if (typeof jobId !== "string") return;
  try {
    const result = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
    if (job || busy) return;
    drafts = retrieve(`splice-drafts-${jobId}`, {});
    renderJob(result);
    if (!["complete", "failed"].includes(result.status)) schedulePoll(jobId);
  } catch { /* A restarted server has no previous jobs to restore. */ }
})();
