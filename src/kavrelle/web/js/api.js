/* Kavrelle API client: timeouts, safe retries for reads, and friendly error messages. */
(function () {
  "use strict";

  class ApiError extends Error {
    constructor(status, message) {
      super(message);
      this.name = "ApiError";
      this.status = status; // 0 = network problem
    }
  }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function messageFor(status, data) {
    if (status >= 500) return "Our servers are having a moment. Please try again.";
    if (data && typeof data.detail === "string") return data.detail;
    if (data && Array.isArray(data.detail)) return "Please check the details and try again.";
    if (status === 404) return "We couldn’t find that.";
    return `Something went wrong (${status}).`;
  }

  async function request(method, path, body, opts) {
    const { timeout = 12000, retries = method === "GET" ? 2 : 0 } = opts || {};
    for (let attempt = 0; ; attempt++) {
      const ctl = new AbortController();
      const timer = setTimeout(() => ctl.abort(), timeout);
      try {
        const res = await fetch(path, {
          method,
          headers: body ? { "Content-Type": "application/json" } : {},
          body: body ? JSON.stringify(body) : undefined,
          signal: ctl.signal,
          cache: "no-store",
          credentials: "same-origin",
        });
        const text = await res.text();
        let data = null;
        if (text) {
          try { data = JSON.parse(text); } catch (_) { data = null; }
        }
        if (!res.ok) {
          const err = new ApiError(res.status, messageFor(res.status, data));
          if (res.status >= 500 && attempt < retries) { await sleep(500 * (attempt + 1)); continue; }
          throw err;
        }
        window.dispatchEvent(new CustomEvent("kv:online"));
        return data;
      } catch (e) {
        if (e instanceof ApiError) throw e;
        if (attempt < retries) { await sleep(500 * (attempt + 1)); continue; }
        window.dispatchEvent(new CustomEvent("kv:unreachable"));
        throw new ApiError(0, e && e.name === "AbortError"
          ? "The mall is taking too long to answer. Please try again."
          : "Can’t reach the mall right now. Check your connection and try again.");
      } finally {
        clearTimeout(timer);
      }
    }
  }

  const enc = encodeURIComponent;
  window.KV = window.KV || {};
  window.KV.ApiError = ApiError;
  window.KV.api = {
    health: () => request("GET", "/api/health", null, { retries: 0, timeout: 6000 }),
    catalog: () => request("GET", "/api/catalog"),
    newCart: () => request("POST", "/api/cart", null, { retries: 1 }),
    cart: (t) => request("GET", `/api/cart/${enc(t)}`),
    addItem: (t, slug, variant, quantity) =>
      request("POST", `/api/cart/${enc(t)}/items`, { product_slug: slug, variant, quantity }),
    setQty: (t, id, quantity) => request("PATCH", `/api/cart/${enc(t)}/items/${enc(id)}`, { quantity }),
    removeItem: (t, id) => request("DELETE", `/api/cart/${enc(t)}/items/${enc(id)}`),
    checkout: (t, buyer) => request("POST", `/api/cart/${enc(t)}/checkout`, buyer, { timeout: 20000 }),
    lookup: (code, email, zip) => request("POST", `/api/orders/${enc(code)}/lookup`, { email, zip_code: zip }),
    cancel: (code, email, zip) => request("POST", `/api/orders/${enc(code)}/cancel`, { email, zip_code: zip }),
    me: () => request("GET", "/api/me", null, { retries: 0 }),
    myOrders: () => request("GET", "/api/me/orders"),
    signup: (body) => request("POST", "/api/auth/signup", body),
    login: (email, password) => request("POST", "/api/auth/login", { email, password }),
    logout: () => request("POST", "/api/auth/logout"),
    sendChat: (message, conversationId) =>
      request("POST", "/api/support/messages", { message, conversation_id: conversationId || null }, { timeout: 15000 }),
    chatJob: (id) => request("GET", `/api/support/jobs/${enc(id)}`, null, { retries: 1, timeout: 10000 }),
    chatHistory: (id) => request("GET", `/api/support/conversations/${enc(id)}`),
    adminOverview: () => request("GET", "/api/admin/overview", null, { retries: 0 }),
    adminRuns: () => request("GET", "/api/admin/runs"),
    adminRun: (id) => request("GET", `/api/admin/runs/${enc(id)}`),
    adminStart: (n) => request("POST", "/api/admin/runs", { n_runs: n }),
    adminProgress: () => request("GET", "/api/admin/lab/progress", null, { retries: 0 }),
    adminChats: () => request("GET", "/api/admin/conversations"),
    adminChat: (id) => request("GET", `/api/admin/conversations/${enc(id)}`),
  };
})();
