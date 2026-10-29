"use strict";
const $ = (id) => document.getElementById(id);
const base = document.body.dataset.basePath || "";
$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("sign-in").disabled = true;
  $("login-error").hidden = true;
  try {
    const response = await fetch(base + "/api/login", {method: "POST", headers: {"Content-Type": "application/json", "X-Requested-With": "Splice"},
      body: JSON.stringify({email: $("email").value})});
    if (!response.ok) {
      if (response.status === 400) throw new Error("Enter a valid email address.");
      if (response.status === 429) throw new Error("Too many attempts. Try again later.");
      throw new Error("Unable to sign in. Try again.");
    }
    window.location.assign(base + (response.status === 202 ? "/signup/thanks" : "/"));
  } catch (error) {
    $("login-error").textContent = error.message === "Enter a valid email address." || error.message.startsWith("Too many") ? error.message : "Unable to sign in. Try again.";
    $("login-error").hidden = false;
  } finally { $("sign-in").disabled = false; }
});
