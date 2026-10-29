"use strict";
const $ = (id) => document.getElementById(id);
const base = document.body.dataset.basePath || "";
$("signup-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("create-account").disabled = true;
  $("signup-error").hidden = true;
  try {
    const response = await fetch(base + "/api/signup", {method: "POST", headers: {"Content-Type": "application/json", "X-Requested-With": "Splice"},
      body: JSON.stringify({email: $("email").value, password: $("password").value})});
    if (!response.ok) {
      if (response.status === 429) throw new Error("Too many attempts. Try again later.");
      if (response.status === 400) {
        const result = await response.json();
        if (typeof result.detail === "string") throw new Error(result.detail);
      }
      throw new Error("Unable to create an account. Try again.");
    }
    window.location.assign(base + "/signup/thanks");
  } catch (error) {
    $("signup-error").textContent = ["Too many attempts. Try again later.", "Enter a valid email address.",
      "Use a password of 15–128 characters; avoid common passwords."].includes(error.message)
      ? error.message : "Unable to create an account. Try again.";
    $("signup-error").hidden = false;
  } finally {
    $("password").value = "";
    $("create-account").disabled = false;
  }
});
