"use strict";
const $ = (id) => document.getElementById(id);
const base = document.body.dataset.basePath || "";
let signingIn = false;
$("admin-login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (signingIn) return;
  signingIn = true;
  $("admin-sign-in").disabled = true;
  $("admin-login-status").hidden = true;
  try {
    const response = await fetch(base + "/api/admin/login", {
      method: "POST", cache: "no-store",
      headers: {"Content-Type": "application/json", "X-Requested-With": "Splice"},
      body: JSON.stringify({email: $("admin-email").value.trim(), password: $("admin-password").value.trim()}),
    });
    $("admin-password").value = "";
    if (response.status === 401) throw new Error("Invalid email or password");
    if (response.status === 429) {
      const seconds = Number(response.headers.get("Retry-After"));
      throw new Error(seconds > 0 ? `Too many attempts. Try again in ${Math.ceil(seconds / 60)} minutes.` : "Too many attempts. Try again later.");
    }
    if (!response.ok) throw new Error("Sign in could not be completed. Please try again.");
    window.location.assign(base + "/admin");
  } catch (error) {
    $("admin-password").value = "";
    $("admin-login-status").textContent = error instanceof TypeError ? "Could not connect. Please try again." : error.message;
    $("admin-login-status").hidden = false;
  } finally {
    signingIn = false;
    $("admin-sign-in").disabled = false;
  }
});
