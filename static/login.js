"use strict";
const $ = (id) => document.getElementById(id);
const base = document.body.dataset.basePath || "";
$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("sign-in").disabled = true;
  $("login-error").hidden = true;
  try {
    const response = await fetch(base + "/api/login", {method: "POST", headers: {"Content-Type": "application/json", "X-Requested-With": "Splice"},
      body: JSON.stringify({email: $("email").value, password: $("password").value})});
    $("password").value = "";
    if (!response.ok) throw new Error(response.status === 429 ? "Too many attempts. Try again later." : "Invalid email or password");
    window.location.assign(base + "/");
  } catch (error) {
    $("login-error").textContent = error.message === "Invalid email or password" || error.message.startsWith("Too many") ? error.message : "Unable to sign in. Try again.";
    $("login-error").hidden = false;
  } finally { $("sign-in").disabled = false; }
});
