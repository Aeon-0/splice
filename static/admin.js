"use strict";
const $ = (id) => document.getElementById(id);
const base = document.body.dataset.basePath || "";
async function api(method, body) {
  const response = await fetch(base + "/api/admin/users", {method, cache: "no-store", headers: {"Content-Type": "application/json", "X-Requested-With": "Splice"}, body: body ? JSON.stringify(body) : undefined});
  if (response.status === 401) { window.location.assign(base + "/login"); throw new Error("Please sign in again."); }
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "The access change failed.");
  return result;
}
function status(text) { $("admin-status").textContent = text; $("admin-status").hidden = false; }
async function refresh() {
  const result = await api("GET");
  $("users").replaceChildren();
  for (const user of result.users) {
    const row = document.createElement("div"); row.className = "access-row";
    const label = document.createElement("span"); label.textContent = user.email + (user.enabled ? "" : " · access removed"); row.append(label);
    if (user.enabled && user.email !== "lukesa100@gmail.com") {
      const button = document.createElement("button"); button.type = "button"; button.className = "text-button"; button.textContent = "Remove access";
      button.addEventListener("click", () => change("remove", user.email)); row.append(button);
    }
    $("users").append(row);
  }
}
async function change(action, email) {
  const password = $("admin-password").value;
  if (!password) { status("Enter your administrator password to confirm this change."); $("admin-password").focus(); return; }
  try {
    const initialPassword = $("initial-password").value;
    $("admin-password").value = ""; $("initial-password").value = "";
    await api("POST", {action, email, password, initialPassword});
    status(action === "add" ? "Access approved. Share the initial password privately." : "Access removed and sessions revoked.");
    await refresh();
  } catch (error) { status(error.message); }
}
$("access-form").addEventListener("submit", (event) => { event.preventDefault(); change("add", $("email").value); });
refresh().catch((error) => status(error.message));
