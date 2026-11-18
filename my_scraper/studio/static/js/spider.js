/* Seine Studio: the live page view with the spider walking it. */

import { $, $$, nf, el, icon, tokens } from "./ui.js";

/** What a log event says about its page: ok, refused, error, or nothing (pending). */
export function pageStatus(ev) {
  if (ev.message === "http_request") return ev.status && ev.status < 400 ? "ok" : "error";
  if (/robots|blocked|refus|login|paywall/.test(ev.message)) return "refused";
  if (ev.level === "ERROR" || ev.level === "WARNING") return "error";
  if (ev.message === "parsing_complete") return "ok";
  return "pending";
}

// ---------------------------------------------------------------- the spider
/**
 * The page being crawled, drawn from its real text, with a spider walking it.
 * The spider lowers itself onto each page on a silk thread, darts across it in short
 * bursts the way spiders do, wraps every item it takes (the data flows into its abdomen),
 * taps the links the crawl will follow, then climbs back up its thread to the next page.
 */
export class PageWalk {
  constructor(ui) {
    this.ui = ui; this.c = ui.canvas; this.ctx = this.c.getContext("2d");
    this.pages = new Map(); this.queue = []; this.page = null; this.sheet = null;
    this.plan = []; this.target = null; this.actT = 0; this.mode = "hang";
    this.scroll = 0; this.carried = 0; this.walked = 0; this.finished = false;
    this.particles = []; this.silk = []; this.ripples = [];
    this.burst = 0; this.pause = 0; this.drag = null;
    this.s = { x: 0, y: 0, v: 0, h: Math.PI / 2, glow: 0, legs: [], anchor: null };
    this.ro = new ResizeObserver(() => this.size()); this.ro.observe(this.c);
    this.size();
    this.s.x = this.vw / 2; this.s.y = 46; this.s.anchor = { x: this.vw / 2, y: -10 };
    this.initLegs();
    this.say("Hanging on, waiting for the first page…");
    this.last = performance.now();
    this.raf = requestAnimationFrame((t) => this.frame(t));
  }
  size() {
    const r = this.c.getBoundingClientRect(), dpr = devicePixelRatio || 1;
    this.vw = r.width; this.vh = r.height; this.c.width = r.width * dpr; this.c.height = r.height * dpr;
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  say(text) { this.ui.action(text); }
  path(url) { try { const u = new URL(url); return (u.pathname + u.search) || "/"; } catch (e) { return url; } }

  // --- events in
  visit(url, status) {
    let page = this.pages.get(url);
    if (page) { if (status !== "pending") page.status = status; return; }
    page = { url, status, sketch: null, items: 0, given: 0, seen: performance.now() };
    this.pages.set(url, page); this.queue.push(page);
  }
  sketch(url, sketch) {
    if (!this.pages.has(url)) this.visit(url, "ok");
    const page = this.pages.get(url);
    page.sketch = sketch;
    if (page.status === "pending") page.status = "ok";
  }
  collect(url, n) {
    if (!n) return;
    const page = this.pages.get(url);
    if (!page || page.left) {  // it already moved on: straight into the count
      this.carried += n; if (page) page.given += n; this.ui.carried(this.carried);
      if (page) page.items += n;
      return;
    }
    page.items += n;
  }
  finish() { this.finished = true; }

  // --- pages
  mount(page) {
    const sk = page.sketch, sheet = el("div", { class: "sheet in" });
    if (page.status === "refused" || page.status === "error" || !sk) {
      const refused = page.status === "refused", error = page.status === "error";
      sheet.append(el("div", { class: "s-wall" + (refused ? " refused" : error ? " error" : "") },
        el("div", { class: "s-wall-mark", text: refused ? "⊘" : error ? "!" : "∅" }),
        el("h4", { text: refused ? "Not allowed in" : error ? "The page answered with an error" : "Nothing new to read here" }),
        el("p", { text: refused ? "robots.txt or the site's rules keep crawlers off this page, so the spider leaves it alone."
          : error ? "The spider touched it, found nothing usable, and moved on."
          : "Same content as a page already walked, or not a web page." })));
    } else {
      if (sk.nav && sk.nav.length) sheet.append(el("div", { class: "s-nav" }, ...sk.nav.map((n) => el("span", { text: n }))));
      sk.blocks.forEach((b, i) => {
        if (b.k === "img") { sheet.append(el("div", { class: "s-img", "data-i": i }, icon("page"), el("span", { text: b.alt || "image" }))); return; }
        sheet.append(el(b.k === "h" ? "h4" : "div", { class: `s-${b.k}${b.item ? " is-item" : ""}`, "data-i": i },
          ...b.parts.map(([text, href, follow]) => href ? el("span", { class: "s-link" + (follow ? " s-follow" : ""), title: href, text }) : text)));
      });
      if (!sk.blocks.length) sheet.append(el("div", { class: "s-wall" }, el("h4", { text: "No readable text on this page" })));
    }
    const old = this.sheet;
    if (old) { old.classList.add("out"); setTimeout(() => old.remove(), 450); }
    this.ui.sheets.append(sheet);
    this.sheet = sheet; this.scroll = 0;
    this.ui.tab((sk && sk.title) || this.path(page.url));
    this.ui.address(page.url, page.status);
  }
  planFor(page, backlog) {
    const sheet = this.sheet, plan = [];
    const at = (node, kind) => ({ kind, node, x: Math.min(node.offsetLeft + Math.min(node.offsetWidth * .3, 120), this.vw - 60), y: node.offsetTop + node.offsetHeight / 2 });
    if (!page.sketch || page.status !== "ok") {
      const wall = $(".s-wall", sheet);
      if (wall) plan.push({ ...at(wall, page.status === "ok" ? "look" : "poke"), x: this.vw / 2 });
      return plan;
    }
    const room = Math.max(1, Math.min(6, 6 - Math.floor(backlog * 1.2)));
    let items = $$(".is-item", sheet);
    if (!items.length && page.items) items = $$(".s-h, .s-p, .s-li", sheet).slice(0, 1);
    const pick = (list, n) => list.length <= n ? list : Array.from({ length: n }, (_, k) => list[Math.round(k * (list.length - 1) / Math.max(1, n - 1))]);
    for (const node of pick(items, room)) plan.push(at(node, "item"));
    for (const node of pick($$(".s-follow", sheet), items.length ? Math.min(2, room) : Math.min(4, room + 1))) {
      plan.push({ kind: "link", node, x: node.offsetLeft + node.offsetWidth / 2, y: node.offsetTop + node.offsetHeight / 2 + 2 });
    }
    if (!plan.length) { const first = $("[data-i]", sheet); if (first) plan.push(at(first, "look")); }
    return plan.sort((a, b) => a.y - b.y);
  }

  // --- legs: two alternating groups of four, two-bone IK each
  initLegs() {
    const S = this.S = 1;
    const rest = [[30, 23], [12, 33], [-8, 33], [-26, 26]];  // legs 1-2 reach forward, 3-4 back
    this.s.legs = [];
    for (const side of [-1, 1]) rest.forEach(([f, l], i) => {
      const leg = { side, i, f: f * S, l: l * side * S, hipF: [5, 2.5, 0, -2.5][i] * S, hipL: 3 * side * S,
        l1: 21 * S, l2: 26 * S, group: (i + (side > 0 ? 1 : 0)) % 2, stepping: false, t: 0 };
      const p = this.local(leg.f, leg.l); leg.x = p.x; leg.y = p.y;
      this.s.legs.push(leg);
    });
  }
  local(f, l) { const s = this.s, c = Math.cos(s.h), n = Math.sin(s.h); return { x: s.x + c * f - n * l, y: s.y + n * f + c * l }; }
  get onThread() { return !["walk", "act"].includes(this.mode); }
  updateLegs(dt, now) {
    const sp = this.s;
    if (this.onThread) {  // dangling: legs drawn in, slowly paddling
      for (const leg of sp.legs) {
        const paddle = Math.sin(now / 260 + leg.i * 1.7 + leg.side) * 3;
        const p = this.local(leg.f * .85 + (leg.i < 2 ? 8 : -4) + paddle, leg.l * .82);
        leg.x += (p.x - leg.x) * .35; leg.y += (p.y - leg.y) * .35; leg.stepping = false;
      }
      return;
    }
    const speed = Math.abs(sp.v), busy = [0, 0];
    for (const leg of sp.legs) if (leg.stepping) busy[leg.group]++;
    const dx = Math.cos(sp.h), dy = Math.sin(sp.h), lead = Math.min(14, speed * .09);
    for (const leg of sp.legs) {
      let ideal = this.local(leg.f, leg.l);
      if (this.mode === "act" && leg.i === 0) {  // front legs reach out and feel the prey
        const tap = Math.sin(now / 90 + (leg.side > 0 ? 1.4 : 0));
        ideal = this.local(leg.f + 10 + tap * 5, leg.l * .55);
        leg.x += (ideal.x - leg.x) * .4; leg.y += (ideal.y - leg.y) * .4; leg.stepping = false;
        continue;
      }
      if (leg.stepping) {
        leg.t = Math.min(1, leg.t + dt / leg.dur);
        const e = leg.t < .5 ? 2 * leg.t * leg.t : 1 - Math.pow(-2 * leg.t + 2, 2) / 2;
        leg.x = leg.fx + (leg.tx - leg.fx) * e; leg.y = leg.fy + (leg.ty - leg.fy) * e;
        if (leg.t >= 1) leg.stepping = false;
        continue;
      }
      if (Math.hypot(ideal.x - leg.x, ideal.y - leg.y) > 12 * this.S && !busy[1 - leg.group]) {
        leg.stepping = true; leg.t = 0; leg.dur = Math.max(.055, .12 - speed / 3000);
        leg.fx = leg.x; leg.fy = leg.y; leg.tx = ideal.x + dx * lead; leg.ty = ideal.y + dy * lead;
        busy[leg.group]++;
      }
    }
  }
  knee(hx, hy, fx, fy, l1, l2) {
    let dx = fx - hx, dy = fy - hy, d = Math.hypot(dx, dy) || .001;
    if (d > l1 + l2 - .5) { const k = (l1 + l2 - .5) / d; fx = hx + dx * k; fy = hy + dy * k; d = l1 + l2 - .5; dx = fx - hx; dy = fy - hy; }
    const a = Math.atan2(dy, dx), b = Math.acos(Math.max(-1, Math.min(1, (l1 * l1 + d * d - l2 * l2) / (2 * l1 * d))));
    const k1 = { x: hx + Math.cos(a + b) * l1, y: hy + Math.sin(a + b) * l1 }, k2 = { x: hx + Math.cos(a - b) * l1, y: hy + Math.sin(a - b) * l1 };
    const away = (k) => Math.hypot(k.x - this.s.x, k.y - this.s.y);  // knees arch away from the body
    return { knee: away(k1) > away(k2) ? k1 : k2, fx, fy };
  }

  // --- behaviour
  next() {
    const backlog = this.queue.length;
    if (backlog > 12) for (const p of this.queue.splice(0, backlog - 4)) this.skip(p);  // catching up on a replay
    const page = this.queue[0];
    if (!page) return false;
    if (page.status !== "refused" && page.status !== "error" && !page.sketch && performance.now() - page.seen < 2500) {
      this.say(`Reading ${this.path(page.url)}…`); return false;
    }
    this.queue.shift();
    this.page = page; this.walked++;
    this.mount(page);
    this.plan = this.planFor(page, this.queue.length);
    this.wraps = this.plan.filter((p) => p.kind === "item").length;
    const first = this.plan[0], sp = this.s;
    sp.x = first ? Math.max(40, Math.min(this.vw - 40, first.x + (Math.random() - .5) * 60)) : this.vw / 2;
    sp.anchor = { x: sp.x, y: -10 }; sp.y = -40; sp.h = Math.PI / 2; sp.v = 0;
    this.landY = Math.max(40, first ? Math.min(first.y - 30, this.vh * .5) : 90);
    this.mode = "descend";
    this.say(page.status === "refused" ? `Lowering onto ${this.path(page.url)} — off limits` : `Dropping onto ${this.path(page.url)}`);
    return true;
  }
  skip(page) {
    page.left = true;
    if (page.items > page.given) { this.carried += page.items - page.given; page.given = page.items; this.ui.carried(this.carried); }
    this.walked++;
  }
  leave() {
    const page = this.page;
    if (page) { page.left = true; if (page.items > page.given) this.spray(this.s.x, this.s.y, page.items - page.given, page); }
    this.page = null;
  }
  spray(x, y, n, page) {
    if (!n) return;
    page.given += n;
    const count = Math.max(3, Math.min(12, Math.round(Math.sqrt(n) * 2.2)));
    let left = n;
    for (let i = 0; i < count; i++) {
      const value = i === count - 1 ? left : Math.floor(n / count); left -= value;
      this.particles.push({ x0: x + (Math.random() - .5) * 60, y0: y + (Math.random() - .5) * 14, t: -i * .05, dur: .5 + Math.random() * .3, bend: (Math.random() - .5) * 70, value });
    }
  }

  update(dt, now) {
    const sp = this.s, hurry = 1 + Math.min(4, this.queue.length * .4);
    if (this.mode === "hang" || this.mode === "rest") {
      if (this.queue.length && this.mode === "rest") this.mode = "ascend";
      else if (this.queue.length && !this.next()) { /* waiting for the page's text */ }
      if (this.mode === "hang" || this.mode === "rest") {
        sp.x = sp.anchor.x + Math.sin(now / 900) * 6;
        const restY = (this.mode === "rest" ? this.vh * .42 : 46) + this.scroll;
        sp.y += (restY - sp.y) * Math.min(1, dt * 2);
        sp.h = Math.PI / 2 + Math.sin(now / 900) * .08;
        if (!this.queue.length && this.finished && this.mode === "hang") {
          this.mode = "rest"; this.say(`Done · ${nf.format(this.walked)} pages walked · ${nf.format(this.carried)} items carried home`);
        }
      }
    } else if (this.mode === "descend") {
      sp.y += 260 * hurry * dt; sp.x = sp.anchor.x + Math.sin(now / 400) * 2; sp.h = Math.PI / 2;
      if (sp.y >= this.landY + this.scroll) {
        this.mode = this.plan.length ? "walk" : "ascend";
        this.drag = { x: sp.x, y: sp.y };  // its dragline stays fixed where it landed
        this.burst = 0; this.pause = .15;
        if (this.mode === "ascend") sp.anchor = { x: sp.x, y: -10 };
      }
    } else if (this.mode === "walk") {
      const t = this.plan[0], dx = t.x - sp.x, dy = t.y - sp.y, d = Math.hypot(dx, dy);
      const want = Math.atan2(dy, dx), diff = Math.atan2(Math.sin(want - sp.h), Math.cos(want - sp.h));
      let speed = 0;
      if (Math.abs(diff) > .9 && d > 6) {
        sp.h += Math.sign(diff) * Math.min(Math.abs(diff), 7 * dt);  // stop and pivot on the spot first
      } else {
        sp.h += diff * Math.min(1, dt * 10);
        if (d < 46) speed = Math.max(30, d * 5) * hurry;
        else if (this.pause > 0) this.pause -= dt * hurry;
        else if (this.burst > 0) { this.burst -= dt; speed = 210 * hurry; }
        else if (Math.random() < .35) this.pause = .07 + Math.random() * .25;  // the stop-start of a hunting spider
        else this.burst = .22 + Math.random() * .4;
        speed = Math.min(speed, d * 8) * Math.pow(Math.max(0, Math.cos(diff)), 2);  // slow while turning, or it circles the target
      }
      sp.v += (speed - sp.v) * Math.min(1, dt * 16);
      sp.x += Math.cos(sp.h) * sp.v * dt; sp.y += Math.sin(sp.h) * sp.v * dt;
      if (d < 6) {
        this.mode = "act"; this.actT = 0; sp.v = 0; this.target = t;
        this.say(t.kind === "item" ? "Wrapping an item" : t.kind === "link" ? `Tapping a link: ${t.node.textContent.trim().slice(0, 40)}`
          : t.kind === "poke" ? "Touched it — backing off" : "Feeling around the page");
      }
    } else if (this.mode === "act") {
      const t = this.target, length = { item: 1.05, link: .55, poke: .6, look: .45 }[t.kind] / hurry;
      this.actT += dt;
      if (t.kind === "item" && Math.random() < dt * 22) {  // silk thrown over the prey
        this.silk.push({ x: t.node.offsetLeft + Math.random() * t.node.offsetWidth, y: t.node.offsetTop + Math.random() * t.node.offsetHeight, life: 1 });
      }
      if (this.actT > length * .5 && !t.done) {
        t.done = true;
        if (t.kind === "item") {
          t.node.classList.add("caught");
          const share = this.wraps > 0 ? Math.round((this.page.items - this.page.given) / this.wraps) : 0;
          this.wraps--;
          this.spray(t.x, t.y, Math.max(0, share), this.page);
        } else if (t.kind === "link") {
          t.node.classList.add("tapped"); this.ripples.push({ x: t.x, y: t.y, t: 0 });
        } else if (t.kind === "poke") {
          t.node.classList.add("shake"); sp.v = -160;
        }
      }
      if (t.kind === "poke") { sp.x += Math.cos(sp.h) * sp.v * dt; sp.y += Math.sin(sp.h) * sp.v * dt; sp.v *= .9; }
      if (this.actT >= length) {
        this.plan.shift(); this.mode = this.plan.length ? "walk" : "ascend"; this.burst = 0; this.pause = .1;
        if (this.mode === "ascend") { sp.anchor = { x: sp.x, y: -10 }; this.say(this.queue.length ? "Climbing up to the next page" : "Climbing back up the thread"); }
      }
    } else if (this.mode === "ascend") {
      const up = -Math.PI / 2, diff = Math.atan2(Math.sin(up - sp.h), Math.cos(up - sp.h));
      sp.h += diff * Math.min(1, dt * 10);
      sp.y -= 300 * hurry * dt;
      if (sp.y < this.scroll - 50) {
        this.leave(); this.drag = null;
        sp.anchor = { x: sp.x, y: -10 }; sp.h = Math.PI / 2;
        this.mode = "hang"; sp.y = this.scroll - 50;
        if (!this.next() && !this.finished) this.say("Listening for the next page…");
      }
    }
    // the camera follows the spider down long pages
    if (this.sheet && (this.mode === "walk" || this.mode === "act")) {
      const max = Math.max(0, this.sheet.offsetHeight - this.vh + 40), want = Math.max(0, Math.min(max, sp.y - this.vh * .45));
      this.scroll += (want - this.scroll) * Math.min(1, dt * 3.5);
    }
    if (this.sheet) this.sheet.style.transform = `translateY(${-this.scroll}px)`;
    this.updateLegs(dt, now);
    for (const p of this.particles) p.t += dt / p.dur;
    for (const p of this.particles) if (p.t >= 1 && !p.landed) { p.landed = true; this.carried += p.value; sp.glow = 1; this.ui.carried(this.carried); }
    this.particles = this.particles.filter((p) => p.t < 1);
    for (const s of this.silk) s.life -= dt * .9;
    this.silk = this.silk.filter((s) => s.life > 0);
    for (const r of this.ripples) r.t += dt * 1.8;
    this.ripples = this.ripples.filter((r) => r.t < 1);
    sp.glow = Math.max(0, sp.glow - dt * 1.5);
  }

  // --- drawing
  frame(now) {
    const dt = Math.min(.05, (now - this.last) / 1000); this.last = now;
    this.update(dt, now);
    const { ctx } = this, t = tokens(), sp = this.s;
    ctx.clearRect(0, 0, this.vw, this.vh);
    ctx.save(); ctx.translate(0, -this.scroll);
    const spin = this.local(-19 * this.S, 0);
    ctx.strokeStyle = t.ink; ctx.lineWidth = 1;
    if (!this.onThread) {
      if (this.drag) {  // the dragline: from the top, down to where it landed, then trailing behind it
        ctx.globalAlpha = .18; ctx.beginPath(); ctx.moveTo(this.drag.x, this.scroll - 10); ctx.lineTo(this.drag.x, this.drag.y); ctx.stroke();
        ctx.globalAlpha = .32; ctx.beginPath(); ctx.moveTo(this.drag.x, this.drag.y); ctx.lineTo(spin.x, spin.y); ctx.stroke();
      }
    } else {  // hanging from its thread
      const end = this.mode === "ascend" ? this.local(12, 0) : spin;
      ctx.globalAlpha = .55; ctx.beginPath(); ctx.moveTo(sp.anchor.x, this.scroll - 10);
      ctx.quadraticCurveTo((sp.anchor.x + end.x) / 2 + Math.sin(now / 700) * 4, (this.scroll - 10 + end.y) / 2, end.x, end.y); ctx.stroke();
    }
    ctx.globalAlpha = 1;
    for (const s of this.silk) { ctx.globalAlpha = s.life * .6; ctx.beginPath(); ctx.moveTo(spin.x, spin.y); ctx.lineTo(s.x, s.y); ctx.stroke(); }
    for (const r of this.ripples) { ctx.globalAlpha = 1 - r.t; ctx.strokeStyle = t.tide; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(r.x, r.y, 6 + r.t * 26, 0, 6.283); ctx.stroke(); }
    ctx.globalAlpha = 1;
    this.drawSpider(ctx, t, now);
    const abd = this.local(-10 * this.S, 0);
    for (const p of this.particles) {
      if (p.t < 0) continue;
      const e = 1 - Math.pow(1 - p.t, 3), mx = (p.x0 + abd.x) / 2 + p.bend, my = (p.y0 + abd.y) / 2 - 36;
      const x = (1 - e) * (1 - e) * p.x0 + 2 * (1 - e) * e * mx + e * e * abd.x, y = (1 - e) * (1 - e) * p.y0 + 2 * (1 - e) * e * my + e * e * abd.y;
      ctx.fillStyle = t.lime; ctx.globalAlpha = 1 - p.t * .3; ctx.fillRect(x - 2, y - 2, 4, 4);
    }
    ctx.restore(); ctx.globalAlpha = 1;
    this.raf = requestAnimationFrame((n) => this.frame(n));
  }
  drawSpider(ctx, t, now) {
    const sp = this.s, S = this.S, moving = Math.abs(sp.v) > 12;
    const bob = moving ? Math.sin(now / 45) * .6 : Math.sin(now / 650) * .35;
    if (!this.onThread) { ctx.globalAlpha = .25; ctx.fillStyle = "#000"; ctx.beginPath(); ctx.ellipse(sp.x + 3, sp.y + 6, 14 * S, 8 * S, sp.h, 0, 6.283); ctx.fill(); ctx.globalAlpha = 1; }
    ctx.strokeStyle = t.ink; ctx.fillStyle = t.ink; ctx.lineCap = "round"; ctx.lineJoin = "round";
    for (const leg of sp.legs) {
      const hip = this.local(leg.hipF, leg.hipL);
      let { knee, fx, fy } = this.knee(hip.x, hip.y, leg.x, leg.y, leg.l1, leg.l2);
      if (leg.stepping) {  // the knee rises as the foot swings
        const lift = Math.sin(leg.t * Math.PI) * 5 * S, d = Math.hypot(knee.x - sp.x, knee.y - sp.y) || 1;
        knee = { x: knee.x + (knee.x - sp.x) / d * lift, y: knee.y + (knee.y - sp.y) / d * lift };
      }
      ctx.lineWidth = 2.6 * S; ctx.beginPath(); ctx.moveTo(hip.x, hip.y); ctx.lineTo(knee.x, knee.y); ctx.stroke();
      ctx.lineWidth = 1.5 * S; ctx.beginPath(); ctx.moveTo(knee.x, knee.y); ctx.lineTo(fx, fy); ctx.stroke();
      ctx.beginPath(); ctx.arc(knee.x, knee.y, 1.4 * S, 0, 6.283); ctx.fill();
    }
    ctx.save(); ctx.translate(sp.x, sp.y); ctx.rotate(sp.h);
    const palp = this.mode === "act" ? Math.sin(now / 70) * 1.6 : Math.sin(now / 500) * .6;  // pedipalps twitch while it works
    ctx.lineWidth = 1.5 * S; ctx.beginPath();
    ctx.moveTo(7 * S, -2 * S); ctx.lineTo(12 * S, (-3.5 - palp) * S); ctx.moveTo(7 * S, 2 * S); ctx.lineTo(12 * S, (3.5 + palp) * S); ctx.stroke();
    ctx.shadowColor = t.lime; ctx.shadowBlur = 22 * sp.glow;  // the abdomen glows as data flows in
    ctx.beginPath(); ctx.ellipse(-10 * S, 0, (9.5 + bob) * S, (7.4 + bob * .6) * S, 0, 0, 6.283); ctx.fill();
    ctx.shadowBlur = 0; ctx.fillStyle = t.lime; ctx.globalAlpha = .55 + sp.glow * .45;
    for (const [x, w] of [[-6, 2.2], [-10, 3], [-14, 2.1]]) { ctx.beginPath(); ctx.moveTo((x + 1.6) * S, 0); ctx.lineTo((x - 1.4) * S, -w * S); ctx.lineTo((x - .4) * S, 0); ctx.lineTo((x - 1.4) * S, w * S); ctx.closePath(); ctx.fill(); }
    ctx.globalAlpha = 1; ctx.fillStyle = t.ink; ctx.beginPath(); ctx.ellipse(2 * S, 0, 6.4 * S, 5.4 * S, 0, 0, 6.283); ctx.fill();
    ctx.fillStyle = t.bg;
    for (const [x, y, r] of [[6, -1.5, 1.1], [6, 1.5, 1.1], [4.6, -3, .65], [4.6, 3, .65]]) { ctx.beginPath(); ctx.arc(x * S, y * S, r * S, 0, 6.283); ctx.fill(); }
    ctx.restore();
  }
  destroy() { cancelAnimationFrame(this.raf); this.ro.disconnect(); }
}
