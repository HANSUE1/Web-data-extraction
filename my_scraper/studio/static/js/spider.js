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
const S = 1.75;   // body scale: every length below is in "spider units"
const HANG = 74;  // how far below the top edge it dangles while it waits
// a wolf spider's browns, the same in both themes: it is an animal, not a UI element
const PAL = { outline: "#160e08", deep: "#2a1c12", dark: "#3e2a1b", base: "#6e5036", light: "#b6936b", pale: "#a5865f", cream: "#d6c29c" };
// leg pairs I-IV: rest foot spot [forward, sideways], coxa on the carapace, femur and lower-leg lengths
const LEGS = [
  { rest: [36, 27], hip: [5.5, 5.2], l1: 24, l2: 30 },
  { rest: [15, 38], hip: [2.5, 6.4], l1: 22, l2: 28 },
  { rest: [-10, 37], hip: [-.5, 6.4], l1: 19, l2: 25 },
  { rest: [-35, 28], hip: [-3.5, 5.2], l1: 25, l2: 33 },
];
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
    this.sense = 0; this.groom = 0; this.wander = 0; this.weave = 0; this.crab = 0; this.groomSide = 1; this.settle = 0; this.air = 1; this.stride = 0;
    this.s = { x: 0, y: 0, v: 0, h: Math.PI / 2, m: Math.PI / 2, glow: 0, legs: [], anchor: null };
    this.ro = new ResizeObserver(() => this.size()); this.ro.observe(this.c);
    this.size();
    this.s.x = this.vw / 2; this.s.y = HANG; this.s.anchor = { x: this.vw / 2, y: -10 };
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

  // --- legs: alternating tetrapod gait (L1 R2 L3 R4, then the others), two-bone IK each
  initLegs() {
    this.s.legs = [];
    for (const side of [-1, 1]) LEGS.forEach((d, i) => {
      const leg = { side, i, f: d.rest[0] * S, l: d.rest[1] * side * S, hipF: d.hip[0] * S, hipL: d.hip[1] * side * S,
        l1: d.l1 * S, l2: d.l2 * S, group: (i + (side > 0 ? 1 : 0)) % 2, stepping: false, t: 0, lift: 0 };
      const p = this.local(leg.f, leg.l); leg.x = p.x; leg.y = p.y;
      this.s.legs.push(leg);
    });
    // fixed texture, drawn every frame. Spiders are furry: the pattern is mostly in the colour of their hair.
    const rnd = (a, b) => a + Math.random() * (b - a);
    // abdomen (its own frame, centre 0,0): an egg, narrower at the waist end (+x)
    const abdRy = (a) => 9.4 * (1 - .1 * Math.cos(a));
    this.abd = Array.from({ length: 40 }, (_, k) => { const a = k / 40 * 6.283; return [12.6 * Math.cos(a), abdRy(a) * Math.sin(a)]; });
    const abdTone = (x, y) => {  // wolf-spider back: dark flanks, a pale band down the middle holding a dark lance-shaped heart mark
      const ay = Math.abs(y), band = 2.8 + (x < 0 ? x * .18 : -x * .12);
      if (x > -3 && x < 10.5 && ay < 1.5 * (1 - Math.abs(x - 3.5) / 7.5) + .3) return "dark";
      if (ay < band) return x < -2 && Math.abs((x * 10) % 24) < 5 ? "dark" : "pale";
      if (ay < band + 1.1) return "cream";
      return Math.random() < .25 ? "pale" : "deep";
    };
    this.abdFur = this.furPatch(900, () => { const a = rnd(0, 6.283), r = Math.sqrt(Math.random()) * .97; return [12.6 * r * Math.cos(a), abdRy(a) * r * Math.sin(a)]; },
      (x, y) => [-1, y * .06 + rnd(-.25, .25)], [.9, 2.1], abdTone);
    // carapace: an ellipse narrowing into the head region; pale median and side bands on dark brown
    const caraR = (a) => 7.6 * (1 - .24 * Math.max(0, Math.cos(a)));
    this.cara = Array.from({ length: 36 }, (_, k) => { const a = k / 36 * 6.283; return [2 + 9 * Math.cos(a), caraR(a) * Math.sin(a)]; });
    this.caraFur = this.furPatch(420, () => { const a = rnd(0, 6.283), r = Math.sqrt(Math.random()) * .96; return [2 + 9 * r * Math.cos(a), caraR(a) * r * Math.sin(a)]; },
      (x, y) => [-.45 + rnd(-.2, .2), Math.sign(y) * .55], [.6, 1.3], (x, y) => {
        const ay = Math.abs(y), edge = Math.hypot((x - 2) / 9, y / caraR(Math.atan2(y / 7.6, (x - 2) / 9)));
        if (x > 5.5 && ay < 3.9) return "deep";  // the black eye region
        if (ay < 1.3 + Math.max(0, x - 1) * .32) return "pale";
        if (edge > .62 && edge < .84) return "cream";
        return "deep";
      });
    this.speck = Array.from({ length: 40 }, () => { const a = rnd(0, 6.283), r = Math.sqrt(rnd(.3, .9)); return { x: Math.cos(a) * r * 12, y: Math.sin(a) * r * 9, r: rnd(.3, .7) }; });
  }
  /** n short hairs, as flat [x1,y1,x2,y2,...] arrays grouped by colour, so each colour is one stroke() per frame. */
  furPatch(n, place, lean, len, tone) {
    const out = {};
    for (let k = 0; k < n; k++) {
      const [x, y] = place(), [ux, uy] = lean(x, y), l = (len[0] + Math.random() * (len[1] - len[0])) / (Math.hypot(ux, uy) || 1);
      (out[tone(x, y)] ||= []).push(x - ux * l * .3, y - uy * l * .3, x + ux * l, y + uy * l);
    }
    return out;
  }
  local(f, l) { const s = this.s, c = Math.cos(s.h), n = Math.sin(s.h); return { x: s.x + c * f - n * l, y: s.y + n * f + c * l }; }
  get onThread() { return !["walk", "act"].includes(this.mode); }
  updateLegs(dt, now) {
    const sp = this.s, ease = (leg, p, k) => { leg.x += (p.x - leg.x) * k; leg.y += (p.y - leg.y) * k; leg.stepping = false; };
    if (this.onThread) {
      // dangling: legs flexed and slowly paddling, hind legs holding the dragline; spread out just before touching down
      const near = this.mode === "descend" ? Math.max(0, Math.min(1, 1 - (this.landY + this.scroll - sp.y) / (90 * S))) : 0;
      this.air += (1 - near - this.air) * Math.min(1, dt * 8);
      for (const leg of sp.legs) {
        let f = leg.f * (.6 + near * .4) + Math.sin(now / 260 + leg.i * 1.7 + leg.side) * 3 * S * (1 - near), l = leg.l * (.62 + near * .38);
        if (this.mode === "ascend" && leg.i < 2) {  // front legs haul the thread in, left and right in turn
          const reach = Math.sin(now / 150 + (leg.side > 0 ? Math.PI : 0) + leg.i * 1.9);
          f = (20 + reach * 9) * S; l = leg.side * (3.5 + (1 - reach) * 2) * S;
        } else if (leg.i === 3 && this.mode !== "descend") { f = -26 * S; l = leg.side * 6 * S; }
        else if (this.groom > 0 && leg.i === 0 && leg.side === this.groomSide) {  // drawing a front leg through the fangs
          f = (12 + Math.sin(now / 55) * 2.5) * S; l = leg.side * 2.5 * S;
        }
        ease(leg, this.local(f, l), .3); leg.lift = 1 - near;
      }
      return;
    }
    this.air += (0 - this.air) * Math.min(1, dt * 10);
    const speed = Math.abs(sp.v), busy = [0, 0];
    for (const leg of sp.legs) if (leg.stepping) busy[leg.group]++;
    const dx = Math.cos(sp.m), dy = Math.sin(sp.m), lead = Math.min(14 * S, speed * .09);  // feet land ahead along the travel line, sideways too
    const wrapping = this.mode === "act" && this.target && this.target.kind === "item";
    for (const leg of sp.legs) {
      let pose = null;  // a leg held off the page: [forward, sideways]
      if (this.mode === "act" && leg.i === 0) {  // front legs reach out and feel the prey
        const tap = Math.sin(now / 90 + (leg.side > 0 ? 1.4 : 0));
        pose = [leg.f + (10 + tap * 5) * S, leg.l * .55];
      } else if (wrapping && leg.i === 3) {  // hind legs comb silk out of the spinnerets over it
        pose = [(-31 + Math.sin(now / 85 + (leg.side > 0 ? Math.PI : 0)) * 6) * S, leg.side * 7 * S];
      } else if (this.sense > 0 && leg.i === 0) {  // stopped: front legs raised, tasting the air
        pose = [leg.f + (8 + Math.sin(now / 140 + leg.side) * 3) * S, leg.l * .5];
      }
      if (pose) { ease(leg, this.local(...pose), .4); leg.lift += (1 - leg.lift) * .3; continue; }
      if (leg.stepping) {
        leg.t = Math.min(1, leg.t + dt / leg.dur);
        const e = leg.t < .5 ? 2 * leg.t * leg.t : 1 - Math.pow(-2 * leg.t + 2, 2) / 2;
        leg.x = leg.fx + (leg.tx - leg.fx) * e; leg.y = leg.fy + (leg.ty - leg.fy) * e;
        leg.lift = Math.sin(leg.t * Math.PI);
        if (leg.t >= 1) leg.stepping = false;
        continue;
      }
      leg.lift *= .7;
      const ideal = this.local(leg.f, leg.l);
      if (Math.hypot(ideal.x - leg.x, ideal.y - leg.y) > 12 * S && !busy[1 - leg.group]) {
        leg.stepping = true; leg.t = 0; leg.dur = Math.max(.065, .14 - speed / 3000);
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
    sp.anchor = { x: sp.x, y: -10 }; sp.y = -40; sp.h = sp.m = Math.PI / 2; sp.v = 0;
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
        const restY = (this.mode === "rest" ? this.vh * .42 : HANG) + this.scroll;
        sp.y += (restY - sp.y) * Math.min(1, dt * 2);
        sp.h = Math.PI / 2 + Math.sin(now / 2300) * .3 + Math.sin(now / 900) * .06;  // slowly twisting on its thread
        if (this.groom <= 0 && Math.random() < dt * .2) { this.groom = 1.2 + Math.random(); this.groomSide = Math.random() < .5 ? -1 : 1; }
        if (!this.queue.length && this.finished && this.mode === "hang") {
          this.mode = "rest"; this.say(`Done · ${nf.format(this.walked)} pages walked · ${nf.format(this.carried)} items carried home`);
        }
      }
    } else if (this.mode === "descend") {
      sp.y += 260 * hurry * dt; sp.x = sp.anchor.x + Math.sin(now / 400) * 2; sp.h = Math.PI / 2;
      if (sp.y >= this.landY + this.scroll) {
        this.mode = this.plan.length ? "walk" : "ascend";
        this.drag = { x: sp.x, y: sp.y };  // its dragline stays fixed where it landed
        this.burst = 0; this.pause = .15; this.settle = 1;
        if (this.mode === "ascend") sp.anchor = { x: sp.x, y: -10 };
      }
    } else if (this.mode === "walk") {
      // A hunting spider never beelines: each burst sets off a little off-line, the path weaves side to side,
      // and when the prey sits off to one flank it often side-steps there crab-wise instead of turning to face it.
      const t = this.plan[0], dx = t.x - sp.x, dy = t.y - sp.y, d = Math.hypot(dx, dy);
      const goal = Math.atan2(dy, dx), turn = (a, b) => Math.atan2(Math.sin(a - b), Math.cos(a - b));
      const fade = Math.max(0, Math.min(1, (d - 30) / 160));  // wander less as it closes in, or it would circle the target
      let speed = 0, move = goal + (this.wander + Math.sin(this.weave) * .45) * fade, face = move;
      if (this.crab) { move = goal + this.wander * .3 * fade; face = sp.h; }
      const diff = turn(face, sp.h);
      if (Math.abs(diff) > 1.3 && d > 6) {
        sp.h += Math.sign(diff) * Math.min(Math.abs(diff), 6 * dt);  // too far round: stop and pivot on the spot first
      } else {
        sp.h += diff * Math.min(1, dt * 5);  // otherwise turn while walking, so the path arcs
        if (d < 40) { speed = Math.max(28, d * 5) * hurry; this.crab = 0; }
        else if (this.pause > 0) this.pause -= dt * hurry;
        else if (this.burst > 0) { this.burst -= dt; speed = (this.crab ? 150 : 240) * hurry; }
        else if (Math.random() < .35) {  // the stop-start of a hunting spider, sometimes rearing up to sense
          this.pause = .07 + Math.random() * .25; this.crab = 0;
          if (hurry < 1.5 && Math.random() < .25) { this.pause += .5; this.sense = this.pause; }
        } else {  // a new burst: a fresh slant, or a side-step if the target is off a flank
          this.burst = .22 + Math.random() * .4;
          this.wander = (Math.random() - .5) * 1.3;
          const flank = Math.sin(turn(goal, sp.h));
          this.crab = Math.abs(flank) > .6 && Math.cos(turn(goal, sp.h)) > -.3 && d > 60 && Math.random() < .45 ? Math.sign(flank) : 0;
          if (this.crab) this.burst *= .7;
        }
        speed = Math.min(speed, d * 8) * (this.crab ? 1 : Math.pow(Math.max(0, Math.cos(diff)), 2));
      }
      sp.m += turn(move, sp.m) * Math.min(1, dt * 7);  // travel direction lags the wanted one: smooth curves, no zigzag corners
      sp.v += (speed - sp.v) * Math.min(1, dt * 16);
      sp.x += Math.cos(sp.m) * sp.v * dt; sp.y += Math.sin(sp.m) * sp.v * dt;
      this.weave += sp.v * dt / (38 * S);
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
    this.sense = Math.max(0, this.sense - dt); this.groom = Math.max(0, this.groom - dt);
    this.settle = Math.max(0, this.settle - dt * 4); this.stride += sp.v * dt / (9 * S);
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
    const dt = Math.max(0, Math.min(.05, (now - this.last) / 1000)); this.last = now;  // the first frame can stamp earlier than the constructor
    this.update(dt, now);
    const { ctx } = this, t = tokens(), sp = this.s;
    ctx.clearRect(0, 0, this.vw, this.vh);
    ctx.save(); ctx.translate(0, -this.scroll);
    const spin = this.local(-27 * S, 0);
    ctx.strokeStyle = t.ink; ctx.lineWidth = 1;
    if (!this.onThread) {
      if (this.drag) {  // the dragline: from the top, down to where it landed, then trailing behind it
        ctx.globalAlpha = .18; ctx.beginPath(); ctx.moveTo(this.drag.x, this.scroll - 10); ctx.lineTo(this.drag.x, this.drag.y); ctx.stroke();
        ctx.globalAlpha = .32; ctx.beginPath(); ctx.moveTo(this.drag.x, this.drag.y); ctx.lineTo(spin.x, spin.y); ctx.stroke();
      }
    } else {  // hanging from its thread
      const end = this.mode === "ascend" ? this.local(10 * S, 0) : spin;
      ctx.globalAlpha = .55; ctx.beginPath(); ctx.moveTo(sp.anchor.x, this.scroll - 10);
      ctx.quadraticCurveTo((sp.anchor.x + end.x) / 2 + Math.sin(now / 700) * 4, (this.scroll - 10 + end.y) / 2, end.x, end.y); ctx.stroke();
    }
    ctx.globalAlpha = 1;
    for (const s of this.silk) { ctx.globalAlpha = s.life * .6; ctx.beginPath(); ctx.moveTo(spin.x, spin.y); ctx.lineTo(s.x, s.y); ctx.stroke(); }
    for (const r of this.ripples) { ctx.globalAlpha = 1 - r.t; ctx.strokeStyle = t.tide; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(r.x, r.y, 6 + r.t * 26, 0, 6.283); ctx.stroke(); }
    ctx.globalAlpha = 1;
    this.drawSpider(ctx, t, now);
    const abd = this.local(-15 * S, 0);
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
    const sp = this.s, moving = Math.abs(sp.v) > 12, P = PAL;
    const c = Math.cos(sp.h), n = Math.sin(sp.h);
    const LW = { x: -.55, y: -.83 }, L = { x: c * LW.x + n * LW.y, y: -n * LW.x + c * LW.y };  // light from the top left, in body space
    // a tapered, slightly swollen limb segment, shaded like a cylinder across its width
    const limb = (a, b, w0, w1, l, fill) => {
      const dx = b.x - a.x, dy = b.y - a.y, d = Math.hypot(dx, dy) || 1, nx = -dy / d, ny = dx / d;
      const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2, wm = Math.max(w0, w1) * .62;
      ctx.beginPath();
      ctx.moveTo(a.x + nx * w0 / 2, a.y + ny * w0 / 2); ctx.quadraticCurveTo(mx + nx * wm, my + ny * wm, b.x + nx * w1 / 2, b.y + ny * w1 / 2);
      ctx.lineTo(b.x - nx * w1 / 2, b.y - ny * w1 / 2); ctx.quadraticCurveTo(mx - nx * wm, my - ny * wm, a.x - nx * w0 / 2, a.y - ny * w0 / 2);
      ctx.closePath();
      if (fill) { ctx.fillStyle = fill; ctx.fill(); return; }
      const s = nx * l.x + ny * l.y > 0 ? 1 : -1, g = ctx.createLinearGradient(mx + nx * s * wm, my + ny * s * wm, mx - nx * s * wm, my - ny * s * wm);
      g.addColorStop(0, P.light); g.addColorStop(.35, P.base); g.addColorStop(.8, P.dark); g.addColorStop(1, P.outline);
      ctx.fillStyle = g; ctx.fill();
    };
    const lerp = (a, b, f) => ({ x: a.x + (b.x - a.x) * f, y: a.y + (b.y - a.y) * f });
    const band = (a, b, f0, f1, w) => limb(lerp(a, b, f0), lerp(a, b, f1), w, w * .95, null, "rgba(30,20,12,.55)");  // the dark rings on wolf-spider legs
    const hairs = (a, b, k, len, spread) => {  // setae lean toward the foot, both sides, fixed spots so they never flicker
      const dx = b.x - a.x, dy = b.y - a.y, d = Math.hypot(dx, dy) || 1, ux = dx / d, uy = dy / d;
      for (let j = 0; j < k; j++) {
        const f = (j + .5) / k, s = j % 2 ? 1 : -1, px = a.x + dx * f - uy * s * spread, py = a.y + dy * f + ux * s * spread;
        const ll = len * (.7 + ((j * 37) % 10) / 22);
        ctx.moveTo(px, py); ctx.lineTo(px + (-uy * s * .55 + ux * .85) * ll, py + (ux * s * .55 + uy * .85) * ll);
      }
    };
    // leg geometry once per frame: hip, knee from IK, then tibia, metatarsus and tarsus along the lower bone
    const geo = sp.legs.map((leg) => {
      const hip = this.local(leg.hipF, leg.hipL);
      let { knee, fx, fy } = this.knee(hip.x, hip.y, leg.x, leg.y, leg.l1, leg.l2);
      const out = Math.hypot(knee.x - sp.x, knee.y - sp.y) || 1, ox = (knee.x - sp.x) / out, oy = (knee.y - sp.y) / out;
      const rise = leg.lift * 5 * S;  // a lifted leg's knee rides higher, so it shows further out
      knee = { x: knee.x + ox * rise, y: knee.y + oy * rise };
      const dx = fx - knee.x, dy = fy - knee.y, d = Math.hypot(dx, dy) || 1, px = -dy / d, py = dx / d;
      const bow = px * ox + py * oy > 0 ? 1 : -1;  // the lower leg bows outward a little at each joint
      const pat = { x: knee.x + dx * .1 + px * bow * .6 * S, y: knee.y + dy * .1 + py * bow * .6 * S };
      const tib = { x: knee.x + dx * .5 + px * bow * 1.4 * S, y: knee.y + dy * .5 + py * bow * 1.4 * S };
      const met = { x: knee.x + dx * .86 + px * bow * .5 * S, y: knee.y + dy * .86 + py * bow * .5 * S };
      return { hip, knee, pat, tib, met, foot: { x: fx, y: fy }, lift: leg.lift, i: leg.i };
    });
    // shadows: soft, and further from the body the higher it hangs, further from each leg as it lifts
    const off = (2 + this.air * 16) * S;
    ctx.lineCap = "round"; ctx.lineJoin = "round"; ctx.strokeStyle = "#000";
    for (const g of geo) {
      const o = off + g.lift * 4 * S, sx = o * .45, sy = o * .9;
      ctx.globalAlpha = .13 - this.air * .06; ctx.lineWidth = 1.8 * S; ctx.beginPath(); ctx.moveTo(g.hip.x + sx, g.hip.y + sy);
      for (const p of [g.knee, g.tib, g.met, g.foot]) ctx.lineTo(p.x + sx, p.y + sy);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
    for (const [f, rx, ry] of [[-15, 15, 12], [2, 11, 9.5]]) {  // body shadow: a soft-edged blot, not a hard ellipse
      const p = this.local(f * S, 0);
      ctx.save(); ctx.translate(p.x + off * .45, p.y + off * .9); ctx.rotate(sp.h); ctx.scale(rx * S, ry * S);
      const g = ctx.createRadialGradient(0, 0, 0, 0, 0, 1); g.addColorStop(0, `rgba(0,0,0,${.42 - this.air * .2})`); g.addColorStop(.6, `rgba(0,0,0,${.25 - this.air * .12})`); g.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = g; ctx.beginPath(); ctx.arc(0, 0, 1, 0, 6.283); ctx.fill(); ctx.restore();
    }
    // legs: coxa hidden under the body, swollen femur, short patella, tibia, metatarsus, slender tarsus
    for (const g of geo) {
      const w = (g.i === 3 ? 1.08 : g.i === 2 ? .9 : 1) * S;  // the hind pair is the stoutest
      limb(g.hip, g.knee, 2.9 * w, 2.6 * w, LW); band(g.hip, g.knee, .62, .8, 2.75 * w);
      limb(g.knee, g.pat, 2.5 * w, 2.4 * w, LW);
      limb(g.pat, g.tib, 2.35 * w, 2 * w, LW); band(g.pat, g.tib, .2, .4, 2.3 * w); band(g.pat, g.tib, .78, 1, 2.1 * w);
      limb(g.tib, g.met, 1.85 * w, 1.4 * w, LW); band(g.tib, g.met, .8, 1, 1.5 * w);
      limb(g.met, g.foot, 1.3 * w, .8 * w, LW);
      ctx.fillStyle = P.outline; ctx.beginPath(); ctx.arc(g.foot.x, g.foot.y, .55 * S, 0, 6.283); ctx.fill();  // claw tuft
    }
    ctx.lineWidth = .32 * S;
    for (const [color, alpha, k, len] of [[P.light, .5, 1, 1], [P.outline, .55, .7, .9]]) {  // two tones of fine hair over every segment
      ctx.globalAlpha = alpha; ctx.strokeStyle = color; ctx.beginPath();
      for (const g of geo) {
        hairs(g.hip, g.knee, Math.round(10 * k), 1.7 * S * len, 1.2 * S); hairs(g.pat, g.tib, Math.round(9 * k), 1.6 * S * len, 1 * S);
        hairs(g.tib, g.met, Math.round(8 * k), 1.4 * S * len, .8 * S); hairs(g.met, g.foot, Math.round(7 * k), 1.1 * S * len, .55 * S);
      }
      ctx.stroke();
    }
    ctx.globalAlpha = .9; ctx.strokeStyle = P.outline; ctx.lineWidth = .5 * S; ctx.beginPath();
    for (const g of geo) { hairs(g.pat, g.tib, 3, 3 * S, 1 * S); hairs(g.tib, g.met, 2 + (g.i < 2 ? 2 : 0), 2.6 * S, .8 * S); }  // stiff spines, strongest on the front legs
    ctx.stroke(); ctx.globalAlpha = 1;

    // the body, in its own frame: +x is forward
    const sway = moving ? Math.sin(this.stride * Math.PI) * .045 : 0, squash = 1 - this.settle * .06;
    ctx.save(); ctx.translate(sp.x, sp.y); ctx.rotate(sp.h + sway); ctx.scale(squash * S, squash * S);
    const fur = (patch, colors, width) => {
      ctx.lineWidth = width;
      for (const [tone, [color, alpha]] of Object.entries(colors)) {
        const a = patch[tone]; if (!a) continue;
        ctx.globalAlpha = alpha; ctx.strokeStyle = color; ctx.beginPath();
        for (let k = 0; k < a.length; k += 4) { ctx.moveTo(a[k], a[k + 1]); ctx.lineTo(a[k + 2], a[k + 3]); }
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
    };
    const path = (pts) => { ctx.beginPath(); pts.forEach(([x, y], k) => k ? ctx.lineTo(x, y) : ctx.moveTo(x, y)); ctx.closePath(); };
    // pedipalps: short leg-like feelers, twitching while it works, drumming while it grooms
    const palp = this.mode === "act" ? Math.sin(now / 70) * 1.6 : this.groom > 0 ? Math.sin(now / 45) * 1.2 : Math.sin(now / 500) * .6;
    ctx.lineCap = "round"; ctx.lineWidth = .25; ctx.strokeStyle = P.light;
    for (const side of [-1, 1]) {
      const a = { x: 8.5, y: 3.3 * side }, b = { x: 12.4, y: (5.1 + palp) * side }, e = { x: 15.4, y: (4 + palp * .6) * side };
      limb(a, b, 1.6, 1.4, L); limb(b, e, 1.4, 1.5, L);
      ctx.fillStyle = P.dark; ctx.beginPath(); ctx.ellipse(e.x + .5, e.y, 1.3, .95, side * .35, 0, 6.283); ctx.fill();
      ctx.globalAlpha = .5; ctx.beginPath(); hairs(a, b, 5, 1.1, .6); hairs(b, e, 5, 1, .6); ctx.stroke(); ctx.globalAlpha = 1;
    }
    // chelicerae: stout, hairy, with dark fangs folded underneath
    for (const side of [-1, 1]) {
      const g = ctx.createRadialGradient(10.4 + L.x, 1.7 * side + L.y, .2, 11, 1.7 * side, 2.9);
      g.addColorStop(0, P.base); g.addColorStop(.6, P.dark); g.addColorStop(1, P.outline);
      ctx.fillStyle = g; ctx.beginPath(); ctx.ellipse(11.1, 1.65 * side, 2.8, 1.8, 0, 0, 6.283); ctx.fill();
      ctx.globalAlpha = .45; ctx.strokeStyle = P.light; ctx.lineWidth = .22; ctx.beginPath();
      for (let k = 0; k < 7; k++) { const y = (.5 + k * .33) * side; ctx.moveTo(9.4 + (k % 2) * .6, y); ctx.lineTo(12.6 + (k % 3) * .3, y * .9); }
      ctx.stroke(); ctx.globalAlpha = 1;
      ctx.strokeStyle = "#140b07"; ctx.lineWidth = .6; ctx.beginPath(); ctx.moveTo(13.5, 1.5 * side); ctx.quadraticCurveTo(14.3, .9 * side, 13.8, .2 * side); ctx.stroke();
    }
    // abdomen: breathing, an egg furred in its own pattern, glowing as data flows in
    const breath = 1 + Math.sin(now / 620) * .018;
    ctx.save(); ctx.translate(-15.4, 0); ctx.scale(breath, breath);
    ctx.shadowColor = t.lime; ctx.shadowBlur = 26 * S * sp.glow;
    let g = ctx.createRadialGradient(L.x * 6, L.y * 5, 1, 0, 0, 13.5);
    g.addColorStop(0, P.base); g.addColorStop(.55, P.dark); g.addColorStop(1, P.outline);
    ctx.fillStyle = g; path(this.abd); ctx.fill(); ctx.shadowBlur = 0;
    ctx.save(); path(this.abd); ctx.clip();
    ctx.globalAlpha = .4; ctx.fillStyle = P.pale; ctx.beginPath(); ctx.ellipse(-.5, 0, 12, 2.9, 0, 0, 6.283); ctx.fill();  // the pale dorsal band under the fur
    ctx.globalAlpha = .85; ctx.fillStyle = P.deep;  // cardiac mark
    ctx.beginPath(); ctx.moveTo(10.5, 0); ctx.quadraticCurveTo(3.5, -2.3, -3, 0); ctx.quadraticCurveTo(3.5, 2.3, 10.5, 0); ctx.fill();
    ctx.globalAlpha = 1;
    fur(this.abdFur, { deep: [P.deep, .75], dark: [P.outline, .65], pale: [P.pale, .45], cream: [P.cream, .4] }, .32);
    for (const d of this.speck) { ctx.globalAlpha = .4; ctx.fillStyle = P.cream; ctx.beginPath(); ctx.arc(d.x, d.y, d.r * .6, 0, 6.283); ctx.fill(); }
    const chevrons = (color, alpha) => {  // the folium's chevrons toward the spinnerets
      ctx.globalAlpha = alpha; ctx.strokeStyle = color; ctx.lineWidth = 1; ctx.beginPath();
      for (let k = 0; k < 4; k++) { const x = -3.5 - k * 2.3, w = 3.6 - k * .6; ctx.moveTo(x - 1.1, -w); ctx.lineTo(x + 1.1, 0); ctx.lineTo(x - 1.1, w); }
      ctx.stroke();
    };
    chevrons(P.outline, .35);
    if (sp.glow > 0) chevrons(t.lime, sp.glow);
    const sheen = ctx.createRadialGradient(L.x * 6, L.y * 5, 0, L.x * 6, L.y * 5, 7);  // a soft, hairy sheen, not a gloss
    sheen.addColorStop(0, "rgba(255,240,215,.16)"); sheen.addColorStop(1, "rgba(255,240,215,0)");
    ctx.globalAlpha = 1; ctx.fillStyle = sheen; ctx.fillRect(-13, -10, 26, 20);
    ctx.restore();
    ctx.globalAlpha = .3; ctx.strokeStyle = P.light; ctx.lineWidth = .22; ctx.beginPath();  // the halo of guard hairs that breaks up the outline
    for (let k = 0; k < this.abd.length; k++) { const [x, y] = this.abd[k], l = 1 + (k * 7 % 5) * .25; ctx.moveTo(x * .97, y * .97); ctx.lineTo(x * (1 + l / 13) - l * .4, y * (1 + l / 10)); }
    ctx.stroke(); ctx.globalAlpha = 1;
    ctx.fillStyle = P.dark;  // spinnerets
    for (const s of [-1, 1]) { ctx.beginPath(); ctx.ellipse(-12.7, 1 * s, 1.5, .8, s * .3, 0, 6.283); ctx.fill(); }
    ctx.restore();
    ctx.fillStyle = P.deep; ctx.beginPath(); ctx.ellipse(-4.4, 0, 2.2, 1.7, 0, 0, 6.283); ctx.fill();  // pedicel
    // carapace: dark brown, a pale median band and side bands carried by the fur, striae radiating from the fovea
    g = ctx.createRadialGradient(2 + L.x * 4, L.y * 3, .5, 2, 0, 10);
    g.addColorStop(0, P.base); g.addColorStop(.55, P.dark); g.addColorStop(1, P.outline);
    ctx.fillStyle = g; path(this.cara); ctx.fill();
    ctx.save(); path(this.cara); ctx.clip();
    ctx.globalAlpha = .45; ctx.fillStyle = P.pale; ctx.beginPath();
    ctx.moveTo(-7, 0); ctx.quadraticCurveTo(0, -1.4, 5.5, -3); ctx.lineTo(5.5, 3); ctx.quadraticCurveTo(0, 1.4, -7, 0); ctx.fill();
    ctx.globalAlpha = 1;
    fur(this.caraFur, { deep: [P.outline, .6], pale: [P.pale, .65], cream: [P.cream, .45] }, .3);
    ctx.globalAlpha = .4; ctx.strokeStyle = P.outline; ctx.lineWidth = .45; ctx.beginPath();
    for (const a of [.55, 1.2, 1.85, 2.5]) for (const s of [-1, 1]) { ctx.moveTo(-.5 + Math.cos(a) * 2.2, Math.sin(a) * 2.2 * s); ctx.lineTo(-.5 + Math.cos(a) * 6.3, Math.sin(a) * 6.3 * s); }
    ctx.stroke(); ctx.globalAlpha = .9; ctx.lineWidth = .9; ctx.strokeStyle = P.outline; ctx.beginPath(); ctx.moveTo(-2.6, 0); ctx.lineTo(.2, 0); ctx.stroke();  // fovea
    const rim = ctx.createRadialGradient(2, 0, 5, 2, 0, 9.5); rim.addColorStop(0, "rgba(0,0,0,0)"); rim.addColorStop(1, "rgba(10,6,3,.55)");  // the carapace rounds away at its edge
    ctx.globalAlpha = 1; ctx.fillStyle = rim; ctx.fillRect(-8, -9, 20, 18);
    ctx.restore();
    // eyes: wolf-spider rows. Four small in front, two big forward-facing ones (with green eyeshine), two behind
    for (const [x, y, r, shine] of [[9.8, -2.3, .5], [10, -.8, .62], [10, .8, .62], [9.8, 2.3, .5], [7.7, -1.95, 1.4, 1], [7.7, 1.95, 1.4, 1], [4.9, -3.3, 1.05, 1], [4.9, 3.3, 1.05, 1]]) {
      ctx.fillStyle = "#090605"; ctx.beginPath(); ctx.arc(x, y, r * 1.18, 0, 6.283); ctx.fill();
      const e = ctx.createRadialGradient(x - L.x * r * .3, y - L.y * r * .3, 0, x, y, r);
      e.addColorStop(0, shine ? "#2f3d2a" : "#1c1612"); e.addColorStop(1, "#050303");
      ctx.fillStyle = e; ctx.beginPath(); ctx.arc(x, y, r, 0, 6.283); ctx.fill();
      ctx.fillStyle = "rgba(255,255,255,.9)"; ctx.beginPath(); ctx.arc(x + L.x * r * .42, y + L.y * r * .42, r * .28, 0, 6.283); ctx.fill();
    }
    ctx.restore(); ctx.globalAlpha = 1;
  }
  destroy() { cancelAnimationFrame(this.raf); this.ro.disconnect(); }
}
