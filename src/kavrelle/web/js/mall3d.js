/* Kavrelle 3D mall (Three.js r128).
   Builds a two-floor mall from the catalogue: a skylit concourse, storefronts with signs,
   and shop interiors whose walls carry lit frames with the real product photos.
   Renders on demand (only while something moves) to stay light on laptops. */
(function () {
  "use strict";
  const T = window.THREE;

  const FLOOR_H = 5.7;
  const SLOT_Z = [-4, -18, -32];
  const SHOP_COLORS = ["#2E8C83", "#E8A93A", "#E2552D", "#6B7F3A", "#5A6FC8", "#C2477A"];
  const PALETTE = ["#C8202B", "#3B3E45", "#F05A1A", "#B8D4EE", "#A8562A", "#4FD65A", "#2E4FB8", "#2F6B45", "#EDEBE6", "#E3C23A", "#8E5BB8", "#16161A"];
  const DISPLAY_FONT = '"Inter Tight", "Inter", Arial, sans-serif';

  function shopColor(shop) {
    return SHOP_COLORS[(shop.position - 1 + (shop.floor - 1) * 2) % SHOP_COLORS.length];
  }
  function slot(shop) {
    const side = shop.position <= 3 ? -1 : 1;
    return { side, z: SLOT_Z[(shop.position - 1) % 3], y: (shop.floor - 1) * FLOOR_H };
  }

  function create(opts) {
    const { canvas, catalog, onProgress, onPickProduct, onEnterShop, onHover, onContextLost } = opts;
    let low = !!opts.lowGraphics;

    // ---------- renderer ----------
    const renderer = new T.WebGLRenderer({ canvas, antialias: !low, powerPreference: "high-performance" });
    renderer.outputEncoding = T.sRGBEncoding;
    renderer.toneMapping = T.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.02;
    const setPixelRatio = () => renderer.setPixelRatio(low ? 1 : Math.min(window.devicePixelRatio || 1, 1.75));
    setPixelRatio();

    const scene = new T.Scene();
    scene.background = new T.Color("#F7E9D7");
    scene.fog = new T.Fog("#F7E9D7", 34, 80);
    const camera = new T.PerspectiveCamera(55, 1, 0.1, 200);
    scene.add(new T.HemisphereLight("#ffffff", "#c9a98a", 0.95));
    const sun = new T.DirectionalLight("#FFF1DC", 0.7);
    sun.position.set(4, 20, 8);
    scene.add(sun);

    // ---------- helpers ----------
    const geoCache = {};
    const matCache = {};
    const once = (k, f) => geoCache[k] || (geoCache[k] = f());
    const M = (c, r = 0.6, m = 0) => {
      const k = `${c}|${r}|${m}`;
      return matCache[k] || (matCache[k] = new T.MeshStandardMaterial({ color: c, roughness: r, metalness: m }));
    };
    const box = (w, h, d, mat, x, y, z, parent) => {
      const b = new T.Mesh(once(`b${w}|${h}|${d}`, () => new T.BoxGeometry(w, h, d)), mat);
      b.position.set(x, y, z);
      (parent || scene).add(b);
      return b;
    };
    const canvasTex = (w, h, draw) => {
      const c = document.createElement("canvas");
      c.width = w; c.height = h;
      draw(c.getContext("2d"), w, h);
      const t = new T.CanvasTexture(c);
      t.encoding = T.sRGBEncoding;
      t.anisotropy = Math.min(8, renderer.capabilities.getMaxAnisotropy());
      return t;
    };
    const rnd = (() => { let s = 11; return () => (s = (s * 9301 + 49297) % 233280) / 233280; })();

    function signTexture(text, sub, bg, ink, w = 1024, h = 256) {
      return canvasTex(w, h, (g, W, H) => {
        if (bg) { g.fillStyle = bg; g.fillRect(0, 0, W, H); }
        g.fillStyle = ink; g.textAlign = "center"; g.textBaseline = "middle";
        let size = Math.round(H * 0.42);
        g.font = `900 ${size}px ${DISPLAY_FONT}`;
        while (g.measureText(text).width > W * 0.9 && size > 20) { size -= 4; g.font = `900 ${size}px ${DISPLAY_FONT}`; }
        g.fillText(text, W / 2, sub ? H * 0.42 : H / 2);
        if (sub) { g.globalAlpha = 0.85; g.font = `600 ${Math.round(H * 0.13)}px Inter, Arial, sans-serif`; g.fillText(sub.toUpperCase(), W / 2, H * 0.8); }
      });
    }
    const basic = (map) => new T.MeshBasicMaterial({ map, toneMapped: false });

    // ---------- concourse ----------
    const floorTex = canvasTex(512, 512, (g, w, h) => {
      g.fillStyle = "#D9A47E"; g.fillRect(0, 0, w, h);
      g.fillStyle = "#CF9670"; g.fillRect(0, 0, w / 2, h / 2); g.fillRect(w / 2, h / 2, w / 2, h / 2);
      g.strokeStyle = "rgba(90,50,20,.18)"; g.lineWidth = 4; g.strokeRect(0, 0, w, h);
      g.beginPath(); g.moveTo(w / 2, 0); g.lineTo(w / 2, h); g.moveTo(0, h / 2); g.lineTo(w, h / 2); g.stroke();
    });
    floorTex.wrapS = floorTex.wrapT = T.RepeatWrapping;
    floorTex.repeat.set(6, 30);
    const ground = new T.Mesh(new T.PlaneGeometry(12, 60), new T.MeshStandardMaterial({ map: floorTex, roughness: 0.4 }));
    ground.rotation.x = -Math.PI / 2; ground.position.set(0, 0, -14);
    scene.add(ground);

    const WALL = M("#FFF4E6", 0.9), TRIM = M("#1F1A16", 0.4), CEIL = M("#FFF8EE", 1);
    box(12, 0.4, 60, CEIL, 0, FLOOR_H * 2 + 0.2, -14);
    const sky = new T.Mesh(new T.PlaneGeometry(5, 56), new T.MeshBasicMaterial({ color: "#FFFFFF" }));
    sky.rotation.x = Math.PI / 2; sky.position.set(0, FLOOR_H * 2 - 0.02, -14);
    scene.add(sky);
    for (let z = 12; z > -42; z -= 3) box(5.2, 0.12, 0.14, TRIM, 0, FLOOR_H * 2 - 0.1, z);
    const glass = new T.MeshStandardMaterial({ color: "#dfefff", transparent: true, opacity: 0.25, roughness: 0.05 });
    for (const s of [-1, 1]) {
      box(2.6, 0.3, 60, WALL, s * 4.7, FLOOR_H - 0.15, -14);
      box(0.06, 1.1, 60, glass, s * 3.42, FLOOR_H + 0.55, -14);
      box(0.08, 0.08, 60, TRIM, s * 3.42, FLOOR_H + 1.12, -14);
      for (let z = 8; z > -42; z -= 7) box(0.5, FLOOR_H * 2, 0.5, M("#FFF4E6", 0.7), s * 3.5, FLOOR_H, z);
    }
    box(12, FLOOR_H * 2, 0.4, WALL, 0, FLOOR_H, -40);

    // escalator between floors
    const steps = M("#8E9196", 0.35, 0.7);
    for (let i = 0; i < 19; i++) box(1.6, 0.2, 0.32, steps, 0, 0.1 + i * 0.3, -24 - i * 0.3);
    for (const s of [-1, 1]) {
      const rail = box(0.12, 1.0, 8.2, TRIM, s * 0.9, FLOOR_H / 2 + 0.6, -26.85);
      rail.rotation.x = Math.PI / 4;
    }

    // planters and benches
    function plant(pot, scale) {
      const g = new T.Group();
      const p = new T.Mesh(once("pot", () => new T.CylinderGeometry(0.18, 0.14, 0.3, 16)), M(pot, 0.8));
      p.position.y = 0.15; g.add(p);
      const lg = once("leaf", () => new T.IcosahedronGeometry(1, 1)), lm = M("#3E7D4E", 0.8);
      [[0, 0.55, 0, 0.22], [0.12, 0.48, 0.05, 0.16], [-0.12, 0.5, -0.04, 0.17], [0.04, 0.68, -0.06, 0.14], [-0.06, 0.42, 0.1, 0.14]]
        .forEach(([x, y, z, r]) => { const l = new T.Mesh(lg, lm); l.scale.setScalar(r); l.position.set(x, y, z); g.add(l); });
      g.scale.setScalar(scale);
      return g;
    }
    for (const z of [4, -10]) {
      for (const sx of [-1.9, 1.9]) { const p = plant("#E2552D", 1.8); p.position.set(sx, 0, z); scene.add(p); }
      box(0.7, 0.45, 2.4, M("#C8A87C", 0.6), 0, 0.35, z - 1);
    }

    // ---------- product frames ----------
    const productFrames = []; // meshes with userData.product
    const textureJobs = [];
    const manager = new T.LoadingManager();
    const loader = new T.TextureLoader(manager);

    function placeholderTexture(p) {
      return canvasTex(512, 640, (g, w, h) => {
        const gr = g.createLinearGradient(0, 0, w, h);
        gr.addColorStop(0, "#FBE2D7"); gr.addColorStop(1, "#F2C8A8");
        g.fillStyle = gr; g.fillRect(0, 0, w, h);
        g.fillStyle = "#1F1A16"; g.textAlign = "center";
        g.font = `900 64px ${DISPLAY_FONT}`;
        g.fillText(p.name.split(" ").map((x) => x[0]).slice(0, 2).join(""), w / 2, h / 2);
      });
    }

    function priceTag(p) {
      return canvasTex(512, 128, (g, w, h) => {
        g.fillStyle = "#FFFBF5"; g.fillRect(0, 0, w, h);
        g.fillStyle = "#1F1A16"; g.textBaseline = "middle";
        let size = 34; g.font = `800 ${size}px Inter, Arial, sans-serif`;
        let name = p.name;
        while (g.measureText(name).width > w - 36 && name.length > 4) name = name.slice(0, -2);
        if (name !== p.name) name = name.trim() + "…";
        g.fillText(name, 18, h * 0.34);
        g.fillStyle = "#E2552D"; g.font = `900 40px ${DISPLAY_FONT}`;
        g.fillText(new Intl.NumberFormat("en-US", { style: "currency", currency: p.currency || "USD" }).format(p.price_cents / 100), 18, h * 0.74);
      });
    }

    function productFrame(p, shop) {
      const g = new T.Group();
      const frame = new T.Mesh(once("frame", () => new T.BoxGeometry(1.5, 1.9, 0.08)), M("#2A211B", 0.45));
      g.add(frame);
      const photoMat = new T.MeshBasicMaterial({ color: "#ffffff", toneMapped: false });
      const photo = new T.Mesh(once("photo", () => new T.PlaneGeometry(1.36, 1.7)), photoMat);
      photo.position.z = 0.045; g.add(photo);
      const tag = new T.Mesh(once("tag", () => new T.PlaneGeometry(1.5, 0.375)), basic(priceTag(p)));
      tag.position.set(0, -1.2, 0.03); g.add(tag);
      const light = new T.Mesh(once("lightbar", () => new T.BoxGeometry(1.2, 0.05, 0.12)), new T.MeshBasicMaterial({ color: "#FFF3DF" }));
      light.position.set(0, 1.08, 0.1); g.add(light);
      const info = { slug: p.slug, name: p.name, price_cents: p.price_cents, currency: p.currency, shop: shop.slug };
      for (const m of [frame, photo, tag]) { m.userData.product = info; productFrames.push(m); }
      g.userData.hover = 0;
      g.userData.base = null;
      textureJobs.push(() => {
        if (!p.image_url) { photoMat.map = placeholderTexture(p); photoMat.needsUpdate = true; return; }
        loader.load(p.image_url, (tex) => {
          tex.encoding = T.sRGBEncoding;
          tex.anisotropy = Math.min(8, renderer.capabilities.getMaxAnisotropy());
          photoMat.map = tex; photoMat.needsUpdate = true; invalidate();
        }, undefined, () => { photoMat.map = placeholderTexture(p); photoMat.needsUpdate = true; invalidate(); });
      });
      return g;
    }

    // ---------- shop dressing by category ----------
    function garment(color, long) {
      const g = new T.Group();
      const geo = once(long ? "dress" : "jk", () => {
        const pts = long
          ? [[-0.12, 0], [0.12, 0], [0.32, -0.1], [0.24, -0.5], [0.5, -1.45], [-0.5, -1.45], [-0.24, -0.5], [-0.32, -0.1]]
          : [[-0.15, 0], [0.15, 0], [0.48, -0.08], [0.64, -0.78], [0.5, -0.82], [0.4, -0.36], [0.38, -1.02], [-0.38, -1.02], [-0.4, -0.36], [-0.5, -0.82], [-0.64, -0.78], [-0.48, -0.08]];
        const s = new T.Shape(); s.moveTo(pts[0][0], pts[0][1]); pts.slice(1).forEach((q) => s.lineTo(q[0], q[1])); s.closePath();
        const e = new T.ExtrudeGeometry(s, { depth: 0.1, bevelEnabled: true, bevelThickness: 0.03, bevelSize: 0.03, bevelSegments: 2 });
        e.center(); return e;
      });
      const m = new T.Mesh(geo, M(color, 0.85)); m.position.y = long ? -0.75 : -0.55; g.add(m);
      const hk = new T.Mesh(once("hook", () => new T.TorusGeometry(0.05, 0.008, 6, 12, Math.PI * 1.4)), M("#9DA0A6", 0.3, 0.8));
      hk.position.y = 0.06; g.add(hk);
      return g;
    }
    function rack(parent, x, z0, z1, y, n, colors, long) {
      const metal = M("#2A2A2E", 0.3, 0.8);
      const r = new T.Mesh(once(`rail${Math.abs(z1 - z0)}`, () => new T.CylinderGeometry(0.025, 0.025, Math.abs(z1 - z0), 8)), metal);
      r.rotation.x = Math.PI / 2; r.position.set(x, y, (z0 + z1) / 2); parent.add(r);
      for (const zz of [z0, z1]) box(0.04, y, 0.04, metal, x, y / 2, zz, parent);
      for (let k = 0; k < n; k++) {
        const gm = garment(colors[k % colors.length], long);
        gm.position.set(x, y - 0.02, z0 + ((k + 0.5) * (z1 - z0)) / n);
        gm.rotation.y = -Math.PI / 2 + (rnd() - 0.5) * 0.15; gm.scale.setScalar(0.75); parent.add(gm);
      }
    }
    function mannequin(parent, x, z, color, long) {
      const skin = M("#EDE6DA", 0.5), metal = M("#888", 0.3, 0.8);
      box(0.05, 1.0, 0.05, metal, x, 0.5, z, parent); box(0.5, 0.03, 0.5, metal, x, 0.02, z, parent);
      const head = new T.Mesh(once("head", () => new T.SphereGeometry(0.13, 16, 12)), skin); head.position.set(x, 1.95, z); parent.add(head);
      const gm = garment(color, long); gm.position.set(x, 1.82, z); gm.rotation.y = -Math.PI / 2; parent.add(gm);
    }
    function table(parent, x, z, w, d, h, color) {
      box(w, 0.06, d, M(color || "#D8C3A5", 0.6), x, h, z, parent);
      const leg = M("#2A2A2E", 0.4, 0.6);
      for (const sx of [-1, 1]) for (const sz of [-1, 1]) box(0.05, h, 0.05, leg, x + sx * (w / 2 - 0.1), h / 2, z + sz * (d / 2 - 0.1), parent);
      return h + 0.03;
    }
    function smallBoxes(parent, x, z, top, n, colors) {
      for (let k = 0; k < n; k++) {
        const s = 0.18 + rnd() * 0.18;
        const b = box(s * 1.3, s, s, M(colors[k % colors.length], 0.5), x - 0.5 + (k % 3) * 0.5, top + s / 2, z - 0.35 + Math.floor(k / 3) * 0.7, parent);
        b.rotation.y = rnd();
      }
    }
    function bottle(parent, x, y, z, c, h) {
      const b = new T.Mesh(once("bottle", () => new T.CylinderGeometry(0.06, 0.06, 1, 12)), M(c, 0.25, 0.1));
      b.scale.y = h; b.position.set(x, y + h / 2, z); parent.add(b);
      const cap = new T.Mesh(once("cap", () => new T.CylinderGeometry(0.035, 0.035, 0.06, 10)), M("#C9A35A", 0.3, 0.7));
      cap.position.set(x, y + h + 0.03, z); parent.add(cap);
    }

    function dress(parent, shop, color) {
      const cat = (shop.category || "").toLowerCase();
      if (cat.includes("men") && !cat.includes("women")) {
        rack(parent, 5.2, -5.2, -1.6, 1.7, 8, ["#C8202B", "#16161A", "#B8D4EE", "#F05A1A"]);
        rack(parent, 5.2, 1.6, 5.2, 1.7, 8, ["#2F6B45", "#EDEBE6", "#2E4FB8", "#A8562A"]);
        const top = table(parent, 3.0, 0, 1.6, 2.4, 0.75, "#D8C3A5");
        for (let k = 0; k < 12; k++) box(0.42, 0.09, 0.5, M(PALETTE[k % PALETTE.length], 0.9), 2.5 + (k % 3) * 0.48, top + 0.05 + Math.floor(k / 6) * 0.1, -0.8 + (Math.floor(k / 3) % 2) * 1.1, parent);
        mannequin(parent, 1.2, -4.6, "#3B3E45"); mannequin(parent, 1.2, 4.6, "#C8202B");
      } else if (cat.includes("women")) {
        rack(parent, 5.2, -5.2, -1.6, 2.0, 6, ["#16161A", "#E8D9C0", "#C9A0DC", "#C2477A"], true);
        rack(parent, 5.2, 1.6, 5.2, 2.0, 6, ["#F2C1C8", "#2E4FB8", "#EDEBE6", "#E3C23A"], true);
        const top = table(parent, 3.0, 0, 1.4, 2.2, 0.9, "#F7F3EE");
        smallBoxes(parent, 3.0, 0, top, 6, ["#A8562A", "#F2C1C8", "#2E8C83", "#C2477A"]);
        mannequin(parent, 1.2, -4.6, "#16161A", true); mannequin(parent, 1.2, 4.6, "#C9A0DC", true);
      } else if (cat.includes("sneaker") || cat.includes("sport")) {
        for (const zz of [-3, 3]) {
          const t = new T.Mesh(once("plinth", () => new T.CylinderGeometry(0.9, 0.9, 0.6, 32)), M(color, 0.5));
          t.position.set(4, 0.3, zz); parent.add(t);
          smallBoxes(parent, 4.5, zz, 0.6, 3, ["#FFFFFF", "#16161A", "#E2552D"]);
        }
        box(1.6, 0.5, 2.2, M("#7A6A5A", 0.9), 2, 0.25, 0, parent);
      } else if (cat.includes("electronic")) {
        for (const zz of [-3, 3]) {
          const top = table(parent, 4.2, zz, 1.4, 3.6, 0.9, "#F2EFEA");
          for (let k = 0; k < 6; k++) box(0.3, 0.02, 0.6, M(k % 2 ? "#2B2B30" : "#D9D4C7", 0.3, 0.5), 3.9 + (k % 2) * 0.55, top + 0.01, zz - 1.3 + Math.floor(k / 2) * 1.1, parent);
        }
      } else if (cat.includes("beauty") || cat.includes("health")) {
        for (const zz of [-3.2, 3.2]) {
          box(2.4, 0.95, 1.2, M("#F7F3EE", 0.3), 4.2, 0.475, zz, parent);
          for (let k = 0; k < 8; k++) bottle(parent, 3.3 + (k % 4) * 0.55, 0.95, zz - 0.25 + Math.floor(k / 4) * 0.5, ["#F2C1C8", "#C9A0DC", "#E8D9C0", "#1E1E1E"][k % 4], 0.2 + (k % 3) * 0.06);
        }
      } else if (cat.includes("home")) {
        const sofa = M("#C9B9A4", 0.95);
        box(1.1, 0.45, 3.2, sofa, 6.5, 0.3, 0, parent); box(0.3, 0.85, 3.2, sofa, 7.05, 0.55, 0, parent);
        const rug = new T.Mesh(new T.PlaneGeometry(3, 4), M("#2E8C83", 1)); rug.rotation.x = -Math.PI / 2; rug.position.set(4.6, 0.02, 0); parent.add(rug);
        box(1.0, 0.4, 1.6, M("#8A6A48", 0.6), 4.5, 0.2, 0, parent);
        for (const [x, z] of [[1.6, -4.6], [1.6, 4.6]]) { const p = plant("#C66A4A", 2.2); p.position.set(x, 0, z); parent.add(p); }
      } else if (cat.includes("jewel")) {
        for (const zz of [-3, 0, 3]) {
          box(1.2, 1.0, 1.2, M("#F7F3EE", 0.3), 4.2, 0.5, zz, parent);
          box(1.1, 0.4, 1.1, new T.MeshStandardMaterial({ color: "#dfefff", transparent: true, opacity: 0.3, roughness: 0.05 }), 4.2, 1.2, zz, parent);
        }
      } else if (cat.includes("food")) {
        box(1.2, 1.1, 10, M(color, 0.6), 8.6, 0.55, 0, parent);
        for (const [x, z] of [[3, -3.5], [3, 0], [3, 3.5], [5.6, -2], [5.6, 2]]) {
          const top = table(parent, x, z, 1.0, 1.0, 0.75, "#FFFFFF");
          for (const s of [-1, 1]) box(0.4, 0.45, 0.4, M("#E8A93A", 0.6), x + s * 0.75, 0.225, z, parent);
          box(0.2, 0.04, 0.2, M("#E2552D", 0.5), x, top + 0.02, z, parent);
        }
      } else {
        for (const zz of [-3, 3]) { const top = table(parent, 4.2, zz, 2.2, 2.2, 0.7, "#D8C3A5"); smallBoxes(parent, 4.2, zz, top, 6, PALETTE); }
      }
    }

    const woodTex = canvasTex(512, 512, (g, w, h) => {
      g.fillStyle = "#E9D3B4"; g.fillRect(0, 0, w, h);
      for (let i = 0; i < 8; i++) {
        g.fillStyle = i % 2 ? "#E2C9A6" : "#EDD9BD"; g.fillRect(0, i * 64, w, 62);
        g.fillStyle = "rgba(120,80,40,.12)"; g.fillRect(((i * 197) % w), i * 64, 3, 64);
      }
    });
    woodTex.wrapS = woodTex.wrapT = T.RepeatWrapping; woodTex.repeat.set(2, 3);
    const woodFloor = new T.MeshStandardMaterial({ map: woodTex, roughness: 0.55 });

    // ---------- shops ----------
    const shops = new Map(); // slug -> {group, shop, frames: [Group]}
    const storefrontMeshes = [];
    const glassM = new T.MeshStandardMaterial({ color: "#F5E3CF", transparent: true, opacity: 0.2, roughness: 0.05, metalness: 0.1 });

    for (const shop of catalog) {
      const { side, z, y } = slot(shop);
      const color = shopColor(shop);
      const g = new T.Group();
      g.position.set(side * 6, y, z);
      g.rotation.y = side > 0 ? 0 : Math.PI;
      scene.add(g);
      const wallM = M(color, 0.85), inner = M("#FFF6EA", 0.9);
      const tint = "#" + new T.Color(color).lerp(new T.Color("#FFF6EA"), 0.72).getHexString();
      const sf = new T.Mesh(once("shopfloor", () => new T.PlaneGeometry(10, 12)), woodFloor);
      sf.rotation.x = -Math.PI / 2; sf.position.set(5, 0.02, 0); g.add(sf);
      box(10, 0.2, 12.4, CEIL, 5, FLOOR_H - 0.25, 0, g);
      box(0.3, FLOOR_H, 12.4, M(tint, 0.9), 10, FLOOR_H / 2, 0, g);
      box(0.1, 0.25, 12.2, M(color, 0.6), 9.84, 0.125, 0, g); // skirting
      box(0.06, 0.16, 12.2, M(color, 0.6), 9.86, 4.35, 0, g); // picture rail
      for (const s of [-1, 1]) box(10, FLOOR_H, 0.3, inner, 5, FLOOR_H / 2, s * 6.2, g);
      // storefront
      const front = [];
      for (const s of [-1, 1]) front.push(box(0.5, FLOOR_H - 0.3, 0.6, wallM, 0, (FLOOR_H - 0.3) / 2, s * 6.1, g));
      front.push(box(0.6, 1.25, 12.8, wallM, 0, FLOOR_H - 0.9, 0, g));
      box(0.62, 0.08, 12.8, TRIM, 0, FLOOR_H - 1.55, 0, g);
      for (const s of [-1, 1]) {
        front.push(box(0.04, FLOOR_H - 1.6, 3.6, glassM, 0.05, (FLOOR_H - 1.6) / 2, s * 4.0, g));
        box(0.08, FLOOR_H - 1.6, 0.06, TRIM, 0.05, (FLOOR_H - 1.6) / 2, s * 2.2, g);
      }
      const sign = new T.Mesh(once("fascia", () => new T.PlaneGeometry(6.4, 1.15)), basic(signTexture(shop.name.toUpperCase(), shop.category, color, "#FFFFFF")));
      sign.rotation.y = -Math.PI / 2; sign.position.set(-0.32, FLOOR_H - 0.9, 0); g.add(sign); front.push(sign);
      const bt = signTexture(shop.name, null, color, "#FFFFFF", 512, 192);
      const blade = new T.Mesh(once("blade", () => new T.BoxGeometry(0.06, 0.9, 2.4)), [basic(bt), basic(bt), TRIM, TRIM, TRIM, TRIM]);
      blade.position.set(-1.6, FLOOR_H - 1.8, 6.0); blade.rotation.y = Math.PI / 2; g.add(blade); front.push(blade);
      for (const m of front) { m.userData.shop = shop.slug; storefrontMeshes.push(m); }
      // lights
      const pl = new T.PointLight("#FFF1DC", 0.75, 16, 2); pl.position.set(5, FLOOR_H - 1.1, 0); g.add(pl);
      for (let x = 2; x <= 8; x += 3) for (const zz of [-3, 0, 3]) box(0.8, 0.03, 0.8, new T.MeshBasicMaterial({ color: "#FFF8EC" }), x, FLOOR_H - 0.37, zz, g);
      dress(g, shop, color);
      // product frames: back wall first, then side walls
      const frames = [];
      const prods = shop.products;
      const nBack = Math.min(prods.length, 5);
      prods.forEach((p, i) => {
        const f = productFrame(p, shop);
        if (i < nBack) {
          f.position.set(9.8, 3.0, (i - (nBack - 1) / 2) * 2.2);
          f.rotation.y = -Math.PI / 2;
        } else {
          const j = i - nBack, left = j % 2 === 0, k = Math.floor(j / 2);
          f.position.set(3.0 + k * 2.3, 2.7, left ? -6.0 : 6.0);
          f.rotation.y = left ? 0 : Math.PI;
        }
        f.scale.setScalar(1.12);
        f.userData.base = f.position.clone();
        f.userData.out = new T.Vector3(...(i < nBack ? [-1, 0, 0] : [0, 0, f.position.z < 0 ? 1 : -1]));
        g.add(f); frames.push(f);
      });
      if (!prods.length) {
        const t = new T.Mesh(new T.PlaneGeometry(6, 1.5), basic(signTexture("Opening soon", "Come back for more", null, "#1F1A16", 1024, 256)));
        t.rotation.y = -Math.PI / 2; t.position.set(9.8, 3.2, 0); g.add(t);
      }
      shops.set(shop.slug, { group: g, shop, frames });
    }

    // end wall brand sign (both floors)
    for (const y of [3.4, FLOOR_H + 3.6]) {
      const s = new T.Mesh(new T.PlaneGeometry(7, 1.75), basic(signTexture("KAVRELLE", "Fashion · Beauty · Tech · Home · Food", "#E2552D", "#FFFFFF")));
      s.position.set(0, y, -39.78); scene.add(s);
    }

    // ---------- camera ----------
    const cam = { mode: "concourse", floor: 1, shop: null, z: 12, yaw: 0, pitch: 0 };
    const target = { pos: new T.Vector3(0, 2.1, 12), look: new T.Vector3(0, 3.2, -30) };
    const look = target.look.clone();
    camera.position.copy(target.pos);

    function updateTarget() {
      if (cam.mode === "concourse") {
        const y = (cam.floor - 1) * FLOOR_H;
        target.pos.set(0, y + 2.1, cam.z);
        const dir = new T.Vector3(Math.sin(cam.yaw), Math.tan(cam.pitch) + 0.02, -Math.cos(cam.yaw));
        target.look.copy(target.pos).addScaledVector(dir, 20);
      } else {
        const { group } = shops.get(cam.shop);
        target.pos.copy(group.localToWorld(new T.Vector3(0.6, 2.2, 0)));
        const lookLocal = new T.Vector3(9.8, 2.6 + Math.tan(cam.pitch) * 9, Math.tan(cam.yaw) * 9);
        target.look.copy(group.localToWorld(lookLocal));
      }
      invalidate();
    }

    // ---------- interaction ----------
    const ray = new T.Raycaster();
    const ndc = new T.Vector2();
    let hovered = null; // frame group
    let drag = null;

    function pick(clientX, clientY) {
      const r = canvas.getBoundingClientRect();
      ndc.set(((clientX - r.left) / r.width) * 2 - 1, -((clientY - r.top) / r.height) * 2 + 1);
      ray.setFromCamera(ndc, camera);
      const candidates = cam.mode === "shop" ? shops.get(cam.shop).frames.flatMap((f) => f.children) : storefrontMeshes;
      const hit = ray.intersectObjects(candidates, false).find((h) => h.distance < 40);
      if (!hit) return null;
      if (hit.object.userData.product) return { kind: "product", info: hit.object.userData.product, frame: hit.object.parent };
      if (hit.object.userData.shop) return { kind: "shop", slug: hit.object.userData.shop };
      return null;
    }
    function setHover(h, x, y) {
      const frame = h && h.kind === "product" ? h.frame : null;
      if (hovered !== frame) { hovered = frame; invalidate(); }
      canvas.classList.toggle("pointing", !!h);
      if (onHover) onHover(h, x, y);
    }
    canvas.addEventListener("pointerdown", (e) => {
      drag = { x: e.clientX, y: e.clientY, moved: 0, yaw: cam.yaw, pitch: cam.pitch };
      canvas.setPointerCapture(e.pointerId);
    });
    canvas.addEventListener("pointermove", (e) => {
      if (drag) {
        const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
        drag.moved = Math.max(drag.moved, Math.abs(dx) + Math.abs(dy));
        if (drag.moved > 4) {
          canvas.classList.add("dragging");
          const lim = cam.mode === "shop" ? 0.62 : 0.9;
          cam.yaw = Math.max(-lim, Math.min(lim, drag.yaw - dx * 0.004));
          cam.pitch = Math.max(-0.35, Math.min(0.3, drag.pitch + dy * 0.003));
          updateTarget(); setHover(null);
        }
        return;
      }
      setHover(pick(e.clientX, e.clientY), e.clientX, e.clientY);
    });
    const endDrag = (e) => {
      if (!drag) return;
      const wasClick = drag.moved <= 4;
      drag = null; canvas.classList.remove("dragging");
      if (!wasClick) return;
      const h = pick(e.clientX, e.clientY);
      if (!h) return;
      if (h.kind === "product" && onPickProduct) onPickProduct(h.info.slug);
      if (h.kind === "shop" && onEnterShop) onEnterShop(h.slug);
    };
    canvas.addEventListener("pointerup", endDrag);
    canvas.addEventListener("pointercancel", () => { drag = null; canvas.classList.remove("dragging"); });
    canvas.addEventListener("pointerleave", () => setHover(null));
    canvas.addEventListener("wheel", (e) => { e.preventDefault(); walk(e.deltaY > 0 ? -2 : 2); }, { passive: false });

    function walk(dz) {
      if (cam.mode !== "concourse") return;
      cam.z = Math.max(-30, Math.min(14, cam.z - dz));
      updateTarget();
    }

    canvas.addEventListener("webglcontextlost", (e) => { e.preventDefault(); running = false; if (onContextLost) onContextLost(); });

    // ---------- render loop (on demand) ----------
    let dirty = true, running = true, raf = 0;
    function invalidate() { dirty = true; }
    const clock = new T.Clock();
    function frame() {
      raf = requestAnimationFrame(frame);
      if (!running) return;
      const dt = Math.min(clock.getDelta(), 0.05);
      const k = 1 - Math.pow(0.002, dt);
      let moving = false;
      if (camera.position.distanceToSquared(target.pos) > 1e-5 || look.distanceToSquared(target.look) > 1e-5) {
        camera.position.lerp(target.pos, k); look.lerp(target.look, k); moving = true;
      }
      camera.lookAt(look);
      for (const { frames } of shops.values()) {
        for (const f of frames) {
          const want = f === hovered ? 1 : 0;
          if (Math.abs(f.userData.hover - want) > 0.002) {
            f.userData.hover += (want - f.userData.hover) * Math.min(1, dt * 10);
            f.position.copy(f.userData.base).addScaledVector(f.userData.out, f.userData.hover * 0.18);
            f.scale.setScalar(1.12 * (1 + f.userData.hover * 0.05));
            moving = true;
          }
        }
      }
      if (moving || dirty) { renderer.render(scene, camera); dirty = false; }
    }

    function resize() {
      const w = canvas.clientWidth || window.innerWidth, h = canvas.clientHeight || window.innerHeight;
      renderer.setSize(w, h, false);
      camera.aspect = w / h; camera.updateProjectionMatrix(); invalidate();
    }
    window.addEventListener("resize", resize);
    resize();

    // ---------- public controller ----------
    const controller = {
      goConcourse(floor) {
        cam.mode = "concourse"; cam.shop = null; cam.yaw = 0; cam.pitch = 0;
        if (floor) cam.floor = floor;
        updateTarget();
      },
      goShop(slug) {
        if (!shops.has(slug)) return false;
        const { shop } = shops.get(slug);
        cam.mode = "shop"; cam.shop = slug; cam.floor = shop.floor; cam.yaw = 0; cam.pitch = 0;
        updateTarget(); return true;
      },
      walk,
      look(dyaw) { cam.yaw = Math.max(-0.9, Math.min(0.9, cam.yaw + dyaw)); updateTarget(); },
      state() { return { mode: cam.mode, floor: cam.floor, shop: cam.shop }; },
      setLow(v) { low = !!v; setPixelRatio(); resize(); },
      pause(v) { running = !v; if (!v) { clock.getDelta(); invalidate(); } },
      productCount: productFrames.length / 3,
      dispose() {
        cancelAnimationFrame(raf); running = false;
        window.removeEventListener("resize", resize);
        renderer.dispose();
      },
    };
    raf = requestAnimationFrame(frame);
    updateTarget();

    // ---------- load photos, report progress ----------
    return new Promise((resolve) => {
      let done = false;
      const finish = () => { if (done) return; done = true; invalidate(); resolve(controller); };
      manager.onProgress = (_url, loaded, total) => { if (onProgress) onProgress(loaded / total); };
      manager.onLoad = finish;
      manager.onError = () => {}; // per-texture fallbacks handle errors
      textureJobs.forEach((job) => job());
      if (!textureJobs.some(() => true) || !catalog.some((s) => s.products.some((p) => p.image_url))) finish();
      setTimeout(finish, 20000); // never block the mall on a slow photo
    });
  }

  function webglAvailable() {
    try {
      const c = document.createElement("canvas");
      return !!(window.WebGLRenderingContext && (c.getContext("webgl") || c.getContext("experimental-webgl")));
    } catch (_) {
      return false;
    }
  }

  window.KV = window.KV || {};
  window.KV.Mall3D = { create, webglAvailable, FLOOR_H };
})();
