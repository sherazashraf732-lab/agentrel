/* Contact form and password reset for the Kavrelle information pages. */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

  async function post(path, body) {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), 15000);
    try {
      const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
        signal: ctl.signal, credentials: "same-origin", cache: "no-store" });
      let data = null;
      try { data = await res.json(); } catch (_) { data = null; }
      if (!res.ok) {
        if (res.status >= 500) throw new Error("Our servers are having a moment. Please try again.");
        if (data && typeof data.detail === "string") throw new Error(data.detail);
        throw new Error("Please check the details and try again.");
      }
      return data;
    } catch (e) {
      if (e.name === "AbortError" || e instanceof TypeError) throw new Error("Can’t reach Kavrelle right now. Check your connection and try again.");
      throw e;
    } finally { clearTimeout(timer); }
  }
  function check(rules) {
    let first = null;
    for (const [id, errId, test] of rules) {
      const msg = test($(id).value);
      $(errId).textContent = msg;
      $(id).setAttribute("aria-invalid", msg ? "true" : "false");
      if (msg && !first) first = $(id);
    }
    if (first) first.focus();
    return !first;
  }
  function showError(id, msg) { $(id).textContent = msg; $(id).hidden = !msg; }
  async function busy(btn, label, fn) {
    const old = btn.textContent; btn.disabled = true; btn.textContent = label;
    try { await fn(); } finally { btn.disabled = false; btn.textContent = old; }
  }

  const contact = $("contactForm");
  if (contact) {
    const count = () => { $("cCount").textContent = `${$("cMsg").value.length} / 3000`; };
    $("cMsg").addEventListener("input", count); count();
    contact.addEventListener("submit", (e) => {
      e.preventDefault();
      if (!check([["cName", "cNameErr", (v) => (v.trim() ? "" : "Please enter your name.")],
        ["cEmail", "cEmailErr", (v) => (EMAIL_RE.test(v.trim()) ? "" : "Please enter a valid email.")],
        ["cTopic", "cTopicErr", (v) => (v ? "" : "Please choose a topic.")],
        ["cMsg", "cMsgErr", (v) => (v.trim().length >= 10 ? "" : "Please write at least 10 characters.")]])) return;
      busy($("cSubmit"), "Sending…", async () => {
        showError("cError", "");
        try {
          await post("/api/contact", { name: $("cName").value.trim(), email: $("cEmail").value.trim(), topic: $("cTopic").value,
            message: $("cMsg").value.trim(), website: $("cWebsite").value });
          contact.hidden = true; $("contactDone").hidden = false; $("contactDone").focus();
        } catch (err) { showError("cError", err.message); }
      });
    });
  }

  const forgot = $("forgotForm");
  if (forgot) {
    const done = (title, text) => { forgot.hidden = true; $("resetForm").hidden = true; $("doneTitle").textContent = title; $("doneText").textContent = text; $("resetDone").hidden = false; $("resetDone").focus(); };
    const token = new URLSearchParams(location.search).get("token");
    if (token) {
      forgot.hidden = true; $("resetForm").hidden = false; $("rTitle").textContent = "Choose a new password";
      history.replaceState(null, "", "/reset"); // keep the token out of history and screenshots
      $("resetForm").addEventListener("submit", (e) => {
        e.preventDefault();
        if (!check([["rPass", "rPassErr", (v) => (v.length >= 8 && /[A-Za-z]/.test(v) && /\d/.test(v) ? "" : "8+ characters with letters and numbers.")],
          ["rPass2", "rPass2Err", (v) => (v === $("rPass").value ? "" : "The two passwords don’t match.")]])) return;
        busy($("rSubmit"), "Saving…", async () => {
          showError("rError", "");
          try {
            await post("/api/auth/reset", { token, password: $("rPass").value });
            done("Password changed", "You can now sign in with your new password. Every other device has been signed out.");
          } catch (err) { showError("rError", err.message); }
        });
      });
    } else {
      forgot.addEventListener("submit", (e) => {
        e.preventDefault();
        if (!check([["fEmail", "fEmailErr", (v) => (EMAIL_RE.test(v.trim()) ? "" : "Please enter a valid email.")]])) return;
        busy($("fSubmit"), "Sending…", async () => {
          showError("fError", "");
          try {
            const r = await post("/api/auth/forgot", { email: $("fEmail").value.trim() });
            done("Check your inbox", r.detail);
          } catch (err) { showError("fError", err.message); }
        });
      });
    }
  }
})();
