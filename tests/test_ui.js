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
  showModal() { this.open = true; }
  close() { this.open = false; }
  pause() {}
  load() {}
  play() { this.playCount += 1; return Promise.resolve(); }
}

function harness(base = "", initialResponses = [], account = {}) {
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
  body.dataset.basePath = base;
  const created = [];
  const document = {
    body,
    getElementById: (id) => elements.get(id),
    querySelectorAll: () => stages,
    createElement: (tag) => {
      const element = new Element(tag);
      if (tag === "canvas") {
        element.getContext = () => ({drawImage() {}});
        element.toDataURL = () => "data:image/jpeg;base64,c3ludGhldGlj";
      }
      created.push(element);
      return element;
    },
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
    setRequestHeader(key, value) { this.headers ||= {}; this.headers[key] = value; }
    send(form) { this.form = form; }
    abort() { this.aborted = true; }
    respond(result, status = 202) {
      this.status = status;
      this.responseText = JSON.stringify(result);
      this.emit("load");
    }
  }
  const timers = new Map();
  let timerId = 0;
  const responses = [...initialResponses];
  const fetches = [];
  const connections = [];
  const context = vm.createContext({
    document,
    window: { matchMedia: () => ({ matches: true }), scrollTo() {}, location: {assign(url) { fetches.push("redirect:" + url); }} },
    XMLHttpRequest: Request,
    FormData: class {
      constructor() { this.fields = new Map(); }
      append(key, value) { this.fields.set(key, value); }
    },
    AbortController,
    EventSource: class {
      constructor(url) { this.url = url; connections.push(this); }
      close() { this.closed = true; }
    },
    fetch: (url, options = {}) => {
      fetches.push({url, options});
      if (url === base + "/api/session") return Promise.resolve(account.expired
        ? {ok: false, status: 401} : {ok: true, json: async () => ({email: "alice@example.test", admin: !!account.admin})});
      if (url === base + "/api/jobs") return Promise.resolve({ok: true, json: async () => ({jobs: account.jobs || []})});
      if (options.method === "PUT") return Promise.resolve({ok: true, json: async () => ({transcript: JSON.parse(options.body).transcript})});
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
    $: (id) => elements.get(id), body, context, requests, responses, fetches, stages, created, connections,
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
    jobId: "sample-job", status, stage, progress: 80, sourceDuration: 3.75, originalUrl: "/media/sample-job/original",
    cuts: Array.from({ length: 3 }, (_, index) => ({
      id: "cut-" + index, index: index + 1,
      start: index * 1.25, end: (index + 1) * 1.25, duration: 1.25,
      videoUrl: "/media/sample-job/clip-00" + (index + 1) + ".mp4",
      transcript: index ? "" : "Original words.", originalTranscript: index ? "" : "Original words.",
    })),
    warnings: [],
  };
}


const settle = () => new Promise(setImmediate);

test("processing holds one connection and releases it on completion or leaving", async () => {
  const ui = harness("/tools");
  ui.upload().respond(job());
  assert.equal(ui.connections.length, 1);
  assert.equal(ui.connections[0].url, "/tools/api/jobs/sample-job/activity");
  ui.responses.push(job());
  await ui.tick(1100);
  assert.equal(ui.connections.length, 1);
  assert.ok(!ui.connections[0].closed);
  ui.responses.push(job("complete"));
  await ui.tick(1100);
  assert.ok(ui.connections[0].closed);
  ui.upload().respond(job());
  ui.$("cancel-upload").click();
  assert.ok(ui.connections[1].closed);
});

test("terminal activity event closes the connection even if polling fails", () => {
  const ui = harness();
  ui.upload().respond(job());
  ui.connections[0].onmessage({data: "done"});
  assert.ok(ui.connections[0].closed);
});

test("email-only login opens the app or waiting page and handles failures", async () => {
  const loginSource = fs.readFileSync("static/login.js", "utf8");
  const generic = "Unable to sign in. Try again.";
  for (const base of ["", "/tools"]) {
    for (const [status, message] of [[200, null], [202, null], [401, generic],
                                   [429, "Too many attempts. Try again later."],
                                   [400, "Enter a valid email address."], [403, generic], [404, generic], [500, generic], [0, generic]]) {
      const elements = new Map(["login-form", "sign-in", "login-error", "email"].map(id => [id, new Element()]));
      elements.get("email").value = "test@example.test";
      let redirected;
      const context = vm.createContext({
        document: {body: {dataset: {basePath: base}}, getElementById: id => elements.get(id)},
        window: {location: {assign: url => { redirected = url; }}},
        fetch: async (url, options) => {
          assert.equal(url, base + "/api/login");
          assert.equal(options.headers["X-Requested-With"], "Splice");
          if (!status) throw new Error("Network failure");
          assert.deepEqual(JSON.parse(options.body), {email: "test@example.test"});
          return {ok: status === 200 || status === 202, status};
        },
      });
      vm.runInContext(loginSource, context);
      await elements.get("login-form").listeners.get("submit")[0]({preventDefault() {}});
      assert.equal(elements.get("sign-in").disabled, false);
      assert.equal(elements.get("login-error").hidden, message === null);
      if (message) {
        assert.equal(elements.get("login-error").textContent, message);
        assert.equal(redirected, undefined);
      } else {
        assert.equal(redirected, base + (status === 202 ? "/signup/thanks" : "/"));
      }
    }
  }
});

test("light blue UI, local assets and restricted formats", () => {
  const css = fs.readFileSync("static/styles.css", "utf8");
  assert.match(html, /name="color-scheme" content="light"/);
  assert.match(css, /color-scheme: light/);
  assert.match(css, /#235DE3/);
  assert.doesNotMatch(html + css, /fonts.googleapis|color-scheme: dark|#E6A23C|#C8872A/);
  assert.doesNotMatch(html, /\.mkv|\.avi|\.m4v/);
});

test("upload, progress, cut strip and original view", async () => {
  const ui = harness();
  await settle();
  const request = ui.upload();
  assert.equal(request.url, "/api/analyze-file");
  assert.equal(request.headers["X-Requested-With"], "Splice");
  request.upload.emit("progress", {lengthComputable: true, loaded: 4, total: 8});
  assert.equal(ui.$("stage-progress").getAttribute("aria-valuenow"), "50");
  request.respond(job("processing"));
  assert.equal(ui.body.dataset.screen, "loading");
  assert.equal(ui.$("cut-strip").children.length, 0);
  ui.responses.push(job("complete"));
  await ui.tick(1100);
  assert.equal(ui.body.dataset.screen, "result");
  const chips = ui.$("cut-strip").children;
  assert.equal(chips.length, 5);
  assert.equal(chips[0].dataset.cutId, "original");
  assert.equal(chips.at(-1).id, "new-video");
  assert.equal(ui.$("clip-player").src, "/media/sample-job/original");
  assert.equal(ui.$("transcript").disabled, true);
  chips[1].click();
  assert.equal(ui.$("selected-title").textContent, "Cut 1 of 3");
  assert.equal(ui.$("transcript").disabled, false);
  assert.equal(ui.$("selected-duration").textContent, "1.25 seconds");
  chips[0].click();
  assert.equal(ui.$("clip-player").src, "/media/sample-job/original");
  assert.equal(ui.$("clip-player").playCount, 2);
  assert.equal(ui.$("clip-player").getAttribute("aria-label"), "Original video");
  assert.equal(ui.$("selected-duration").textContent, "3.75 seconds");
  assert.equal(ui.$("selected-range").textContent, "00:00.00 – 00:03.75");
  assert.equal(ui.$("download-clip").href, "/media/sample-job/original");
  assert.equal(ui.$("transcript").disabled, true);
});

test("New Video resets after one Continue; cancellation preserves the player", async () => {
  const ui = harness();
  ui.upload().respond(job("complete"));
  ui.$("cut-strip").children.at(-1).click();
  assert.equal(ui.$("new-video-dialog").open, true);
  assert.match(ui.$("new-video-copy").textContent, /stay in Recent/);
  ui.$("new-video-cancel").click();
  assert.equal(ui.body.dataset.screen, "result");
  assert.equal(ui.$("clip-player").src, "/media/sample-job/original");
  ui.$("cut-strip").children.at(-1).click();
  ui.$("new-video-confirm").click();
  await settle();
  assert.equal(ui.$("new-video-dialog").open, false);
  assert.equal(ui.body.dataset.screen, "empty");
  assert.equal(ui.$("clip-player").src, null);
});

test("transcripts save to the correct cut and reset to recognised speech", async () => {
  const ui = harness();
  ui.upload().respond(job("complete"));
  ui.$("cut-strip").children[1].click();
  ui.$("transcript").value = "My own words.";
  ui.$("transcript").emit("input");
  ui.$("cut-strip").children[2].click();
  await settle();
  const save = ui.fetches.find((request) => request.options.method === "PUT");
  assert.equal(save.url, "/api/jobs/sample-job/transcript/cut-0");
  assert.equal(JSON.parse(save.options.body).transcript, "My own words.");
  assert.equal(save.options.headers["X-Requested-With"], "Splice");
  ui.$("cut-strip").children[1].click();
  assert.equal(ui.$("transcript").value, "My own words.");
  ui.$("reset-transcript").click();
  assert.equal(ui.$("transcript").value, "Original words.");
});

test("invalid files and size limits never upload", () => {
  const ui = harness();
  for (const file of [{name: "words.txt", size: 10}, {name: "legacy.mkv", size: 10}, {name: "empty.mp4", size: 0}, {name: "large.mov", size: 200 * 1024 * 1024 + 1}]) {
    ui.upload(file);
    assert.equal(ui.requests.length, 0);
    assert.equal(ui.$("upload-error").hidden, false);
  }
  ui.upload({name: "phone.MOV", size: 200 * 1024 * 1024});
  assert.equal(ui.requests.length, 1);
});

test("path deployment prefixes every API request", async () => {
  const ui = harness("/tools");
  await settle();
  assert.equal(ui.fetches[0].url, "/tools/api/session");
  assert.equal(ui.fetches[1].url, "/tools/api/jobs");
  const upload = ui.upload();
  assert.equal(upload.url, "/tools/api/analyze-file");
  upload.respond(job());
  ui.responses.push(job("complete"));
  await ui.tick(1100);
  assert.equal(ui.fetches.at(-1).url, "/tools/api/jobs/sample-job");
});

test("old upload and poll responses cannot overwrite a newer operation", async () => {
  const ui = harness();
  const first = ui.upload();
  first.emit("error");
  ui.$("retry-upload").click();
  assert.equal(ui.requests.length, 2);
  first.respond(job("complete"));
  assert.equal(ui.body.dataset.screen, "loading");
  ui.$("cancel-upload").click();
  assert.equal(ui.requests[1].aborted, true);
  ui.requests[1].respond(job("complete"));
  assert.equal(ui.body.dataset.screen, "empty");
});

test("a failed job never exposes partial cuts", () => {
  const ui = harness();
  const result = job("failed"); result.error = "This file could not be read as a video.";
  ui.upload().respond(result);
  assert.equal(ui.body.dataset.screen, "loading");
  assert.equal(ui.$("loading-error").hidden, false);
  assert.equal(ui.$("cut-strip").children.length, 0);
});

test("homepage loads recent jobs without storing private drafts in browser storage", async () => {
  const recent = {...job("complete"), originalFilename: "Saved reel.mp4", createdAt: 1793200000};
  const ui = harness("", [recent], {jobs: [recent]});
  await settle();
  assert.equal(ui.body.dataset.screen, "empty");
  assert.equal(ui.$("account-email").textContent, "alice@example.test");
  assert.equal(ui.$("admin-link").hidden, true);
  assert.equal(ui.$("recent-jobs").children.length, 1);
  const card = ui.$("recent-jobs").children[0];
  assert.equal(card.children[0].className, "cut-thumbnail");
  assert.match(card.children[1].textContent, /3 cuts/);
  assert.doesNotMatch(card.getAttribute("aria-label"), /Saved reel/);
  ui.$("recent-jobs").children[0].click();
  await settle();
  assert.equal(ui.body.dataset.screen, "result");
  assert.equal(ui.$("clip-player").src, recent.originalUrl);
  assert.doesNotMatch(source, /sessionStorage|localStorage/);
});

test("session expiry redirects to prefixed login before loading Recent", async () => {
  const ui = harness("/tools", [], {expired: true});
  await settle();
  assert.equal(ui.fetches.at(-1), "redirect:/tools/login");
  assert.equal(ui.fetches.length, 2);
});

test("admin account link and sign out use the signed-in session", async () => {
  const ui = harness("/tools", [{ok: true}], {admin: true});
  await settle();
  assert.equal(ui.$("admin-link").hidden, false);
  ui.$("logout").click();
  await settle();
  assert.equal(ui.fetches.at(-2).url, "/tools/api/logout");
  assert.equal(ui.fetches.at(-2).options.method, "POST");
  assert.equal(ui.fetches.at(-1), "redirect:/tools/login");
});


test("Original and Cut 1 share a decoded thumbnail; arrow keys include Original", () => {
  const ui = harness();
  ui.upload().respond(job("complete"));
  const cards = ui.$("cut-strip").children;
  assert.equal(cards[0].children[1].textContent, "Original");
  assert.equal(cards[1].children[1].textContent, "Cut 1");
  const preview = ui.created.find(el => el.tagName === "VIDEO");
  assert.equal(preview.src, job().cuts[0].videoUrl);
  preview.videoWidth = 1080; preview.videoHeight = 1920;
  preview.emit("loadeddata");
  const original = cards[0].children[0].children[1];
  const cut = cards[1].children[0].children[1];
  assert.equal(original.hidden, false);
  assert.equal(original.src, cut.src);
  assert.match(original.src, /^data:image\/jpeg/);
  assert.equal(ui.$("clip-player").src, job().originalUrl);
  cards[0].emit("keydown", {key: "ArrowRight"});
  assert.equal(ui.$("clip-player").src, job().cuts[0].videoUrl);
  cards[1].emit("keydown", {key: "ArrowLeft"});
  assert.equal(ui.$("clip-player").src, job().originalUrl);
});

test("Recent decodes first frames and cancels stale previews when opening a video", async () => {
  const recent = {...job("complete"), createdAt: 1793200000};
  const ui = harness("", [recent], {jobs: [recent]});
  await settle();
  const preview = ui.created.find(el => el.tagName === "VIDEO");
  assert.equal(preview.src, recent.cuts[0].videoUrl);
  preview.videoWidth = 1080; preview.videoHeight = 1920;
  const img = ui.$("recent-jobs").children[0].children[0].children[1];
  preview.emit("loadeddata");
  assert.equal(img.hidden, false);
  assert.match(img.src, /^data:image\/jpeg/);
  ui.$("recent-jobs").children[0].click();
  await settle();
  assert.equal(preview.src, null);
  assert.equal(ui.$("clip-player").src, recent.originalUrl);
});

test("regular sign-in and access management remain email-only", () => {
  for (const file of ["login.html", "login.js", "admin.html", "admin.js", "signup-thanks.html"]) {
    assert.doesNotMatch(fs.readFileSync("static/" + file, "utf8"), /password|initialPassword|reauth/i);
  }
  const thanks = fs.readFileSync("static/signup-thanks.html", "utf8");
  assert.match(thanks, /Thank you for creating an account/);
  assert.match(thanks, /Discord or in coaching chat/);
  assert.match(fs.readFileSync("static/login.html", "utf8"), /type="email"/);
});

test("separate admin sign-in trims only outer whitespace and handles failures", async () => {
  assert.match(fs.readFileSync("static/admin-login.html", "utf8"), /type="password"/);
  for (const base of ["", "/tools"]) {
    for (const responseStatus of [200, 401, 429, 500]) {
      const elements = new Map(["admin-login-form", "admin-email", "admin-password", "admin-sign-in", "admin-login-status"].map(id => [id, new Element()]));
      elements.get("admin-email").value = " admin@example.test ";
      elements.get("admin-password").value = " \nsynthetic  phrase\t ";
      const redirects = [];
      const context = vm.createContext({
        document: {body: {dataset: {basePath: base}}, getElementById: id => elements.get(id)},
        window: {location: {assign: url => redirects.push(url)}},
        fetch: async (url, options) => {
          assert.equal(elements.get("admin-sign-in").disabled, true);
          assert.equal(url, base + "/api/admin/login");
          assert.equal(options.method, "POST");
          assert.equal(options.headers["X-Requested-With"], "Splice");
          assert.deepEqual(JSON.parse(options.body), {email: "admin@example.test", password: "synthetic  phrase"});
          return {ok: responseStatus === 200, status: responseStatus, headers: {get: () => "120"}};
        },
      });
      vm.runInContext(fs.readFileSync("static/admin-login.js", "utf8"), context);
      await elements.get("admin-login-form").listeners.get("submit")[0]({preventDefault() {}});
      assert.equal(elements.get("admin-password").value, "");
      assert.equal(elements.get("admin-sign-in").disabled, false);
      if (responseStatus === 200) assert.deepEqual(redirects, [base + "/admin"]);
      else {
        assert.deepEqual(redirects, []);
        assert.equal(elements.get("admin-login-status").hidden, false);
        const message = elements.get("admin-login-status").textContent;
        if (responseStatus === 401) assert.equal(message, "Invalid email or password");
        else if (responseStatus === 429) assert.match(message, /Try again in 2 minutes/);
        else assert.match(message, /could not be completed/);
      }
    }
  }
});

test("admin can approve directly, accept or decline applicants and remove access", async () => {
  for (const base of ["", "/tools"]) {
    const elements = new Map(["applicants", "users", "approve-form", "access-email", "approve-email", "admin-status"].map(id => [id, new Element()]));
    const users = [{email: "lukesa100@gmail.com", enabled: 1, status: "approved"},
                   {email: "applicant@example.test", enabled: 0, status: "pending"},
                   {email: "member@example.test", enabled: 1, status: "approved"}];
    const calls = [];
    const context = vm.createContext({
      document: {body: {dataset: {basePath: base}}, getElementById: id => elements.get(id),
                 createElement: tag => new Element(tag), querySelectorAll: () => []},
      window: {location: {assign() { assert.fail("Approval must not redirect"); }}},
      fetch: async (url, options) => {
        calls.push({url, options});
        assert.equal(url, base + "/api/admin/users");
        return {ok: true, status: 200, json: async () => ({users})};
      },
    });
    vm.runInContext(fs.readFileSync("static/admin.js", "utf8"), context);
    await settle();
    assert.equal(elements.get("applicants").children.length, 1);
    assert.equal(elements.get("users").children.length, 2);
    assert.equal(elements.get("users").children[0].children[1].children[0].textContent, "Administrator");
    for (const [id, index, action] of [["applicants", 0, "accept"], ["applicants", 1, "decline"], ["users", 0, "remove"]]) {
      const row = elements.get(id).children[id === "users" ? 1 : 0];
      const button = row.children[1].children[index];
      button.click();
      await settle();
      const body = JSON.parse(calls.at(-2).options.body);
      assert.equal(body.action, action);
      assert.deepEqual(Object.keys(body).sort(), ["action", "email"]);
      assert.equal(Object.hasOwn(body, "initialPassword"), false);

    }
    elements.get("access-email").value = " direct@example.test ";
    await elements.get("approve-form").listeners.get("submit")[0]({preventDefault() {}});
    assert.deepEqual(JSON.parse(calls.at(-2).options.body), {action: "approve", email: " direct@example.test "});
    assert.equal(elements.get("access-email").value, "");
    assert.equal(elements.get("approve-email").disabled, false);
  }
});
