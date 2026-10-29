"use strict";
const $ = (id) => document.getElementById(id);
const base = document.body.dataset.basePath || "";
let changing = false;
async function api(method, body) {
  const response = await fetch(base + "/api/admin/users", {method, cache: "no-store", headers: {"Content-Type": "application/json", "X-Requested-With": "Splice"}, body: body ? JSON.stringify(body) : undefined});
  if (response.status === 401 || response.status === 404) {
    window.location.assign(base + "/admin");
    throw new Error("Please sign in again.");
  }
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "The access change failed.");
  return result;
}
function status(text) { $("admin-status").textContent = text; $("admin-status").hidden = false; }
async function refresh() {
  const result = await api("GET");
  $("applicants").replaceChildren();
  $("users").replaceChildren();
  for (const user of result.users) {
    const pending = user.status === "pending";
    if (!pending && !user.enabled) continue;
    const row = document.createElement("div"); row.className = "access-row";
    const label = document.createElement("span"); label.textContent = user.email; row.append(label);
    const actions = document.createElement("div"); actions.className = "access-actions";
    if (user.email !== "lukesa100@gmail.com") {
      for (const [action, text] of pending ? [["accept", "Accept"], ["decline", "Decline"]] : [["remove", "Remove"]]) {
        const button = document.createElement("button"); button.type = "button";
        button.className = action === "accept" ? "primary-button" : "text-button"; button.textContent = text;
        button.setAttribute("aria-label", text + " " + user.email);
        button.addEventListener("click", () => change(action, user.email)); actions.append(button);
      }
    } else {
      const role = document.createElement("span"); role.textContent = "Administrator"; actions.append(role);
    }
    row.append(actions);
    $(pending ? "applicants" : "users").append(row);
  }
  if (!$("applicants").children.length) {
    const empty = document.createElement("p"); empty.textContent = "No pending applicants."; $("applicants").append(empty);
  }
}
async function change(action, email) {
  if (changing) return;
  changing = true;
  document.querySelectorAll(".access-actions button").forEach(button => { button.disabled = true; });
  $("approve-email").disabled = true;
  try {
    await api("POST", {action, email});
    status({approve: "Access approved.", accept: "Access approved.", decline: "Application declined.", remove: "Access removed and sessions revoked."}[action]);
    if (action === "approve") $("access-email").value = "";
    await refresh();
  } catch (error) { status(error.message); }
  finally {
    changing = false;
    $("approve-email").disabled = false;
    document.querySelectorAll(".access-actions button").forEach(button => { button.disabled = false; });
  }
}
$("approve-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  await change("approve", $("access-email").value);
});
refresh().catch((error) => status(error.message));
