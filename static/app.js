"use strict";

const $ = (id) => document.getElementById(id);
const BASE = document.body.dataset.basePath || "";
const apiPath = (path) => BASE + path;
const STAGES = ["Uploading", "Finding cuts", "Transcribing", "Ready"];
const VIDEO_SUFFIXES = new Set(["mp4", "mov", "webm"]);
const MAX_BYTES = 200 * 1024 * 1024;
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
let job = null;
let selectedId = null;
let selectedFile = null;
let drafts = {};
let operation = 0;
let uploadRequest = null;
let pollController = null;
let pollTimer = null;
let slowTimer = null;
let pollingFailures = 0;
let currentStage = -1;
let stageStarted = 0;
let failed = false;
let retryAction = "upload";
let thumbnailObserver = null;
let thumbnailQueue = [];
let thumbnailCancel = null;

let saveTimer = null;
let pendingSave = null;
let saves = Promise.resolve(true);

async function api(path, options = {}) {
  const response = await fetch(apiPath(path), {cache: "no-store", ...options,
    headers: {"X-Requested-With": "Splice", "Content-Type": "application/json", ...(options.headers || {})}});
  if (response.status === 401) { window.location.assign(apiPath("/login")); throw new Error("Please sign in again."); }
  const result = await response.json();
  if (!response.ok) throw new Error(responseError(result));
  return result;
}
function queueSave() {
  if (!job || selectedId === "original") return;
  clearTimeout(saveTimer);
  if (pendingSave && (pendingSave.id !== selectedId || pendingSave.jobId !== job.jobId)) flushSave();
  pendingSave = {jobId: job.jobId, id: selectedId, transcript: $("transcript").value, operation};
  $("draft-note").textContent = "Saving…";
  saveTimer = setTimeout(flushSave, 500);
}
function flushSave() {
  clearTimeout(saveTimer);
  const item = pendingSave;
  if (!item) return saves;
  pendingSave = null;
  saves = saves.then(async () => {
    try {
      await api("/api/jobs/" + encodeURIComponent(item.jobId) + "/transcript/" + encodeURIComponent(item.id),
        {method: "PUT", body: JSON.stringify({transcript: item.transcript})});
      if (item.operation === operation) $("draft-note").textContent = "Saved privately to this video.";
      return true;
    } catch (error) {
      if (item.operation === operation) $("draft-note").textContent = "Not saved. " + error.message + " Copy your words before leaving.";
      if (!pendingSave) pendingSave = item;
      return false;
    }
  });
  return saves;
}
async function loadRecent() {
  try {
    const result = await api("/api/jobs");
    $("recent-jobs").replaceChildren();
    for (const item of result.jobs) {
      const button = document.createElement("button");
      button.type = "button"; button.className = "recent-job";
      const title = document.createElement("strong"); title.textContent = item.originalFilename || "Uploaded video";
      const meta = document.createElement("span"); meta.textContent = new Date(item.createdAt * 1000).toLocaleDateString("en-GB") + " · " + (item.status === "complete" ? item.cuts.length + " cuts" : item.status);
      button.append(title, meta);
      button.addEventListener("click", () => openJob(item.jobId));
      $("recent-jobs").append(button);
    }
    message("recent-error", result.jobs.length ? "" : "Your videos will appear here.");
  } catch (error) { message("recent-error", error.message); }
}
async function openJob(id) {
  if (!await flushSave()) return;
  stopWork();
  const token = operation;
  try {
    const result = await fetchJob(id);
    if (token !== operation) return;
    drafts = {}; selectedId = null; currentStage = -1;
    $("source-name").textContent = result.originalFilename || "Uploaded video";
    $("source-size").textContent = "";
    renderJob(result);
    if (!["complete", "failed"].includes(result.status)) schedulePoll(id, token);
  } catch (error) { message("recent-error", error.message); }
}
function newVideo() {
  $("new-video-title").textContent = "Start a new video?";
  $("new-video-copy").textContent = "The current video will no longer stay visible in this workspace. You can reopen it from Recent videos while it is within your latest 10.";
  $("new-video-confirm").textContent = "Continue";
  $("new-video-dialog").showModal();
}

function message(id, value) {
  $(id).textContent = value || "";
  $(id).hidden = !value;
}
function seconds(value) { return Number(value).toFixed(2) + "s"; }
function fileSize(value) {
  return (value / (1024 * 1024)).toFixed(1) + " MiB";
}
function timestamp(value) {
  const hundredths = Math.round(Number(value) * 100);
  const minutes = Math.floor(hundredths / 6000);
  return String(minutes).padStart(2, "0") + ":" + ((hundredths % 6000) / 100).toFixed(2).padStart(5, "0");
}
function setScreen(name) {
  const changed = document.body.dataset.screen !== name;
  document.body.dataset.screen = name;
  if (changed) {
    window.scrollTo({ top: 0, behavior: "auto" });
    const heading = $(name === "empty" ? "dropzone" : name === "loading" ? "loading-title" : "result-title");
    heading.focus({ preventScroll: true });
  }
}
function clearThumbnails() {
  thumbnailObserver?.disconnect();
  thumbnailObserver = null;
  thumbnailQueue = [];
  thumbnailCancel?.();
}
function stopWork() {
  flushSave();
  operation += 1;
  clearTimeout(pollTimer);
  clearTimeout(slowTimer);
  pollController?.abort();
  pollController = null;
  uploadRequest?.abort();
  uploadRequest = null;
  clearThumbnails();
  const player = $("clip-player");
  player.pause();
  player.removeAttribute("src");
  player.load();
}
function resetWorkspace() {
  stopWork();
  job = null;
  selectedFile = null;
  selectedId = null;
  drafts = {};
  currentStage = -1;
  failed = false;
  dragDepth = 0;
  $("video-file").value = "";
  $("dropzone").classList.remove("is-invalid", "is-dragging");
  $("dropzone").removeAttribute("aria-invalid");
  message("upload-error", "");

  setScreen("empty");
  loadRecent();
}
function chooseVideo() {
  $("video-file").value = "";
  $("video-file").click();
}
function invalidFile(text) {
  resetWorkspace();
  $("dropzone").classList.add("is-invalid");
  $("dropzone").setAttribute("aria-invalid", "true");
  message("upload-error", text);
}
function acceptFiles(files) {
  if (files.length !== 1) return invalidFile("Choose one video at a time.");
  if (document.body.dataset.screen === "result") return newVideo();
  const file = files[0];
  const suffix = file.name.split(".").pop().toLowerCase();
  if (!VIDEO_SUFFIXES.has(suffix)) return invalidFile("Use an MP4, MOV or WebM video.");
  if (!file.size) return invalidFile("This file is empty. Choose a video with something in it.");
  if (file.size > MAX_BYTES) return invalidFile("Choose a video up to 200 MiB and no longer than 5 minutes.");
  startUpload(file);
}

function renderStages() {
  document.querySelectorAll("#loading-stages li").forEach((item, index) => {
    const done = index < currentStage || currentStage === 3;
    const active = index === currentStage;
    item.classList.toggle("is-done", done);
    item.classList.toggle("is-current", active && !failed && !done);
    item.classList.toggle("is-error", active && failed);
    if (active) item.setAttribute("aria-current", "step");
    else item.removeAttribute("aria-current");
    item.querySelector(".stage-marker").textContent = done ? "✓" : String(index + 1);
    item.querySelector(".stage-state").textContent = done ? "Done" : active ? (failed ? "Stopped" : "In progress") : "Waiting";
  });
}
function setProgress(fraction = null, detail = "") {
  const progress = $("stage-progress");
  progress.hidden = failed || currentStage === 3;
  progress.classList.toggle("is-indeterminate", fraction === null);
  progress.setAttribute("aria-label", STAGES[currentStage]);
  progress.setAttribute("aria-valuetext", detail || STAGES[currentStage] + " in progress");
  if (fraction === null) {
    progress.removeAttribute("aria-valuenow");
    progress.removeAttribute("aria-valuemin");
    progress.removeAttribute("aria-valuemax");
    $("progress-fill").style.width = "";
  } else {
    progress.setAttribute("aria-valuemin", "0");
    progress.setAttribute("aria-valuemax", "100");
    progress.setAttribute("aria-valuenow", String(Math.round(fraction * 100)));
    $("progress-fill").style.width = (fraction * 100) + "%";
  }
  message("upload-bytes", currentStage === 0 ? detail : "");
}
function setStage(index) {
  if (index === currentStage && !failed) return;
  currentStage = index;
  stageStarted = Date.now();
  failed = false;
  clearTimeout(slowTimer);
  message("loading-note", "");
  renderStages();
  setProgress();
  $("loading-live").textContent = STAGES[index] + (index === 3 ? ". Your cut sheet is ready." : " in progress.");
  if (index === 2) {
    const token = operation;
    slowTimer = setTimeout(() => {
      if (token === operation && currentStage === 2 && !failed) {
        message("loading-note", "Still writing transcript… Longer reels can take a little more time.");
      }
    }, 45000);
  }
}
function jobStage(result) {
  if (result.status === "complete") return 3;
  const stage = String(result.stage || "").toLowerCase();
  if (/transcrib|transcript|whisper|finishing/.test(stage)) return 2;
  if (/reading|finding|ffmpeg|creating|waiting|queued/.test(stage)) return 1;
  if (/receiving|uploading/.test(stage)) return 0;
  // Older workers may only report status/progress. This chooses a stage, never a percentage.
  return Number(result.progress) >= 80 ? 2 : 1;
}
function showFailure(text, action = "upload") {
  failed = true;
  retryAction = action;
  clearTimeout(pollTimer);
  clearTimeout(slowTimer);
  $("loading-title").textContent = action === "poll" ? "Connection interrupted" : currentStage === 0 ? "Upload interrupted" : "Analysis stopped";
  $("stage-progress").hidden = true;
  message("upload-bytes", "");
  message("loading-note", "");
  message("processing-error", text);
  $("loading-error").hidden = false;
  renderStages();
  setScreen("loading");
}
function responseError(payload) {
  return typeof payload?.detail === "string" ? payload.detail : "The video could not be accepted. Try again or choose another video.";
}
function startUpload(file) {
  stopWork();
  const token = operation;
  selectedFile = file;
  job = null;
  selectedId = null;
  drafts = {};
  currentStage = -1;
  failed = false;
  pollingFailures = 0;

  $("dropzone").classList.remove("is-invalid", "is-dragging");
  $("dropzone").removeAttribute("aria-invalid");
  message("upload-error", "");
  $("loading-title").textContent = "Analysing your reel…";
  $("loading-error").hidden = true;
  $("source-name").textContent = file.name;
  $("source-size").textContent = fileSize(file.size);
  setStage(0);
  setScreen("loading");

  const form = new FormData();
  form.append("file", file);
  const request = new XMLHttpRequest();
  uploadRequest = request;
  request.open("POST", apiPath("/api/analyze-file"));
  request.setRequestHeader("X-Requested-With", "Splice");
  request.timeout = 300000;
  request.upload.addEventListener("progress", (event) => {
    if (token !== operation || currentStage !== 0) return;
    if (event.lengthComputable && event.total) {
      const detail = fileSize(event.loaded) + " of " + fileSize(event.total) + " sent";
      setProgress(Math.min(event.loaded / event.total, 1), detail);
    } else setProgress();
  });
  request.upload.addEventListener("load", () => {
    if (token === operation) setStage(1);
  });
  request.addEventListener("load", () => {
    if (token !== operation) return;
    uploadRequest = null;
    let result;
    try { result = JSON.parse(request.responseText); } catch {
      return showFailure("The server returned an unexpected response. Try again.");
    }
    if (request.status === 401) return window.location.assign(apiPath("/login"));
    if (request.status < 200 || request.status >= 300) return showFailure(responseError(result));
    if (typeof result?.jobId !== "string") return showFailure("The upload was received without a job reference. Try again.");
    renderJob(result);
    if (!["complete", "failed"].includes(result.status)) schedulePoll(result.jobId, token);
  });
  request.addEventListener("error", () => {
    if (token === operation) showFailure("The upload was interrupted. Check your connection and retry.");
  });
  request.addEventListener("timeout", () => {
    if (token === operation) showFailure("The upload timed out. Check your connection and retry.");
  });
  request.send(form);
}

async function fetchJob(id) {
  const controller = new AbortController();
  pollController = controller;
  const timer = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(apiPath("/api/jobs/" + encodeURIComponent(id)), { signal: controller.signal, cache: "no-store" });
    let result;
    try { result = await response.json(); } catch { throw new Error("The server returned an unexpected response."); }
    if (response.status === 401) window.location.assign(apiPath("/login"));
    if (!response.ok) throw new Error(responseError(result));
    return result;
  } finally {
    clearTimeout(timer);
    if (pollController === controller) pollController = null;
  }
}
function schedulePoll(id, token = operation, delay = 1100) {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(async () => {
    try {
      const result = await fetchJob(id);
      if (token !== operation) return;
      pollingFailures = 0;
      renderJob(result);
      if (!["complete", "failed"].includes(result.status)) schedulePoll(id, token);
    } catch (error) {
      if (token !== operation) return;
      pollingFailures += 1;
      if (pollingFailures < 5) {
        message("loading-note", "Connection interrupted. Trying to reconnect…");
        schedulePoll(id, token);
      } else {
        const detail = error.name === "AbortError" ? "The server took too long to respond." : error.message;
        showFailure(detail + " Retry to reconnect to this reel.", "poll");
      }
    }
  }, delay);
}
function renderJob(result) {
  job = result;
  if (result.status === "failed") {
    currentStage = Math.max(currentStage, jobStage(result));
    return showFailure(result.error || "This video could not be processed. Retry or choose another video.");
  }
  setStage(Math.max(currentStage, jobStage(result)));
  if (result.status !== "complete") {
    message("loading-note", currentStage === 2 && Date.now() - stageStarted >= 45000 ? "Still writing transcript… Longer reels can take a little more time." : "");
    setScreen("loading");
    return;
  }
  clearTimeout(slowTimer);
  if (!Array.isArray(result.cuts) || !result.cuts.length) {
    currentStage = 2;
    return showFailure("No cuts could be prepared. Retry or choose another video.");
  }
  renderResult();
}

function renderResult() {
  clearThumbnails();
  $("cut-count").textContent = String(job.cuts.length);
  const total = job.sourceDuration ?? job.cuts.reduce((sum, cut) => sum + Number(cut.duration), 0);
  $("total-duration").textContent = "Total " + seconds(total);
  const warnings = (job.warnings || []).map((warning) => {
    if (/no audio/i.test(warning)) return "This reel has no audio track. Type the words you'll record.";
    if (/whisper|transcri/i.test(warning)) return "Transcription is unavailable for this reel. You can still type your own words.";
    return warning;
  });
  message("result-warning", warnings.join(" "));
  selectedId = selectedId && job.cuts.some((cut) => cut.id === selectedId) ? selectedId : "original";
  $("cut-strip").replaceChildren();
  const original = document.createElement("button");
  original.type = "button"; original.className = "cut-card"; original.dataset.cutId = "original";
  original.textContent = "Original · " + seconds(total);
  original.addEventListener("click", () => selectCut("original", true));
  $("cut-strip").append(original);
  const thumbnails = [];
  for (const [index, cut] of job.cuts.entries()) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "cut-card";
    button.dataset.cutId = cut.id;
    button.setAttribute("aria-pressed", String(cut.id === selectedId));
    button.setAttribute("aria-label", "Cut " + (index + 1) + ", " + Number(cut.duration).toFixed(2) + " seconds. Play cut.");
    button.tabIndex = cut.id === selectedId ? 0 : -1;
    const poster = document.createElement("span");
    poster.className = "cut-thumbnail";
    poster.setAttribute("aria-hidden", "true");
    const placeholder = document.createElement("span");
    placeholder.className = "thumbnail-placeholder";
    placeholder.textContent = String(index + 1).padStart(2, "0");
    const img = document.createElement("img");
    img.alt = "";
    img.hidden = true;
    img.decoding = "async";
    const duration = document.createElement("span");
    duration.className = "cut-duration";
    duration.textContent = seconds(cut.duration);
    const indicator = document.createElement("span");
    indicator.className = "selected-play";
    const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    icon.setAttribute("width", "12");
    icon.setAttribute("height", "12");
    icon.setAttribute("viewBox", "0 0 12 12");
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", "M3 1.5v9L10 6z");
    path.setAttribute("fill", "currentColor");
    icon.append(path);
    indicator.append(icon);
    poster.append(placeholder, img, duration, indicator);
    const label = document.createElement("span");
    label.className = "cut-label";
    label.textContent = "Cut " + (index + 1);
    button.append(poster, label);
    button.addEventListener("click", () => selectCut(cut.id, true));
    button.addEventListener("keydown", (event) => {
      let target = index;
      if (event.key === "ArrowLeft") target = Math.max(0, index - 1);
      else if (event.key === "ArrowRight") target = Math.min(job.cuts.length - 1, index + 1);
      else if (event.key === "Home") target = 0;
      else if (event.key === "End") target = job.cuts.length - 1;
      else return;
      event.preventDefault();
      selectCut(job.cuts[target].id, false);
      $("cut-strip").children[target + 1].focus({ preventScroll: true });
    });
    $("cut-strip").append(button);
    const thumb = cut.thumb || cut.thumbnailUrl;
    if (thumb) {
      img.addEventListener("load", () => { img.hidden = false; });
      img.src = thumb;
    } else thumbnails.push({ cut, img, button });
  }
  const add = document.createElement("button");
  add.type = "button"; add.className = "cut-card new-video-card"; add.id = "new-video";
  const plus = document.createElement("span"); plus.textContent = "+"; plus.setAttribute("aria-hidden", "true");
  const label = document.createElement("strong"); label.textContent = "New Video";
  add.append(plus, label); add.addEventListener("click", newVideo);
  $("cut-strip").append(add);
  setScreen("result");
  selectCut(selectedId, false);
  observeThumbnails(thumbnails);
}
function selectCut(id, play) {
  const index = job?.cuts.findIndex((cut) => cut.id === id) ?? -1;
  if (!job || (index < 0 && id !== "original")) return;
  flushSave();
  const original = id === "original";
  const cut = original ? {videoUrl: job.originalUrl, duration: job.sourceDuration, start: 0, end: job.sourceDuration,
    transcript: job.cuts.map((item) => item.transcript).filter(Boolean).join(" ")} : job.cuts[index];
  selectedId = id;

  for (const card of $("cut-strip").children) {
    const selected = card.dataset.cutId === id;
    card.setAttribute("aria-pressed", String(selected));
    card.tabIndex = 0;
    if (selected) card.scrollIntoView({ block: "nearest", inline: "nearest", behavior: reducedMotion.matches ? "auto" : "smooth" });
  }
  $("selected-title").textContent = original ? "Original video" : "Cut " + (index + 1) + " of " + job.cuts.length;
  $("selected-duration").textContent = Number(cut.duration).toFixed(2) + " seconds";
  $("selected-range").textContent = timestamp(cut.start) + " – " + timestamp(cut.end);
  const player = $("clip-player");
  if (player.getAttribute("src") !== cut.videoUrl) {
    player.pause();
    message("player-error", "");
    player.src = cut.videoUrl;
    player.load();
  }
  player.loop = false;
  player.setAttribute("aria-label", original ? "Original video" : "Cut " + (index + 1) + " of " + job.cuts.length);
  if (play) {
    if (player.ended) player.currentTime = 0;
    player.play().catch(() => { /* Native controls remain available when autoplay is blocked. */ });
  }
  $("download-clip").href = cut.videoUrl;
  $("transcript").value = drafts[id] ?? cut.transcript ?? "";
  $("transcript").disabled = original;
  $("download-clip").textContent = original ? "Download original ↓" : "Download cut ↓";
  $("download-clip").download = original ? "splice-original" + (job.sourceSuffix || ".mp4") : "splice-cut-" + (index + 1) + ".mp4";
  $("draft-note").textContent = original ? "Select a cut to edit its transcript." : "Edits are saved privately to this video.";
  $("previous-cut").disabled = index <= 0;
  $("next-cut").disabled = index === job.cuts.length - 1;
  updateDraftMeta();
}
function updateDraftMeta() {
  const words = $("transcript").value.trim().split(/\s+/).filter(Boolean).length;
  const edited = Object.hasOwn(drafts, selectedId);
  $("word-count").textContent = words + (words === 1 ? " word" : " words");
  $("draft-state").textContent = edited ? "Your words" : "Original transcript";
  $("reset-transcript").disabled = selectedId === "original" || !edited;
}
function adjacentCut(offset) {
  const index = job.cuts.findIndex((cut) => cut.id === selectedId) + offset;
  if (job.cuts[index]) selectCut(job.cuts[index].id, true);
}

// Decode only nearby thumbnails, one at a time. The visible editor has one player.
function observeThumbnails(items) {
  if (!("IntersectionObserver" in window)) {
    thumbnailQueue.push(...items.slice(0, 8));
    makeNextThumbnail();
    return;
  }
  const pending = new Map(items.map((item) => [item.button, item]));
  thumbnailObserver = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      const item = pending.get(entry.target);
      if (item) thumbnailQueue.push(item);
      pending.delete(entry.target);
      thumbnailObserver.unobserve(entry.target);
    }
    makeNextThumbnail();
  }, { root: $("cut-strip"), rootMargin: "0px 160px" });
  items.forEach((item) => thumbnailObserver.observe(item.button));
}
function makeNextThumbnail() {
  if (thumbnailCancel || !thumbnailQueue.length) return;
  const { cut, img } = thumbnailQueue.shift();
  const token = operation;
  const preview = document.createElement("video");
  preview.muted = true;
  preview.playsInline = true;
  preview.preload = "auto";
  let finished = false;
  let timeout;
  function finish(draw) {
    if (finished) return;
    finished = true;
    clearTimeout(timeout);
    if (draw && token === operation && preview.videoWidth && preview.videoHeight) {
      try {
        const canvas = document.createElement("canvas");
        const scale = Math.min(160 / preview.videoWidth, 320 / preview.videoHeight);
        canvas.width = Math.max(1, Math.round(scale * preview.videoWidth));
        canvas.height = Math.max(1, Math.round(scale * preview.videoHeight));
        canvas.getContext("2d").drawImage(preview, 0, 0, canvas.width, canvas.height);
        img.src = canvas.toDataURL("image/jpeg", 0.75);
        img.hidden = false;
      } catch { /* A solid numbered poster remains usable without a decoded frame. */ }
    }
    preview.pause();
    preview.removeAttribute("src");
    preview.load();
    thumbnailCancel = null;
    if (token === operation) makeNextThumbnail();
  }
  thumbnailCancel = () => finish(false);
  preview.addEventListener("loadeddata", () => finish(true), { once: true });
  preview.addEventListener("error", () => finish(false), { once: true });
  timeout = setTimeout(() => finish(false), 6000);
  preview.src = cut.videoUrl;
  preview.load();
}

$("choose-video").addEventListener("click", (event) => { event.stopPropagation(); chooseVideo(); });
$("video-file").addEventListener("change", (event) => {
  if (event.target.files.length) acceptFiles(event.target.files);
});
$("dropzone").addEventListener("click", chooseVideo);
$("dropzone").addEventListener("keydown", (event) => {
  if (event.target === $("dropzone") && ["Enter", " "].includes(event.key)) {
    event.preventDefault();
    chooseVideo();
  }
});
let dragDepth = 0;
$("dropzone").addEventListener("dragenter", (event) => {
  event.preventDefault();
  dragDepth += 1;
  $("dropzone").classList.add("is-dragging");
});
$("dropzone").addEventListener("dragover", (event) => {
  event.preventDefault();
  event.dataTransfer.dropEffect = "copy";
});
$("dropzone").addEventListener("dragleave", () => {
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) $("dropzone").classList.remove("is-dragging");
});
$("dropzone").addEventListener("drop", (event) => {
  event.preventDefault();
  dragDepth = 0;
  $("dropzone").classList.remove("is-dragging");
  acceptFiles(event.dataTransfer.files);
});
document.addEventListener("dragover", (event) => {
  if (Array.from(event.dataTransfer.types).includes("Files")) event.preventDefault();
});
document.addEventListener("drop", (event) => {
  if (Array.from(event.dataTransfer.types).includes("Files")) event.preventDefault();
});
$("home").addEventListener("click", async () => { if (await flushSave()) resetWorkspace(); });
$("logout").addEventListener("click", async () => {
  if (!await flushSave()) return;
  try { await api("/api/logout", {method: "POST"}); window.location.assign(apiPath("/login")); }
  catch (error) { message("result-warning", error.message); }
});
$("new-video-cancel").addEventListener("click", () => $("new-video-dialog").close());
$("new-video-confirm").addEventListener("click", async () => {
  if (!await flushSave()) { $("new-video-dialog").close(); return; }
  $("new-video-dialog").close(); resetWorkspace();
});
$("cancel-upload").addEventListener("click", resetWorkspace);
$("choose-another").addEventListener("click", chooseVideo);
$("retry-upload").addEventListener("click", () => {
  if (retryAction === "poll" && job) {
    failed = false;
    pollingFailures = 0;
    $("loading-error").hidden = true;
    $("loading-title").textContent = "Analysing your reel…";
    $("loading-live").textContent = "Reconnecting to your reel.";
    renderStages();
    setProgress();
    schedulePoll(job.jobId, operation, 0);
  } else if (selectedFile) startUpload(selectedFile);
  else chooseVideo();
});
$("transcript").addEventListener("input", () => {
  if (!job || !selectedId) return;
  drafts[selectedId] = $("transcript").value;
  const cut = job.cuts.find((item) => item.id === selectedId);
  if (cut) cut.transcript = $("transcript").value;
  queueSave();
  updateDraftMeta();
});
$("reset-transcript").addEventListener("click", () => {
  const cut = job.cuts.find((item) => item.id === selectedId);
  if (!cut) return;
  cut.transcript = cut.originalTranscript || "";
  drafts[selectedId] = cut.transcript;
  selectCut(selectedId, false);
  queueSave();
});
$("previous-cut").addEventListener("click", () => adjacentCut(-1));
$("next-cut").addEventListener("click", () => adjacentCut(1));
$("replay-cut").addEventListener("click", () => {
  const player = $("clip-player");
  player.currentTime = 0;
  player.play().catch(() => {});
});
$("clip-player").addEventListener("error", () => {
  if (document.body.dataset.screen === "result" && $("clip-player").getAttribute("src")) {
    const name = selectedId === "original" ? "original video" : "cut";
    message("player-error", "This " + name + " could not be played in this browser. Download it to watch it, or choose another video.");
  }
});

(async function initialise() {
  try {
    const session = await api("/api/session");
    $("account-email").textContent = session.email;
    $("admin-link").hidden = !session.admin;
    await loadRecent();
  } catch (error) { message("recent-error", error.message); }
})();
