/* Kavrelle app: loads the catalogue, runs the 3D mall (or the grid fallback),
   and drives the product sheet, cart, checkout and order tracking.
   Every user-visible string from the server goes through textContent (never innerHTML). */
(function () {
  "use strict";
  const { api, ApiError, Mall3D } = window.KV;
  const $ = (id) => document.getElementById(id);
  const money = (cents, cur = "USD") => new Intl.NumberFormat("en-US", { style: "currency", currency: cur }).format(cents / 100);
  const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
  const ZIP_RE = /^[A-Za-z0-9 -]{3,12}$/;
  const SHOP_COLORS = ["#2E8C83", "#E8A93A", "#E2552D", "#6B7F3A", "#5A6FC8", "#C2477A"];
  const MAX_QTY = 10;

  const store = {
    get(k) { try { return window.localStorage.getItem(k); } catch (_) { return null; } },
    set(k, v) { try { window.localStorage.setItem(k, v); } catch (_) { /* private mode: fine */ } },
    del(k) { try { window.localStorage.removeItem(k); } catch (_) { /* ignore */ } },
  };

  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : String(v));
    }
    for (const kid of kids.flat()) if (kid !== null && kid !== undefined) el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    return el;
  }
  const shopColor = (s) => SHOP_COLORS[(s.position - 1 + (s.floor - 1) * 2) % SHOP_COLORS.length];

  const state = {
    catalog: [], shops: new Map(), products: new Map(),
    mall: null, mode: "3d", floor: 1, shop: null,
    cart: null, token: store.get("kv_cart"),
    product: null, variant: null, qty: 1, lastOrder: null, tracked: null,
  };

  // ---------- toasts / offline banner ----------
  function toast(text, opts = {}) {
    const el = h("div", { class: "toast" + (opts.bad ? " bad" : ""), role: opts.bad ? "alert" : "status" }, h("span", { text }));
    if (opts.action) el.append(h("button", { type: "button", text: opts.action.label, onclick: () => { el.remove(); opts.action.run(); } }));
    $("toasts").append(el);
    setTimeout(() => el.remove(), opts.ms || 3800);
  }
  let offline = false;
  function setOffline(v, text) {
    offline = v;
    $("offline").hidden = !v;
    if (text) $("offlineText").textContent = text;
  }
  window.addEventListener("offline", () => setOffline(true, "You’re offline. Some things won’t work until you reconnect."));
  window.addEventListener("online", () => setOffline(false));
  window.addEventListener("kv:unreachable", () => setOffline(true, "We can’t reach the mall right now. Your cart is safe."));
  window.addEventListener("kv:online", () => { if (offline && navigator.onLine !== false) setOffline(false); });
  $("offlineRetry").addEventListener("click", async () => {
    try { await api.health(); setOffline(false); if (state.token) await refreshCart(); toast("Back online"); }
    catch (_) { toast("Still can’t reach the mall. We’ll keep trying.", { bad: true }); }
  });

  // ---------- loader ----------
  function loadProgress(frac, text) {
    $("loadBar").style.width = `${Math.round(Math.max(0, Math.min(1, frac)) * 100)}%`;
    if (text) $("loadStatus").textContent = text;
  }
  function loadFail(msg) {
    $("loadError").hidden = false;
    $("loadErrorText").textContent = msg;
    $("loadStatus").textContent = "";
  }
  $("loadRetry").addEventListener("click", () => { $("loadError").hidden = true; boot(); });

  // ---------- directory / grid ----------
  function renderDirectory() {
    const root = $("dirFloors");
    root.replaceChildren();
    for (const floor of [1, 2]) {
      const list = h("ul");
      for (const s of state.catalog.filter((x) => x.floor === floor)) {
        list.append(h("li", {}, h("button", { type: "button", "data-shop": s.slug, onclick: () => openShop(s.slug) },
          h("span", { class: "swatch", "aria-hidden": "true" }), h("span", { class: "d-name", text: s.name }),
          h("span", { class: "d-meta", text: s.category }))));
      }
      root.append(h("h3", { text: `Floor ${floor}` }), list);
    }
    root.querySelectorAll("button[data-shop]").forEach((b) => {
      const s = state.shops.get(b.dataset.shop);
      b.querySelector(".swatch").style.background = shopColor(s);
    });
  }
  function markDirectory() {
    document.querySelectorAll("#dirFloors button[data-shop]").forEach((b) => b.setAttribute("aria-current", String(b.dataset.shop === state.shop)));
  }

  function renderGrid() {
    const root = $("grid2d");
    root.replaceChildren(h("section", { class: "hero2d" },
      h("h1", {}, "Everything, under ", h("span", { text: "one roof." })),
      h("p", { text: "Twelve shops across two floors: fashion, beauty, tech, home and more. Tap any product to see it up close." })));
    for (const s of state.catalog) {
      const head = h("header", {}, h("div", {}, h("div", { class: "fl", text: `Floor ${s.floor} · ${s.category}` }), h("h2", { text: s.name }), h("p", { text: s.description })));
      head.style.background = shopColor(s);
      const sec = h("section", { class: "shop-sec", id: `shop-${s.slug}` }, head);
      if (!s.products.length) sec.append(h("div", { class: "no-products", text: "Opening soon. New kitchens are moving in." }));
      else {
        const cards = h("div", { class: "cards" });
        for (const p of s.products) {
          cards.append(h("button", { class: "card", type: "button", "data-product": p.slug, onclick: () => openProduct(p.slug) },
            h("div", { class: "ph" }, h("img", { src: p.image_url || "", alt: p.name, loading: "lazy", onerror: (e) => { e.target.hidden = true; } })),
            h("span", { class: "n", text: p.name }), h("span", { class: "pr", text: money(p.price_cents, p.currency) })));
        }
        sec.append(cards);
      }
      root.append(sec);
    }
  }

  // ---------- navigation ----------
  function setMode(mode, quiet) {
    if (mode === "3d" && !state.mall) { if (!quiet) toast("3D isn’t available on this device, showing the grid instead.", { bad: true }); mode = "2d"; }
    state.mode = mode;
    document.body.classList.toggle("mode-2d", mode === "2d");
    $("grid2d").hidden = mode !== "2d";
    $("directory").hidden = mode === "2d";
    $("hint").hidden = mode === "2d";
    $("floorSeg").hidden = mode === "2d";
    document.querySelectorAll("#viewSeg button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mode === mode)));
    if (state.mall) state.mall.pause(mode === "2d");
    if (mode === "2d") $("shopCard").hidden = true;
    else if (state.shop) showShopCard(state.shop);
    store.set("kv_mode", mode);
  }
  function setFloor(floor) {
    state.floor = floor;
    document.querySelectorAll("#floorSeg button").forEach((b) => b.setAttribute("aria-pressed", String(Number(b.dataset.floor) === floor)));
  }
  function showShopCard(slug) {
    const s = state.shops.get(slug);
    $("shopKicker").textContent = `Floor ${s.floor} · ${s.category}`;
    $("shopName").textContent = s.name;
    $("shopDesc").textContent = s.description;
    $("shopCard").hidden = state.mode !== "3d";
  }
  function openShop(slug, opts = {}) {
    const s = state.shops.get(slug);
    if (!s) return;
    state.shop = slug;
    setFloor(s.floor);
    markDirectory();
    if (state.mode === "3d" && state.mall) { state.mall.goShop(slug); showShopCard(slug); $("scene").focus({ preventScroll: true }); }
    else { const el = $(`shop-${slug}`); if (el) el.scrollIntoView({ behavior: "smooth", block: "start" }); }
    if (!opts.noHash) setHash(`shop-${slug}`);
  }
  function leaveShop() {
    state.shop = null;
    markDirectory();
    $("shopCard").hidden = true;
    if (state.mall) state.mall.goConcourse(state.floor);
    setHash("");
  }
  function setHash(v) {
    try { history.replaceState(null, "", v ? `#${v}` : location.pathname); } catch (_) { /* ignore */ }
  }

  // ---------- panels ----------
  const sheets = ["productSheet", "cartDrawer"];
  function openSheet(id) {
    sheets.forEach((s) => { if (s !== id) closeSheet(s, true); });
    const el = $(id);
    el.classList.add("open"); el.setAttribute("aria-hidden", "false");
    $("scrim").classList.add("show");
    setTimeout(() => (el.querySelector("[data-close]") || el).focus(), 50);
  }
  function closeSheet(id, keepScrim) {
    const el = $(id);
    if (!el.classList.contains("open")) return;
    el.classList.remove("open"); el.setAttribute("aria-hidden", "true");
    if (!keepScrim) $("scrim").classList.remove("show");
  }
  $("scrim").addEventListener("click", () => sheets.forEach((s) => closeSheet(s)));
  document.querySelectorAll(".sheet [data-close]").forEach((b) => b.addEventListener("click", () => closeSheet(b.closest(".sheet").id)));
  document.querySelectorAll("dialog [data-close]").forEach((b) => b.addEventListener("click", () => b.closest("dialog").close()));

  // ---------- product sheet ----------
  function openProduct(slug) {
    const p = state.products.get(slug);
    if (!p) { toast("That product isn’t available any more.", { bad: true }); return; }
    state.product = p;
    const firstInStock = p.variants.find((v) => v.in_stock);
    state.variant = p.variants.length === 1 && firstInStock ? firstInStock.label : null;
    state.qty = 1;
    const img = $("pImg");
    img.hidden = false; img.src = p.image_url || ""; img.alt = p.name;
    img.onerror = () => { img.hidden = true; };
    const credit = $("pCredit");
    const url = (p.image_credit || "").match(/https:\/\/www\.pexels\.com\/photo\/\d+\/?/);
    credit.hidden = !url; credit.textContent = "Photo: Pexels"; if (url) credit.href = url[0];
    $("pShop").textContent = state.shops.get(p.shop).name;
    $("pName").textContent = p.name;
    $("pPrice").textContent = money(p.price_cents, p.currency);
    $("pDesc").textContent = p.description || "";
    $("pColor").textContent = p.color ? `Colour: ${p.color}` : "";
    $("pVarLabel").textContent = p.variant_label;
    const chips = $("pChips");
    chips.replaceChildren(...p.variants.map((v) => h("button", {
      class: "chip", type: "button", text: v.label, disabled: !v.in_stock,
      "aria-pressed": String(v.label === state.variant), title: v.in_stock ? null : "Out of stock",
      onclick: () => { state.variant = v.label; hideError("pError"); renderProductState(); },
    })));
    hideError("pError");
    renderProductState();
    openSheet("productSheet");
    setHash(`product-${slug}`);
  }
  function renderProductState() {
    const p = state.product;
    if (!p) return;
    $("pChips").querySelectorAll(".chip").forEach((c) => c.setAttribute("aria-pressed", String(c.textContent === state.variant)));
    $("pVarPicked").textContent = state.variant || "Choose one";
    $("qVal").textContent = String(state.qty);
    $("qMinus").disabled = state.qty <= 1;
    $("qPlus").disabled = state.qty >= MAX_QTY;
    $("pTotal").textContent = money(p.price_cents * state.qty, p.currency);
    const soldOut = !p.variants.some((v) => v.in_stock);
    const add = $("addBtn");
    add.disabled = soldOut;
    add.textContent = soldOut ? "Sold out" : "Add to cart";
  }
  $("qMinus").addEventListener("click", () => { state.qty = Math.max(1, state.qty - 1); renderProductState(); });
  $("qPlus").addEventListener("click", () => { state.qty = Math.min(MAX_QTY, state.qty + 1); renderProductState(); });

  function showError(id, msg) { const el = $(id); el.textContent = msg; el.hidden = false; }
  function hideError(id) { $(id).hidden = true; }
  async function busy(btn, fn) {
    if (btn.classList.contains("busy")) return;
    btn.classList.add("busy"); btn.setAttribute("aria-busy", "true");
    try { return await fn(); } finally { btn.classList.remove("busy"); btn.removeAttribute("aria-busy"); }
  }

  $("addBtn").addEventListener("click", () => busy($("addBtn"), async () => {
    const p = state.product;
    if (!state.variant) { showError("pError", `Please choose a ${p.variant_label.toLowerCase()} first.`); return; }
    hideError("pError");
    try {
      const token = await ensureCart();
      let cart;
      try { cart = await api.addItem(token, p.slug, state.variant, state.qty); }
      catch (e) {
        if (e instanceof ApiError && e.status === 404 && /cart/i.test(e.message)) {
          const fresh = await ensureCart(true);
          cart = await api.addItem(fresh, p.slug, state.variant, state.qty);
        } else throw e;
      }
      setCart(cart);
      closeSheet("productSheet");
      toast(`Added ${p.name} (${state.variant})`, { action: { label: "View cart", run: openCart } });
    } catch (e) {
      showError("pError", e.message || "Couldn’t add that to your cart.");
    }
  }));

  // ---------- cart ----------
  async function ensureCart(forceNew) {
    if (state.token && !forceNew) return state.token;
    const cart = await api.newCart();
    state.token = cart.token; store.set("kv_cart", cart.token);
    setCart(cart);
    return cart.token;
  }
  async function refreshCart() {
    if (!state.token) { setCart(null); return; }
    try { setCart(await api.cart(state.token)); }
    catch (e) {
      if (e instanceof ApiError && e.status === 404) { state.token = null; store.del("kv_cart"); setCart(null); }
      else throw e;
    }
  }
  function setCart(cart) {
    state.cart = cart;
    $("cartCount").textContent = String(cart ? cart.item_count : 0);
    renderCart();
  }
  function renderCart() {
    const cart = state.cart;
    const lines = cart ? cart.items : [];
    $("cartEmpty").hidden = lines.length > 0;
    $("checkoutBtn").disabled = lines.length === 0;
    $("cartTotal").textContent = money(cart ? cart.total_cents : 0);
    $("cartLines").replaceChildren(...lines.map((l) => {
      const p = state.products.get(l.product_slug);
      const minus = h("button", { type: "button", "aria-label": `One less ${l.name}`, text: "−", disabled: l.quantity <= 1, onclick: () => changeQty(l, l.quantity - 1) });
      const plus = h("button", { type: "button", "aria-label": `One more ${l.name}`, text: "+", disabled: l.quantity >= MAX_QTY, onclick: () => changeQty(l, l.quantity + 1) });
      return h("li", { class: "cart-line" },
        h("img", { src: (p && p.image_url) || "", alt: "", onerror: (e) => { e.target.style.visibility = "hidden"; } }),
        h("div", {}, h("div", { class: "n", text: l.name }), h("div", { class: "m", text: `${l.variant} · ${money(l.unit_price_cents)}` }),
          h("div", { class: "stepper" }, minus, h("output", { text: String(l.quantity) }), plus),
          h("button", { class: "rm", type: "button", text: "Remove", onclick: () => changeQty(l, 0) })),
        h("div", { class: "t", text: money(l.line_total_cents) }));
    }));
  }
  let cartBusy = false;
  async function changeQty(line, qty) {
    if (cartBusy) return;
    cartBusy = true; hideError("cartError");
    $("cartLines").setAttribute("aria-busy", "true");
    try { setCart(qty === 0 ? await api.removeItem(state.token, line.id) : await api.setQty(state.token, line.id, qty)); }
    catch (e) { showError("cartError", e.message); if (e.status === 404) await refreshCart().catch(() => {}); }
    finally { cartBusy = false; $("cartLines").removeAttribute("aria-busy"); }
  }
  async function openCart() {
    openSheet("cartDrawer");
    hideError("cartError");
    try { await refreshCart(); } catch (e) { showError("cartError", e.message); }
  }
  $("cartBtn").addEventListener("click", openCart);

  // ---------- checkout ----------
  const coFields = [["coName", "coNameErr", (v) => (v.trim() ? "" : "Please enter your name.")],
    ["coEmail", "coEmailErr", (v) => (EMAIL_RE.test(v.trim()) ? "" : "Please enter a valid email.")],
    ["coZip", "coZipErr", (v) => (ZIP_RE.test(v.trim()) ? "" : "3 to 12 letters or numbers.")]];
  function validate(fields) {
    let ok = true, first = null;
    for (const [id, errId, rule] of fields) {
      const msg = rule($(id).value);
      $(errId).textContent = msg;
      $(id).setAttribute("aria-invalid", String(!!msg));
      if (msg) { ok = false; first = first || $(id); }
    }
    if (first) first.focus();
    return ok;
  }
  $("checkoutBtn").addEventListener("click", () => {
    if (!state.cart || !state.cart.items.length) return;
    $("checkoutForm").hidden = false; $("coDone").hidden = true; hideError("coError");
    closeSheet("cartDrawer");
    $("checkoutDlg").showModal();
    $("coName").focus();
  });
  $("checkoutForm").addEventListener("submit", (e) => {
    e.preventDefault();
    if (!validate(coFields)) return;
    busy($("coSubmit"), async () => {
      hideError("coError");
      try {
        const order = await api.checkout(state.token, { name: $("coName").value.trim(), email: $("coEmail").value.trim(), zip_code: $("coZip").value.trim() });
        state.lastOrder = { code: order.code, email: $("coEmail").value.trim(), zip: $("coZip").value.trim() };
        $("coCode").textContent = order.code;
        $("checkoutForm").hidden = true; $("coDone").hidden = false;
        await refreshCart().catch(() => setCart(null));
      } catch (err) {
        showError("coError", err.message);
        if (err.status === 409) refreshCart().catch(() => {});
      }
    });
  });
  $("copyCode").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText($("coCode").textContent); toast("Order code copied"); }
    catch (_) { const r = document.createRange(); r.selectNodeContents($("coCode")); const s = getSelection(); s.removeAllRanges(); s.addRange(r); toast("Code selected, press Ctrl+C to copy"); }
  });
  $("coTrack").addEventListener("click", () => {
    $("checkoutDlg").close();
    openTrack(state.lastOrder);
  });

  // ---------- order tracking ----------
  const trFields = [["trCode", "trCodeErr", (v) => (/^[A-Za-z0-9]{3,20}$/.test(v.trim()) ? "" : "Enter the code from your confirmation.")],
    ["trEmail", "trEmailErr", (v) => (EMAIL_RE.test(v.trim()) ? "" : "Enter a valid email.")],
    ["trZip", "trZipErr", (v) => (ZIP_RE.test(v.trim()) ? "" : "Enter your zip code.")]];
  function openTrack(prefill) {
    $("trackForm").hidden = false; $("trResult").hidden = true; hideError("trError");
    if (prefill) { $("trCode").value = prefill.code; $("trEmail").value = prefill.email; $("trZip").value = prefill.zip; }
    $("trackDlg").showModal();
    (prefill ? $("trSubmit") : $("trCode")).focus();
  }
  $("trackBtn").addEventListener("click", () => openTrack());
  $("trackForm").addEventListener("submit", (e) => {
    e.preventDefault();
    if (!validate(trFields)) return;
    busy($("trSubmit"), async () => {
      hideError("trError");
      const who = { code: $("trCode").value.trim().toUpperCase(), email: $("trEmail").value.trim(), zip: $("trZip").value.trim() };
      try { showOrder(await api.lookup(who.code, who.email, who.zip), who); }
      catch (err) { showError("trError", err.status === 404 ? "We couldn’t find an order with those details. Check the code, email and zip." : err.message); }
    });
  });
  function showOrder(order, who) {
    state.tracked = who;
    $("trackForm").hidden = true; $("trResult").hidden = false; $("trConfirm").hidden = true; hideError("trResError");
    $("trResCode").textContent = order.code;
    const pill = $("trStatus"); pill.textContent = order.status; pill.className = `status-pill ${order.status}`;
    const reached = { pending: 1, shipped: 2, delivered: 3, refunded: 3, cancelled: 0 }[order.status] || 0;
    [...$("trTimeline").children].forEach((li, i) => li.classList.toggle("done", i < reached));
    $("trItems").replaceChildren(...order.items.map((i) => h("li", {}, h("span", { text: `${i.quantity} × ${i.name} (${i.variant})` }), h("span", { text: money(i.unit_price_cents * i.quantity) }))));
    $("trTotal").textContent = money(order.total_cents) + (order.refunded_cents ? ` (refunded ${money(order.refunded_cents)})` : "");
    $("trCancel").hidden = order.status !== "pending";
  }
  $("trAnother").addEventListener("click", () => openTrack());
  $("trCancel").addEventListener("click", () => { $("trConfirm").hidden = false; $("trYes").focus(); });
  $("trKeep").addEventListener("click", () => { $("trConfirm").hidden = true; });
  $("trYes").addEventListener("click", () => busy($("trYes"), async () => {
    const who = state.tracked;
    try { showOrder(await api.cancel(who.code, who.email, who.zip), who); toast("Order cancelled. Nothing will be charged."); }
    catch (err) { $("trConfirm").hidden = true; showError("trResError", err.message); }
  }));

  // ---------- search ----------
  let results = [], active = -1;
  function runSearch(q) {
    const box = $("qResults");
    q = q.trim().toLowerCase();
    if (!q) { box.hidden = true; $("q").setAttribute("aria-expanded", "false"); return; }
    const words = q.split(/\s+/);
    results = [...state.products.values()].filter((p) => {
      const s = state.shops.get(p.shop);
      const hay = `${p.name} ${p.color} ${p.description} ${s.name} ${s.category}`.toLowerCase();
      return words.every((w) => hay.includes(w));
    }).slice(0, 8);
    active = results.length ? 0 : -1;
    box.replaceChildren(...(results.length ? results.map((p, i) => h("li", {
      role: "option", id: `opt-${i}`, "aria-selected": String(i === active),
      onmousedown: (e) => { e.preventDefault(); choose(i); },
    }, h("img", { src: p.image_url || "", alt: "" }), h("div", {}, h("div", { class: "r-name", text: p.name }),
      h("div", { class: "r-meta", text: `${state.shops.get(p.shop).name} · ${money(p.price_cents, p.currency)}` }))))
      : [h("li", { class: "empty", text: "No matches. Try “dress”, “hoodie” or “perfume”." })]));
    box.hidden = false; $("q").setAttribute("aria-expanded", "true");
    $("q").setAttribute("aria-activedescendant", active >= 0 ? `opt-${active}` : "");
  }
  function choose(i) {
    const p = results[i];
    if (!p) return;
    $("q").value = ""; runSearch("");
    openShop(p.shop);
    setTimeout(() => openProduct(p.slug), state.mode === "3d" ? 650 : 300);
  }
  $("q").addEventListener("input", (e) => runSearch(e.target.value));
  $("q").addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!results.length) return;
      active = (active + (e.key === "ArrowDown" ? 1 : -1) + results.length) % results.length;
      [...$("qResults").children].forEach((li, i) => li.setAttribute("aria-selected", String(i === active)));
      $("q").setAttribute("aria-activedescendant", `opt-${active}`);
    } else if (e.key === "Enter") { e.preventDefault(); choose(active); }
    else if (e.key === "Escape") { $("q").value = ""; runSearch(""); }
  });
  $("q").addEventListener("blur", () => setTimeout(() => runSearch(""), 120));

  // ---------- top bar + keyboard ----------
  document.querySelectorAll("#floorSeg button").forEach((b) => b.addEventListener("click", () => {
    setFloor(Number(b.dataset.floor)); state.shop = null; markDirectory(); $("shopCard").hidden = true;
    if (state.mall) state.mall.goConcourse(state.floor);
  }));
  document.querySelectorAll("#viewSeg button").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));
  $("leaveShop").addEventListener("click", leaveShop);
  $("homeLink").addEventListener("click", (e) => { e.preventDefault(); if (state.mode === "3d") leaveShop(); else window.scrollTo({ top: 0, behavior: "smooth" }); });
  document.addEventListener("keydown", (e) => {
    if (e.target.closest("input, dialog")) return;
    if (e.key === "Escape") {
      if ($("productSheet").classList.contains("open")) closeSheet("productSheet");
      else if ($("cartDrawer").classList.contains("open")) closeSheet("cartDrawer");
      else if (state.shop && state.mode === "3d") leaveShop();
      return;
    }
    if (state.mode !== "3d" || !state.mall) return;
    const k = e.key.toLowerCase();
    if (k === "w" || k === "arrowup") { state.mall.walk(2); e.preventDefault(); }
    else if (k === "s" || k === "arrowdown") { state.mall.walk(-2); e.preventDefault(); }
    else if (k === "a" || k === "arrowleft") { state.mall.look(0.12); e.preventDefault(); }
    else if (k === "d" || k === "arrowright") { state.mall.look(-0.12); e.preventDefault(); }
  });
  document.addEventListener("visibilitychange", () => { if (state.mall && state.mode === "3d") state.mall.pause(document.hidden); });

  // ---------- 3D hooks ----------
  function onHover(hit, x, y) {
    const tip = $("tip");
    if (!hit) { tip.hidden = true; return; }
    tip.replaceChildren();
    if (hit.kind === "product") tip.append(h("b", { text: hit.info.name }), h("span", { text: money(hit.info.price_cents, hit.info.currency) }));
    else tip.append(h("b", { text: `Enter ${state.shops.get(hit.slug).name}` }));
    tip.style.left = `${x}px`; tip.style.top = `${y}px`; tip.hidden = false;
  }

  // ---------- boot ----------
  async function boot() {
    loadProgress(0.05, "Opening the doors…");
    let catalog;
    try { catalog = await api.catalog(); }
    catch (e) { loadFail(e.message || "The mall couldn’t open. Please try again."); return; }
    state.catalog = catalog;
    state.shops = new Map(catalog.map((s) => [s.slug, s]));
    state.products = new Map(catalog.flatMap((s) => s.products.map((p) => [p.slug, { ...p, shop: s.slug }])));
    renderDirectory();
    renderGrid();
    refreshCart().catch(() => {});

    const params = new URLSearchParams(location.search);
    const wanted = params.get("mode") || store.get("kv_mode") || "3d";
    const small = window.matchMedia("(max-width: 700px)").matches;
    const canGL = Mall3D.webglAvailable() && window.THREE;
    if (wanted !== "2d" && canGL && !small) {
      loadProgress(0.15, "Lighting up the shops…");
      try {
        state.mall = await Mall3D.create({
          canvas: $("scene"), catalog,
          lowGraphics: params.get("low") === "1" || (navigator.hardwareConcurrency || 8) <= 2,
          onProgress: (f) => loadProgress(0.15 + f * 0.85, "Hanging the photos…"),
          onPickProduct: openProduct, onEnterShop: openShop, onHover,
          onContextLost: () => { state.mall = null; setMode("2d", true); toast("3D view stopped, switched to the grid.", { bad: true }); },
        });
      } catch (e) {
        state.mall = null;
        console.error(e);
      }
    }
    setMode(state.mall ? (wanted === "2d" ? "2d" : "3d") : "2d", true);
    loadProgress(1, "Welcome to Kavrelle");
    $("loader").classList.add("done");

    const hash = location.hash.slice(1);
    if (hash.startsWith("shop-")) openShop(hash.slice(5), { noHash: true });
    else if (hash.startsWith("product-") && state.products.has(hash.slice(8))) { const p = state.products.get(hash.slice(8)); openShop(p.shop, { noHash: true }); openProduct(p.slug); }
    document.body.dataset.ready = "true";
  }

  window.addEventListener("error", (e) => { if (e.message && !/ResizeObserver/.test(e.message)) toast("Something went wrong on this page. Try again.", { bad: true }); });
  window.addEventListener("unhandledrejection", () => toast("Something went wrong. Please try again.", { bad: true }));

  // test hooks (no secrets here: only navigation helpers)
  window.KV.app = { openShop, openProduct, openCart, leaveShop, state: () => ({ mode: state.mode, shop: state.shop, floor: state.floor, mall: !!state.mall }) };
  boot();
})();
