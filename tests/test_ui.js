"use strict";

// Dependency-free DOM/API harness for screen transitions and upload/poll races.
// This exercises the shipped app.js; it does not substitute for browser layout checks.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const html = fs.readFileSync("static/index.html", "utf8");
const source = fs.readFileSync("static/app.js", "utf8");

class Element {
  constructor(tag = "div") {
    this.tagName = tag.toUpperCase();
    this.attributes = new Map();
    this.dataset = {};
    this.listeners = new Map();
    this.children = [];
    this.className = "";
    this.style = {};
    this.value = "";
    this.hidden = false;
    this.disabled = false;
    this.textContent = "";
    this.playCount = 0;
    this.clickCount = 0;
    this.classList = {
      contains: (name) => this.className.split(" ").includes(name),
      add: (...names) => { names.forEach((name) => this.classList.toggle(name, true)); },
      remove: (...names) => { names.forEach((name) => this.classList.toggle(name, false)); },
      toggle: (name, value) => {
        const names = new Set(this.className.split(" ").filter(Boolean));
        if (value) names.add(name); else names.delete(name);
        this.className = Array.from(names).join(" ");
      },
    };
  }
  addEventListener(type, callback) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(callback);
  }
  emit(type, details = {}) {
    const event = { target: this, preventDefault() {}, stopPropagation() {}, ...details };
    for (const callback of this.listeners.get(type) || []) callback(event);
  }
  click() { this.clickCount += 1; this.emit("click"); }
  focus() { this.focused = true; }
  scrollIntoView() { this.scrolled = true; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  removeAttribute(name) { this.attributes.delete(name); }
  set src(value) { this.setAttribute("src", value); }
  get src() { return this.getAttribute("src"); }
  querySelector(selector) { return this.children.find((child) => child.classList.contains(selector.slice(1))); }
  pause() {}
  load() {}
  play() { this.playCount += 1; return Promise.resolve(); }
}

function harness(saved = {}, initialResponses = []) {
  const elements = new Map();
  for (const match of html.matchAll(/<(\w+)\b([^>]*\bid="([^"]+)"[^>]*)>/g)) {
    const element = new Element(match[1]);
    element.hidden = /\bhidden\b/.test(match[2]);
    element.disabled = /\bdisabled\b/.test(match[2]);
    elements.set(match[3], element);
  }
  const stages = Array.from({ length: 4 }, () => {
    const row = new Element("li");
    for (const name of ["stage-marker", "stage-state"]) {
      const child = new Element("span");
      child.className = name;
      row.append(child);
    }
    return row;
  });
  const body = new Element("body");
  body.dataset.screen = "empty";
  const document = {
    body,
    getElementById: (id) => elements.get(id),
    querySelectorAll: () => stages,
    createElement: (tag) => new Element(tag),
    createElementNS: (_, tag) => new Element(tag),
    addEventListener() {},
  };
  const requests = [];
  class Request extends Element {
    constructor() {
      super();
      this.upload = new Element();
      requests.push(this);
    }
    open(method, url) { this.method = method; this.url = url; }
    send(form) { this.form = form; }
    abort() { this.aborted = true; }
    respond(result, status = 202) {
      this.status = status;
      this.responseText = JSON.stringify(result);
      this.emit("load");
    }
  }
  const storage = new Map(Object.entries(saved).map(([key, value]) => [key, JSON.stringify(value)]));
  const timers = new Map();
  let timerId = 0;
  const responses = [...initialResponses];
  const fetches = [];
  const context = vm.createContext({
    document,
    window: { matchMedia: () => ({ matches: true }), scrollTo() {} },
    sessionStorage: {
      getItem: (key) => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, value),
    },
    XMLHttpRequest: Request,
    FormData: class {
      constructor() { this.fields = new Map(); }
      append(key, value) { this.fields.set(key, value); }
    },
    AbortController,
    fetch: (url) => {
      fetches.push(url);
      const result = responses.shift();
      if (result instanceof Error) return Promise.reject(result);
      if (result?.then) return result;
      return Promise.resolve({ ok: true, json: async () => result });
    },
    setTimeout: (callback, delay) => {
      timers.set(++timerId, { callback, delay });
      return timerId;
    },
    clearTimeout: (id) => timers.delete(id),
  });
  vm.runInContext(source, context);
  return {
    $: (id) => elements.get(id), body, context, requests, responses, fetches, storage, stages,
    upload(file = { name: "reel.mp4", size: 8 * 1024 * 1024 }) {
      elements.get("video-file").files = [file];
      elements.get("video-file").emit("change");
      return requests.at(-1);
    },
    async tick(delay) {
      const entry = Array.from(timers).find(([, timer]) => timer.delay === delay);
      assert.ok(entry, "Expected timer with delay " + delay);
      timers.delete(entry[0]);
      await entry[1].callback();
    },
  };
}

function job(status = "processing", stage = "Finding hard cuts with ffmpeg…") {
  return {
    jobId: "sample-job", status, stage, progress: 80, sourceDuration: 3.75,
    cuts: Array.from({ length: 3 }, (_, index) => ({
      id: "cut-" + index, index: index + 1,
      start: index * 1.25, end: (index + 1) * 1.25, duration: 1.25,
      videoUrl: "/media/sample-job/clip-00" + (index + 1) + ".mp4",
      transcript: index ? "" : "Original words.",
    })),
    warnings: [],
  };
}

test("every design token matches the brief without extra tokens", () => {
  const brief = fs.readFileSync("docs/splice-ui-brief.md", "utf8").split("## 4. Design tokens")[1].split("## 5.")[0];
  const css = fs.readFileSync("static/styles.css", "utf8");
  const tokens = (text) => Object.fromEntries(Array.from(text.matchAll(/(--[\w-]+):\s*([^;]+);/g), ([, key, value]) => [key, value.replace(/\s+/g, "")]));
  assert.deepEqual(tokens(css), tokens(brief));
  assert.match(html, /name="color-scheme" content="dark"/);
  assert.match(html, /IBM\+Plex\+Sans/);
  assert.match(html, /IBM\+Plex\+Mono/);
});

test("worker labels collapse into the four named stages", () => {
  const ui = harness();
  for (const [stage, expected] of [
    ["Receiving your video…", 0], ["Reading video…", 1],
    ["Waiting for the local worker…", 1], ["Finding hard cuts with ffmpeg…", 1],
    ["Creating clip 2 of 7…", 1], ["Transcribing locally with Whisper…", 2],
    ["Writing transcript…", 2], ["Finishing cut sheet…", 2],
  ]) {
    assert.equal(vm.runInContext("jobStage(" + JSON.stringify({ stage }) + ")", ui.context), expected, stage);
  }
  assert.equal(vm.runInContext('jobStage({status: "complete"})', ui.context), 3);
  assert.equal(vm.runInContext('jobStage({status: "processing", progress: 80})', ui.context), 2);
});

test("upload progress, partial job, polling, selection and transcript drafts", async () => {
  const ui = harness();
  ui.$("choose-video").click();
  assert.equal(ui.$("video-file").clickCount, 1);
  const request = ui.upload();
  assert.equal(request.method, "POST");
  assert.equal(request.url, "/api/analyze-file");
  assert.deepEqual(Array.from(request.form.fields.keys()), ["file"]);
  assert.equal(ui.body.dataset.screen, "loading");
  assert.equal(ui.$("source-name").textContent, "reel.mp4");
  request.upload.emit("progress", { lengthComputable: true, loaded: 4, total: 8 });
  assert.equal(ui.$("stage-progress").getAttribute("aria-valuenow"), "50");
  request.upload.emit("load");
  assert.equal(ui.$("stage-progress").getAttribute("aria-valuenow"), null);
  request.respond(job("processing", "Transcribing locally with Whisper…"));
  assert.equal(ui.body.dataset.screen, "loading");
  assert.equal(ui.$("cut-strip").children.length, 0, "Partial cuts must stay hidden");
  await ui.tick(45000);
  assert.match(ui.$("loading-note").textContent, /Still writing transcript/);
  ui.responses.push(job("complete", "Your cut sheet is ready"));
  await ui.tick(1100);
  assert.equal(ui.fetches[0], "/api/jobs/sample-job");
  assert.equal(ui.body.dataset.screen, "result");
  assert.equal(ui.$("cut-strip").children.length, 3);
  assert.equal(ui.$("clip-player").src, "/media/sample-job/clip-001.mp4");
  assert.equal(ui.$("clip-player").loop, false);
  assert.equal(ui.$("total-duration").textContent, "Total 3.75s");
  ui.$("transcript").value = "My own words.";
  ui.$("transcript").emit("input");
  ui.$("cut-strip").children[0].emit("keydown", { key: "ArrowRight" });
  assert.equal(ui.$("selected-title").textContent, "Cut 2 of 3");
  assert.equal(ui.$("selected-duration").textContent, "1.25 seconds");
  assert.equal(ui.$("selected-range").textContent, "00:01.25 – 00:02.50");
  assert.equal(ui.$("clip-player").playCount, 0, "Arrow navigation should allow selection without autoplay");
  ui.$("cut-strip").children[0].click();
  assert.equal(ui.$("clip-player").playCount, 1);
  assert.equal(ui.$("transcript").value, "My own words.");
  assert.match(ui.storage.get("splice-drafts-sample-job"), /My own words/);
  ui.$("reset-transcript").click();
  assert.equal(ui.$("transcript").value, "Original words.");
  ui.$("new-upload").click();
  assert.equal(ui.body.dataset.screen, "empty");
  assert.equal(ui.$("clip-player").src, null);
  assert.equal(ui.storage.get("splice-job"), "null");
});

test("invalid files and size limits never send an upload", () => {
  const ui = harness();
  for (const file of [
    { name: "words.txt", size: 10 }, { name: "empty.mp4", size: 0 },
    { name: "large.mov", size: 500 * 1024 * 1024 + 1 },
  ]) {
    ui.upload(file);
    assert.equal(ui.requests.length, 0);
    assert.equal(ui.body.dataset.screen, "empty");
    assert.equal(ui.$("upload-error").hidden, false);
    assert.equal(ui.$("dropzone").getAttribute("aria-invalid"), "true");
  }
  ui.upload({ name: "phone.MOV", size: 500 * 1024 * 1024 });
  assert.equal(ui.requests.length, 1, "The stated maximum and uppercase extensions are accepted");
});

test("upload retry retains the file and old responses cannot replace a new upload", () => {
  const ui = harness();
  const first = ui.upload();
  first.emit("error");
  assert.equal(ui.$("loading-error").hidden, false);
  ui.$("retry-upload").click();
  assert.equal(ui.requests.length, 2);
  assert.equal(ui.requests[1].form.fields.get("file").name, "reel.mp4");
  first.respond(job("complete"));
  assert.equal(ui.body.dataset.screen, "loading");
  ui.$("cancel-upload").click();
  assert.equal(ui.requests[1].aborted, true);
  ui.requests[1].respond(job("complete"));
  assert.equal(ui.body.dataset.screen, "empty");
});

test("a failed job stays on the error screen even when it contains partial cuts", () => {
  const ui = harness();
  const result = job("failed", "Analysis stopped");
  result.error = "This file could not be read as a video.";
  ui.upload().respond(result);
  assert.equal(ui.body.dataset.screen, "loading");
  assert.equal(ui.$("loading-error").hidden, false);
  assert.equal(ui.$("processing-error").textContent, result.error);
  assert.equal(ui.$("cut-strip").children.length, 0);
  ui.$("choose-another").click();
  assert.equal(ui.$("video-file").clickCount, 1);
});

test("poll failures offer reconnection without uploading again", async () => {
  const ui = harness();
  ui.upload().respond(job());
  for (let index = 0; index < 5; index += 1) {
    ui.responses.push(new Error("Offline"));
    await ui.tick(1100);
  }
  assert.equal(ui.$("loading-error").hidden, false);
  assert.equal(ui.$("loading-title").textContent, "Connection interrupted");
  ui.$("retry-upload").click();
  ui.responses.push(job("complete"));
  await ui.tick(0);
  assert.equal(ui.requests.length, 1);
  assert.equal(ui.body.dataset.screen, "result");
});

test("restoring a tab keeps the selected cut, source metadata and edited words", async () => {
  const ui = harness();
  ui.upload().respond(job("complete"));
  ui.$("cut-strip").children[1].click();
  ui.$("transcript").value = "Another take.";
  ui.$("transcript").emit("input");
  const saved = Object.fromEntries(Array.from(ui.storage, ([key, value]) => [key, JSON.parse(value)]));
  const restored = harness(saved, [job("complete")]);
  await new Promise(setImmediate);
  assert.equal(restored.fetches[0], "/api/jobs/sample-job");
  assert.equal(restored.body.dataset.screen, "result");
  assert.equal(restored.$("selected-title").textContent, "Cut 2 of 3");
  assert.equal(restored.$("transcript").value, "Another take.");
  assert.equal(restored.$("source-name").textContent, "reel.mp4");
});

test("a delayed restore cannot replace a newly selected upload", async () => {
  let resolve;
  const pending = new Promise((complete) => { resolve = complete; });
  const ui = harness({ "splice-job": "sample-job" }, [pending]);
  ui.upload({ name: "new.mp4", size: 1024 });
  resolve({ ok: true, json: async () => job("complete") });
  await new Promise(setImmediate);
  assert.equal(ui.body.dataset.screen, "loading");
  assert.equal(ui.$("source-name").textContent, "new.mp4");
  assert.equal(ui.$("cut-strip").children.length, 0);
});
