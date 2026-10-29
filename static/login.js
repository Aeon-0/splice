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
    if (!response.ok) {
      if (response.status === 401) throw new Error("Invalid email or password");
      if (response.status === 429) throw new Error("Too many attempts. Try again later.");
      throw new Error("Unable to sign in. Try again.");
    }
    window.location.assign(base + "/");
  } catch (error) {
    $("login-error").textContent = error.message === "Invalid email or password" || error.message.startsWith("Too many") ? error.message : "Unable to sign in. Try again.";
    $("login-error").hidden = false;
  } finally { $("sign-in").disabled = false; }
});
