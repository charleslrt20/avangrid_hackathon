// Task 3.2: build the submission deck in the Avangrid Hackathon Kickoff design system.
//   python scripts/11_deck_facts.py && node deck/build_deck.js
// Every number on a slide comes from deck/facts.json (written from outputs/); figures from docs/figures/.
const fs = require("fs");
const path = require("path");
const pptxgen = require("pptxgenjs");

const ROOT = path.resolve(__dirname, "..");
const F = JSON.parse(fs.readFileSync(path.join(__dirname, "facts.json"), "utf8"));
const TEAM = fs.existsSync(path.join(__dirname, "team.json"))
  ? JSON.parse(fs.readFileSync(path.join(__dirname, "team.json"), "utf8"))
  : { number: "", name: "", members: [], repo: "" };
const OUT = path.join(__dirname, TEAM.number && TEAM.name
  ? `${TEAM.number}_${TEAM.name}_Final Submission.pptx` : "Final Submission.pptx");

// ---------------------------------------------------------------- design system (from the kickoff deck)
const K = {
  green: "008C38", dkgreen: "003D1E", bright: "00A443", orange: "FF9C1A", blue: "0DA9FF",
  text: "3A3734", muted: "6B6661", cream: "FFFAF0", row1: "CBDBCE", row2: "E7EEE8", pill: "007F33", white: "FFFFFF",
};
const HEAD = "Arial", BODY = "Calibri", MATH = "Cambria";
const W = 13.333, H = 7.5, M = 0.55, CT = 1.95, CB = 6.8;
const THEME = {
  name: "Avangrid Hackathon 2026", headFontFace: HEAD, bodyFontFace: BODY,
  colors: { dk1: K.text, lt1: K.white, dk2: K.dkgreen, lt2: K.cream, accent1: K.green, accent2: K.orange,
            accent3: K.blue, accent4: K.dkgreen, accent5: K.bright, accent6: K.pill, hlink: K.green, folHlink: K.pill },
};

// ---------------------------------------------------------------- number formatting (all inputs from facts.json)
const n0 = (x) => Math.round(x).toLocaleString("en-US");
const n1 = (x) => x.toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const n2 = (x) => x.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
// toFixed rounds the exact binary value, like Python's format (0.205 -> "20%", as in the docs and app)
const pct = (x, d = 0) => (parseFloat(x.toFixed(d + 2)) * 100).toFixed(d) + "%";
const usd = (x) => "$" + n0(x);
const G = F.global, D = F.data, CL = F.classify, V = F.validation, NMF = F.nmf, BA = F.bess_assumptions;
const seg = Object.fromEntries(F.segments.map((s) => [s.segment, s]));
const tier = Object.fromEntries(F.bess_tiers.map((t) => [t.tier, t]));
const variant = Object.fromEntries(F.bess_variants.map((v) => [v.variant, v]));
const fb30 = F.future_bess.find((r) => Math.abs(r.hp_share - 0.3) < 1e-9);
const T1 = tier["Tier 1"];
const APP = Object.fromEntries(F.appliance_peak.map((r) => [r.group, r]));
const EH = seg["Electric-heat home"], EL = seg["Electrified: EV + electric heat"], TY = seg["Typical"],
      SP = seg["Summer peak contributor"], EVH = seg["EV home"];

// ---------------------------------------------------------------- deck + layouts
const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";
pres.theme = { headFontFace: HEAD, bodyFontFace: BODY };
pres.author = TEAM.name || "Hackathon team";
pres.title = "AMI electrification and behind-the-meter battery framework";

const asset = (f) => path.join(__dirname, "assets", f);
const figure = (f) => path.join(ROOT, "docs", "figures", f);
function pngSize(file) {
  const b = fs.readFileSync(file);
  return { w: b.readUInt32BE(16), h: b.readUInt32BE(20) };
}
const LEAF = { w: 0.6, h: 0.6 * 284 / 344 };
const footerText = `${TEAM.number ? "Team " + TEAM.number + (TEAM.name ? " · " + TEAM.name + " · " : " · ") : ""}2026 Energy Innovation Hackathon`;

pres.defineSlideMaster({
  title: "CONTENT",
  background: { color: K.white },
  objects: [
    { image: { x: W - M - LEAF.w, y: 0.32, w: LEAF.w, h: LEAF.h, path: asset("leaf_mark.png") } },
    { text: { text: footerText, options: { x: M, y: 7.02, w: 8, h: 0.3, fontFace: BODY, fontSize: 9, color: K.muted, margin: 0 } } },
    { placeholder: { options: { name: "kicker", type: "body", x: M, y: 0.3, w: 10.5, h: 0.3, fontFace: BODY, fontSize: 12,
                                bold: true, color: K.pill, charSpacing: 1.5, margin: 0, valign: "middle", align: "left" }, text: "" } },
    { placeholder: { options: { name: "title", type: "title", x: M, y: 0.6, w: 11.4, h: 0.68, fontFace: HEAD, fontSize: 28,
                                bold: true, color: K.green, margin: 0, valign: "middle", align: "left" }, text: "" } },
    { placeholder: { options: { name: "subtitle", type: "body", x: M, y: 1.3, w: 12.2, h: 0.5, fontFace: BODY, fontSize: 16,
                                color: K.text, margin: 0, valign: "top", align: "left" }, text: "" } },
  ],
  slideNumber: { x: W - M - 0.6, y: 7.02, w: 0.6, h: 0.3, fontFace: BODY, fontSize: 9, color: K.muted, align: "right" },
});
pres.defineSlideMaster({ title: "COVER", background: { color: K.cream }, objects: [] });
pres.defineSlideMaster({
  title: "STATEMENT",
  background: { color: K.green },
  objects: [{ text: { text: footerText, options: { x: M, y: 7.02, w: 8, h: 0.3, fontFace: BODY, fontSize: 9, color: K.white, margin: 0 } } }],
  slideNumber: { x: W - M - 0.6, y: 7.02, w: 0.6, h: 0.3, fontFace: BODY, fontSize: 9, color: K.white, align: "right" },
});

// ---------------------------------------------------------------- helpers
let currentSection = null;
function section(title) { pres.addSection({ title }); currentSection = title; }
const TITLE_MAX = 53;
function content(kicker, title, subtitle, notes) {
  if (title.length > TITLE_MAX) console.warn(`WARN title over ${TITLE_MAX} chars (${title.length}): ${title}`);
  if (subtitle && subtitle.length > 130) console.warn(`WARN subtitle may wrap (${subtitle.length}): ${subtitle}`);
  const s = pres.addSlide({ masterName: "CONTENT", sectionTitle: currentSection });
  s.addText(kicker.toUpperCase(), { placeholder: "kicker" });
  s.addText(title, { placeholder: "title" });
  if (subtitle) s.addText(subtitle, { placeholder: "subtitle" });
  if (notes) s.addNotes(notes);
  return s;
}
// place a PNG inside a box without distortion; align: "c" centre, "l" left/top
function fig(s, file, x, y, w, h, align = "c", name) {
  const sz = pngSize(file);
  const r = Math.min(w / (sz.w / 300), h / (sz.h / 300), 99);
  let fw = w, fh = w * sz.h / sz.w;
  if (fh > h) { fh = h; fw = h * sz.w / sz.h; }
  const fx = align === "c" ? x + (w - fw) / 2 : x;
  const fy = align === "c" ? y + (h - fh) / 2 : y;
  s.addImage({ path: file, x: fx, y: fy, w: fw, h: fh, altText: name || path.basename(file, ".png"), objectName: name });
  return { x: fx, y: fy, w: fw, h: fh, r };
}
// stat tile: big number + label on a rounded brand-colour square
function tile(s, x, y, w, h, value, label, fill, opts = {}) {
  const dark = [K.orange, K.blue, K.row2, K.row1].includes(fill);
  const fg = dark ? K.dkgreen : K.white;
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h, fill: { color: fill }, line: { color: fill }, rectRadius: 0.12 });
  s.addText(value, { x: x + 0.18, y: y + 0.12, w: w - 0.36, h: opts.valueH || h * 0.48, fontFace: HEAD, bold: true,
                     fontSize: opts.valueSize || 30, color: fg, margin: 0, valign: "bottom", fit: "shrink", isTextBox: true });
  s.addText(label, { x: x + 0.18, y: y + 0.14 + (opts.valueH || h * 0.48), w: w - 0.36, h: h - 0.26 - (opts.valueH || h * 0.48),
                     fontFace: BODY, fontSize: opts.labelSize || 13, color: fg, margin: 0, valign: "top", isTextBox: true });
}
// dark-green pill with white text (kickoff callout)
function pill(s, x, y, w, h, text, size = 14) {
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h, fill: { color: K.pill }, line: { color: K.pill }, rectRadius: 0.12 });
  s.addText(text, { x: x + 0.2, y, w: w - 0.4, h, fontFace: BODY, fontSize: size, color: K.white, margin: 0, valign: "middle", isTextBox: true });
}
// light card with a bold heading and body runs
function card(s, x, y, w, h, heading, body, opts = {}) {
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h, fill: { color: opts.fill || K.row2 }, line: { color: opts.fill || K.row2 }, rectRadius: 0.1 });
  s.addText(heading, { x: x + 0.2, y: y + 0.14, w: w - 0.4, h: 0.38, fontFace: HEAD, bold: true, fontSize: opts.headSize || 15,
                       color: K.green, margin: 0, valign: "top", isTextBox: true });
  const runs = typeof body === "string" ? [{ text: body }] : body;
  s.addText(runs, { x: x + 0.2, y: y + 0.56, w: w - 0.4, h: h - 0.7, fontFace: BODY, fontSize: opts.size || 14, color: K.text,
                    margin: 0, valign: "top", paraSpaceAfter: 4, isTextBox: true });
}
function bullets(items, size = 15) {
  return items.map((t, i) => {
    const runs = typeof t === "string" ? [{ text: t }] : t;
    return runs.map((r, j) => ({ text: r.text, options: { ...(r.options || {}), bullet: j === 0 ? { indent: 16 } : undefined,
      breakLine: j === runs.length - 1 && i < items.length - 1, fontSize: size } }));
  }).flat();
}
const b = (text) => ({ text, options: { bold: true } });
const t_ = (text) => ({ text });
// equation runs: "x_{i}" -> subscript, "x^{2}" -> superscript
function eq(src, size = 17, face = MATH) {
  const runs = [];
  const re = /([_^])\{([^}]*)\}/g;
  let last = 0, m;
  while ((m = re.exec(src))) {
    if (m.index > last) runs.push({ text: src.slice(last, m.index), options: { fontFace: face, fontSize: size } });
    runs.push({ text: m[2], options: { fontFace: face, fontSize: size, [m[1] === "_" ? "subscript" : "superscript"]: true } });
    last = re.lastIndex;
  }
  if (last < src.length) runs.push({ text: src.slice(last), options: { fontFace: face, fontSize: size } });
  return runs;
}
function table(s, header, rows, x, y, w, colW, opts = {}) {
  const fs_ = opts.size || 12;
  const head = header.map((h) => ({ text: h, options: { bold: true, color: K.white, fill: { color: K.green }, fontFace: BODY, fontSize: fs_ + 1 } }));
  const body = rows.map((r, i) => r.map((c) => ({ text: String(c), options: { color: K.text, fill: { color: i % 2 ? K.row2 : K.row1 }, fontFace: BODY, fontSize: fs_ } })));
  s.addTable([head, ...body], { x, y, w, colW, border: { type: "solid", pt: 1.5, color: K.white }, margin: [3, 6, 3, 6],
                                 rowH: opts.rowH || 0.36, valign: "middle", autoPage: false });
}

// ================================================================= SLIDES
// 1 Cover --------------------------------------------------------
section("Introduction");
{
  const s = pres.addSlide({ masterName: "COVER", sectionTitle: currentSection });
  s.addImage({ path: asset("tile_plug.png"), x: 0.75, y: 0, w: 1.05, h: 1.05 * 400 / 280 });
  s.addImage({ path: asset("bulb_head.png"), x: 9.35, y: 0.45, w: 2.4, h: 2.4 * 560 / 500, transparency: 0 });
  s.addImage({ path: asset("tiles_wind_solar.png"), x: W - 2.55, y: 3.15, w: 2.55, h: 2.55 * 670 / 530 });
  s.addText("2026 Energy Innovation Hackathon", { x: 0.75, y: 1.75, w: 9, h: 0.55, fontFace: HEAD, bold: true, fontSize: 24, color: K.blue, margin: 0, isTextBox: true });
  s.addText("From smart-meter data to battery programs", { x: 0.75, y: 2.3, w: 9.4, h: 1.5, fontFace: HEAD, bold: true, fontSize: 44, color: K.green, margin: 0, valign: "top", isTextBox: true });
  s.addText(`Classifying appliances, segmenting ${n0(D.modeled)} homes and sizing behind-the-meter batteries from one year of hourly AMI`,
            { x: 0.75, y: 3.95, w: 8.6, h: 0.9, fontFace: BODY, fontSize: 20, color: K.dkgreen, margin: 0, valign: "top", isTextBox: true });
  const team = [TEAM.number ? `Team ${TEAM.number}` : "", TEAM.name].filter(Boolean).join(" · ");
  if (team) s.addText(team + (TEAM.members && TEAM.members.length ? "  |  " + TEAM.members.join(", ") : ""),
                      { x: 0.75, y: 5.0, w: 8.6, h: 0.4, fontFace: BODY, bold: true, fontSize: 16, color: K.text, margin: 0, isTextBox: true });
  s.addImage({ path: asset("avangrid_logo.png"), x: 0.75, y: 6.15, w: 3.6, h: 3.6 * 220 / 1340 });
  s.addNotes("Title. One year of hourly smart-meter data for 34k homes, turned into appliance inventories, segments and a battery program.");
}

// 2 Executive summary ---------------------------------------------
{
  const s = content("Executive summary", "Electrified homes drive the peak; target batteries",
    `What one year of hourly meter data says about ${n0(D.modeled)} Avangrid homes`,
    "Four numbers carry the story; the three actions are the program we recommend. Avoided buildout alone does not pay for a battery, so targeting matters.");
  const tw = (W - 2 * M - 3 * 0.3) / 4, th = 1.9;
  tile(s, M, CT, tw, th, n0(CL.ev_detected), `home EV chargers detected: ${n1(CL.ev_detected / CL.ev_implied)}x what the labels imply`, K.green);
  tile(s, M + (tw + 0.3), CT, tw, th, pct(EH.share_cp_w), `of the winter peak comes from ${pct(EH.share_customers)} of homes (electric heat)`, K.dkgreen);
  tile(s, M + 2 * (tw + 0.3), CT, tw, th, "≈" + pct(G.crossover_B), `heat-pump adoption makes the system peak in winter (${pct(G.crossover_B)}-${pct(G.crossover_A)})`, K.orange);
  tile(s, M + 3 * (tw + 0.3), CT, tw, th, n0(G.tier1_homes), `Tier 1 battery homes: ${n1(G.tier1_mw_s)} MW summer / ${n1(G.tier1_mw_w)} MW winter relief`, K.blue);
  s.addText("What we recommend", { x: M, y: 4.15, w: 6, h: 0.4, fontFace: HEAD, bold: true, fontSize: 18, color: K.green, margin: 0, isTextBox: true });
  const recs = [
    [b("Battery + managed-charging bundle "), t_(`for the ${n0(EL.tier12)} electrified Tier 1-2 homes (EV + electric heat): ${pct(EL.share_cp_w)} of the winter peak from ${pct(EL.share_customers)} of homes.`)],
    [b("Batteries with heat-pump conversion "), t_(`in electric-heat homes: the largest Tier 1 pool (${n0(EH.tier1)} homes, ${n1(EH.relief_w_mw_t12)} MW winter relief in Tier 1-2).`)],
    [b("Coordinate fleet recharge before heat pumps scale: "), t_(`uncoordinated overnight recharge at ${pct(G.hp_top)} adoption creates a ${n0(G.hp30_peak_after_uncoord_mw)} MW night peak; coordinated, the peak falls to ${n1(G.hp30_peak_after_coord_mw)} MW.`)],
  ];
  s.addText(bullets(recs, 15), { x: M, y: 4.6, w: 8.0, h: 2.1, fontFace: BODY, color: K.text, margin: 0, valign: "top", paraSpaceAfter: 6, isTextBox: true });
  pill(s, 8.95, 4.65, W - M - 8.95, 1.9,
       `Avoided buildout ≈ ${usd(G.tier1_usd)}/yr per Tier 1 home (mid placeholder $/kW-yr): target constrained feeders, electrification headroom and customer value, not the system average.`, 14);
}

// 3 The challenge and the data --------------------------------------
section("Data & method");
{
  const s = content("The challenge", "Hourly reads, one weather station, partial labels",
    "Three questions from the brief, answered from the data Avangrid provided",
    "Data facts. Delivered-only metering is why PV shows as midday zeros. Labels are a partial, non-random list, which shapes the whole classification design.");
  const tw = 2.35, th = 1.45, gx = 0.25, gy = 0.25;
  const tiles = [
    [n0(D.customers), "residential customers with AMI", K.green],
    [n0(D.hours), "hourly readings each (Sep 2025 - Aug 2026)", K.dkgreen],
    [`${n0(D.labels_ev)} · ${n0(D.labels_hp)} · ${n0(D.labels_pv)}`, "EV · heat-pump · PV labels present", K.orange],
    ["0 negatives", "delivered-only meters: PV exports clipped to 0", K.blue],
    [n0(D.customers - D.modeled), `excluded: ${n0(D.excluded_stuck)} stuck, ${n0(D.excluded_short)} short, ${n0(D.excluded_error)} error`, K.row1],
    [n0(D.very_high_load_kept), "very-high-load accounts reviewed and kept", K.row2],
  ];
  tiles.forEach(([v, l, c], i) => tile(s, M + (i % 3) * (tw + gx), CT + Math.floor(i / 3) * (th + gy), tw, th, v, l, c, { valueSize: i === 2 ? 20 : 24, labelSize: 12 }));
  const x0 = M + 3 * (tw + gx) + 0.25, w0 = W - M - x0;
  const qs = [
    ["1  Classify appliances", "EV (L1 / L2), heat pump vs resistive heat, AC, PV, pool, water heater, hot tub"],
    ["2  Segment customers", "load behaviour, what drives it, who drives the summer and winter peaks"],
    ["3  Size batteries and flexibility", "candidates, sizing, value as avoided buildout cost; today and in a winter-peaking future"],
  ];
  qs.forEach(([h, t], i) => card(s, x0, CT + i * 1.12, w0, 1.02, h, t, { size: 13, headSize: 15 }));
  s.addText("Context data: one hourly weather series (temperature, humidity, wind). No geography, tariff or demographics, so 'what drives what' means weather → load, appliance → load shape, segment → system peak.",
            { x: M, y: 5.45, w: W - 2 * M, h: 0.7, fontFace: BODY, fontSize: 13, italic: true, color: K.muted, margin: 0, isTextBox: true });
}

// 4 Pipeline --------------------------------------------------------
{
  const s = content("Our approach", "One reproducible pipeline from raw reads to programs",
    "bash scripts/run_all.sh rebuilds every table, figure and number in this deck",
    "Six stages. Rules we held to: thresholds come from physics or score gaps, labels are only used to check, every number in the deck is traced to an output table.");
  const steps = [
    ["Clean & check", "stuck meters, spikes, high-load review, metering type", K.green],
    ["Physics features", `${n0(CL.n_features)} explainable features per home: weather slopes, events, shape`, K.dkgreen],
    ["Classify", "physics gates + PU ranking; heat pump vs resistive by response shape", K.bright],
    ["Segment & drivers", "rule segments, K-Means archetypes, NMF parts, peak shares", K.pill],
    ["Batteries & DR", "hour-by-hour SOC dispatch, sizing, avoided buildout, tiers", K.orange],
    ["Future & playbook", "heat-pump scenarios, 3 kVA lens, segment cards, app", K.blue],
  ];
  const bw = 1.82, gap = 0.26, y = CT + 0.1, bh = 2.35;
  steps.forEach(([h, t, c], i) => {
    const x = M + i * (bw + gap);
    const dark = [K.orange, K.blue].includes(c);
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w: bw, h: bh, fill: { color: c }, line: { color: c }, rectRadius: 0.12 });
    s.addText(String(i + 1), { x: x + 0.18, y: y + 0.12, w: 0.6, h: 0.55, fontFace: HEAD, bold: true, fontSize: 28, color: dark ? K.dkgreen : K.white, margin: 0, isTextBox: true });
    s.addText(h, { x: x + 0.18, y: y + 0.7, w: bw - 0.3, h: 0.6, fontFace: HEAD, bold: true, fontSize: 15, color: dark ? K.dkgreen : K.white, margin: 0, valign: "top", isTextBox: true });
    s.addText(t, { x: x + 0.18, y: y + 1.32, w: bw - 0.3, h: 0.95, fontFace: BODY, fontSize: 12, color: dark ? K.dkgreen : K.white, margin: 0, valign: "top", isTextBox: true });
    if (i < steps.length - 1) s.addShape(pres.shapes.RIGHT_ARROW,
      { x: x + bw + 0.04, y: y + bh / 2 - 0.1, w: 0.18, h: 0.2, fill: { color: K.muted }, line: { color: K.muted } });
  });
  const rules = [
    ["Physics first", "thresholds come from equipment ratings or natural gaps in a score, never from the labels"],
    ["Labels only check", "labels and the EIA RECS survey are used as checks that are allowed to fail"],
    ["Honest kWh", "batteries claim only the kW they can hold through the whole event window"],
  ];
  const cw = (W - 2 * M - 2 * 0.3) / 3;
  rules.forEach(([h, t], i) => card(s, M + i * (cw + 0.3), 4.75, cw, 1.6, h, t, { size: 14 }));
}

// 5 Equations ---------------------------------------------------------
{
  const s = content("Method", "The model in six equations",
    "Every flag, segment and battery number on the following slides comes from one of these",
    "Change-point regression per home; PU learning (Elkan-Noto) used as a ranking because estimated c is far from 0.25; NMF on normalised shapes; SOC-constrained dispatch; avoided cost; priority score.");
  const cw = (W - 2 * M - 0.3) / 2, ch = 1.5, gy = 0.18;
  const cards = [
    ["Weather response (per home, per day)",
     eq("kWh_{d} = β_{0} + β_{h}·HDD_{d}(T_{h}) + β_{c}·CDD_{d}(T_{c}) + β_{x}·HDD_{d}(20°F) + β_{w}·HDD·wind + β_{e}·weekend", 13),
     "β_{x} > 0: slope steepens in deep cold (heat-pump backup); resistive heat stays linear"],
    ["Positive-unlabeled ranking (Elkan-Noto)",
     eq("g(x) = P(labeled | x),   P(owner | x) = g(x) / c,   ĉ = mean g(x) over labeled", 15),
     `ĉ = ${CL.c_hat_ev.toFixed(3)} (EV), ${CL.c_hat_hp.toFixed(3)} (HP) vs ${CL.pu_c_brief} in the brief → labels not random: g used to rank only`],
    ["Load decomposition (NMF)",
     eq("S_{i} ≈ Σ_{j} w_{ij} H_{j},   w_{ij} ≥ 0,   Σ H_{j} = 1", 16),
     `S_{i} = 12-month × 24-hour shape summing to 1, so w_{ij} = share of energy; k = ${NMF.k} chosen without labels`],
    ["Battery state of charge (hour by hour)",
     eq("SOC_{t+1} = SOC_{t} + η·c_{t} − d_{t},   0 ≤ SOC ≤ E,   d_{t} ≤ min(P, load_{t})", 15),
     `E = ${n2(BA.kwh)} kWh, P = ${n2(BA.kw)} kW, η = ${pct(BA.rte, 1)}; recharge 00-05 h only, never above the home's own p95 load`],
    ["Avoided buildout ($/yr)",
     eq("V = p_{cap}·ΔkW_{peak} + p_{tx}·mean(ΔkW_{S}, ΔkW_{W}) + p_{dist}·max(ΔkW_{S}, ΔkW_{W})", 13),
     `mid $/kW-yr ${BA.avoided.capacity.mid} / ${BA.avoided.transmission.mid} / ${BA.avoided.distribution_deferral.mid} (placeholders); NPV over ${BA.years} yr at ${pct(BA.discount)}`],
    ["Battery priority score",
     eq("score = 0.45·rank(V) + 0.15·persist. + 0.15·coverage + 0.15·electrified + 0.10·PV", 13),
     `Tier 1 / 2 / 3 = top 10% / next 20% / next 30% of ${n0(G.bess_candidates_total)} candidates`],
  ];
  cards.forEach(([h, e, note], i) => {
    const x = M + (i % 2) * (cw + 0.3), y = CT - 0.05 + Math.floor(i / 2) * (ch + gy);
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w: cw, h: ch, fill: { color: K.row2 }, line: { color: K.row2 }, rectRadius: 0.1 });
    s.addText(h, { x: x + 0.2, y: y + 0.1, w: cw - 0.4, h: 0.34, fontFace: HEAD, bold: true, fontSize: 14, color: K.green, margin: 0, isTextBox: true });
    s.addText(e, { x: x + 0.2, y: y + 0.46, w: cw - 0.4, h: 0.5, color: K.dkgreen, margin: 0, valign: "middle", isTextBox: true });
    s.addText(eq(note, 12, BODY), { x: x + 0.2, y: y + 1.0, w: cw - 0.4, h: 0.45, color: K.muted, margin: 0, valign: "top", isTextBox: true });
  });
}

// 6 Appliance signatures (carpets) ------------------------------------
section("Appliances");
{
  const s = content("Appliance classification", "Physics signatures reveal appliances in hourly data",
    "One typical home per class (the medoid on the class's defining features): every hour of the year, Sep 2025 to Aug 2026",
    "Each panel is one real home (anonymised), hour of day vs day of year. L2 = bright late-night blocks; pool = summer timer band; PV = dark midday stripe in summer; AC = summer afternoons. Water heater and hot tub are low confidence at hourly resolution.");
  const f = fig(s, figure("exemplars_carpet_deck.png"), M, CT - 0.1, 9.3, CB - CT + 0.1, "l");
  const x0 = f.x + f.w + 0.35, w0 = W - M - x0;
  const inv = [
    [n0(CL.ev_detected), `EV home charging (${n0(CL.ev_l2)} L2, ${n0(CL.ev_l1)} L1)`, K.green],
    [n0(CL.eheat), `electric space heat (${n0(CL.hp_detected)} heat pump)`, K.dkgreen],
    [n0(CL.central_ac), "central AC", K.blue],
    [`${n0(CL.pv)} · ${n0(CL.pool)}`, "solar PV · pool pumps", K.orange],
  ];
  inv.forEach(([v, l, c], i) => tile(s, x0, CT - 0.1 + i * 1.18, w0, 1.05, v, l, c, { valueSize: 22, labelSize: 12, valueH: 0.42 }));
}

// 7 Heating type ------------------------------------------------------
{
  const s = content("Appliance classification", "The cold-weather response reveals the heating type",
    "Daily kWh vs temperature for three typical homes: the slope below 20 °F tells the technologies apart",
    "Heat pump: moderate slope that steepens a little in deep cold. HP + backup: slope more than doubles below 20 °F as resistance strips engage; also cools in summer. Resistive: steep and linear.");
  fig(s, figure("exemplars_heating.png"), M, CT - 0.1, W - 2 * M, 3.55, "c");
  const tw = (W - 2 * M - 3 * 0.3) / 4, y = 5.55, th = 1.2;
  tile(s, M, y, tw, th, n0(CL.hp_only), "heat pump: moderate slope, mild steepening", K.bright, { valueSize: 24, labelSize: 12, valueH: 0.45 });
  tile(s, M + (tw + 0.3), y, tw, th, n0(CL.hp_backup), "heat pump + backup: slope ≥ 2× below 20 °F", K.green, { valueSize: 24, labelSize: 12, valueH: 0.45 });
  tile(s, M + 2 * (tw + 0.3), y, tw, th, n0(CL.resistive), "resistive: steep, linear (COP = 1)", K.dkgreen, { valueSize: 24, labelSize: 12, valueH: 0.45 });
  tile(s, M + 3 * (tw + 0.3), y, tw, th, n0(CL.eheat_unclear), "electric, type unclear at hourly resolution", K.row1, { valueSize: 24, labelSize: 12, valueH: 0.45 });
}

// 8 EV ------------------------------------------------------------------
{
  const s = content("Appliance classification", "L2 and L1 charging leave different fingerprints",
    "Two weeks of a typical labeled L2 home and a typical labeled L1 home",
    "L2: 6-11 kW blocks of 2-5 hours, counted on mild days only so HVAC surges don't trigger it. L1: a flat 1.0-2.2 kWh overnight plateau above the home's own quiet-night level.");
  const f = fig(s, figure("exemplars_ev.png"), M, CT - 0.1, 8.7, CB - CT + 0.1, "l");
  const x0 = f.x + f.w + 0.35, w0 = W - M - x0;
  tile(s, x0, CT - 0.1, w0, 1.45, n0(CL.ev_l2), "L2 detectable: short high blocks ≥ 1 week in 4", K.green, { valueSize: 26, labelSize: 13 });
  tile(s, x0, CT + 1.55, w0, 1.45, n0(CL.ev_l1), "L1 detectable: long low overnight plateau", K.dkgreen, { valueSize: 26, labelSize: 13 });
  tile(s, x0, CT + 3.2, w0, 1.6, pct(CL.ev_label_recall), "of labeled EV homes show home charging: many charge away from home or bought mid-year", K.orange, { valueSize: 26, labelSize: 12 });
}

// 9 Labels finding ------------------------------------------------------
{
  const s = content("Key finding", "The labels are not a random sample, so physics leads",
    `Physics finds ${n1(CL.ev_detected / CL.ev_implied)}x the EV chargers the labels imply; PV labels match the meter signature`,
    `If labels were a random ${pct(CL.pu_c_brief)} (the brief), Elkan-Noto c would be about ${CL.pu_c_brief}. We estimate ${CL.c_hat_ev.toFixed(3)} and ${CL.c_hat_hp.toFixed(3)}. So we report physics-detected inventories and use PU only to rank. The PV list is complete: our zero-midday rule gets ${pct(CL.pv_precision)} precision, ${pct(CL.pv_label_recall)} recall and finds ${n0(CL.pv_unregistered)} unregistered systems.`);
  fig(s, figure("classification_summary.png"), M, CT - 0.15, W - 2 * M, 3.85, "c");
  const y = 5.85, h = 0.85, cw = (W - 2 * M - 2 * 0.3) / 3;
  pill(s, M, y, cw, h, `Elkan-Noto ĉ = ${CL.c_hat_ev.toFixed(3)} (EV), ${CL.c_hat_hp.toFixed(3)} (HP) vs ${CL.pu_c_brief}: PU used as a ranking`, 13);
  pill(s, M + cw + 0.3, y, cw, h, `EV ranking: bagged PU LightGBM, CV AUC ${n2(CL.ev_cv_auc_rank)} (benchmark 0.74)`, 13);
  pill(s, M + 2 * (cw + 0.3), y, cw, h, `PV rule: ${pct(CL.pv_precision)} precision, ${pct(CL.pv_label_recall)} recall, ${n0(CL.pv_unregistered)} unregistered systems`, 13);
}

// 10 Validation ----------------------------------------------------------
{
  const s = content("Rigor", "Flags are stable, seasonal and plausible",
    `${V.n_checks} validation checks: ${V.n_pass} pass, ${V.n_info} informational, ${V.n_fail} fail (3 designed to fail if labels were random, 3 survey gaps)`,
    "Stability: we refit on odd weeks and on even weeks (both halves cover every season) and compare flags with Cohen's kappa. Plausibility: EIA RECS 2020 New England, used only as a check.");
  const f = fig(s, figure("validation_stability_recs.png"), M, CT - 0.05, 8.75, CB - CT, "l");
  const x0 = f.x + f.w + 0.35, w0 = W - M - x0;
  const kap = V.kappa_flags.match(/median ([0-9.]+)/)[1];
  const items = [
    [kap, "median Cohen's κ, flags on odd vs even weeks", K.green],
    [V.seasonal.replace(" pass", ""), "seasonal checks: heating fires in winter, cooling in summer", K.dkgreen],
    [V.ev_oot_auc.split(" / ")[0], "EV PU AUC, out-of-customer and out-of-time", K.blue],
    [V.bess_violations, "battery power / SOC / export violations", K.orange],
  ];
  items.forEach(([v, l, c], i) => tile(s, x0, CT - 0.05 + i * 1.2, w0, 1.07, v, l, c, { valueSize: 24, labelSize: 12, valueH: 0.45 }));
}

// 11 Segments → peak ---------------------------------------------------------
section("Segments & drivers");
{
  const s = content("Segmentation", `${pct(EH.share_customers)} of homes carry ${pct(EH.share_cp_w)} of the winter peak`,
    "Share of customers vs share of the coincident summer and winter peak, by business segment (bars = 95% bootstrap CI)",
    "Rule-based business segments are the primary, actionable grouping; each customer gets the first rule that fires. Coincident peak = mean load in the system's top 88 hours of each season.");
  const f = fig(s, figure("peak_share_by_segment.png"), M, CT - 0.05, 8.6, CB - CT + 0.05, "l");
  const x0 = f.x + f.w + 0.35, w0 = W - M - x0;
  tile(s, x0, CT - 0.05, w0, 1.5, `${pct(EH.share_customers)} → ${pct(EH.share_cp_w)}`, "electric-heat homes: share of homes → share of winter peak", K.dkgreen, { valueSize: 26, labelSize: 12 });
  tile(s, x0, CT + 1.6, w0, 1.5, `${pct(SP.share_customers)} → ${pct(SP.share_cp_s)}`, `summer peak contributors (${pct(SP.central_ac)} central AC) → summer peak`, K.orange, { valueSize: 26, labelSize: 12 });
  tile(s, x0, CT + 3.25, w0, 1.5, `${pct(TY.share_customers)} → ${pct(TY.share_cp_s)}`, `typical homes → summer peak (${pct(TY.share_cp_w)} of winter)`, K.row1, { valueSize: 26, labelSize: 12 });
}

// 12 Drivers -----------------------------------------------------------------
{
  const s = content("What drives load", "Cold drives electric-heat homes; heat drives AC homes",
    "Average kW per home by outdoor temperature (left) and per appliance during the system peak hours (right)",
    "Weather is exogenous, so weather → load is causal. Appliance and segment → peak links are associations. Resistive heat is the single biggest winter-peak load per home.");
  const f = fig(s, figure("temperature_response.png"), M, CT - 0.05, 8.6, 3.9, "l");
  const x0 = f.x + f.w + 0.35, w0 = W - M - x0;
  s.addText("kW per home at the system peak", { x: x0, y: CT - 0.05, w: w0, h: 0.35, fontFace: HEAD, bold: true, fontSize: 13, color: K.green, margin: 0, isTextBox: true });
  [["Resistive heat", "winter", K.dkgreen], ["Heat pump (HP / HP+backup)", "winter", K.green], ["Pool pump", "summer", K.orange], ["EV (home charging)", "summer", K.blue]]
    .forEach(([g, season, c], i) => tile(s, x0, CT + 0.4 + i * 0.88, w0, 0.78, `${n1(APP[g][`mean_cp_kw_${season}`])} kW`,
      `${g.replace(" (HP / HP+backup)", "").replace(" (home charging)", "")}, ${season} peak`, c, { valueSize: 20, labelSize: 11, valueH: 0.34 }));
  pill(s, M, 6.05, W - 2 * M, 0.7,
       `Weather → load is causal (weather is exogenous). Appliance → peak is an association: e.g. resistive-heat homes average ${n1(APP["Resistive heat"].mean_cp_kw_winter)} kW at the winter peak.`, 14);
}

// 13 NMF ---------------------------------------------------------------------
{
  const s = content("Innovation", `Each home mixes ${NMF.k} load parts, found without labels`,
    "Non-negative matrix factorisation of 12-month × 24-hour load shapes; parts described before matching them to appliances",
    "k chosen label-free: the largest k whose parts all reappear when NMF is refit separately on odd and even weeks. At k = 8 a part appears in one half only. Two winter heating parts; L2 charging has no part because weekly blocks wash out in monthly means.");
  const f = fig(s, figure("nmf_components.png"), M, CT - 0.05, 8.4, CB - CT + 0.05, "l");
  const x0 = f.x + f.w + 0.35, w0 = W - M - x0;
  tile(s, x0, CT - 0.05, w0, 1.25, `k = ${NMF.k}`, `largest k reproduced on odd and even weeks (k = ${NMF.k + 1}: cosine ${n2(NMF.k8_min_cosine)})`, K.green, { valueSize: 24, labelSize: 12, valueH: 0.45 });
  tile(s, x0, CT + 1.35, w0, 1.25, `${pct(NMF.heat_share_eheat)} vs ${pct(NMF.heat_share_typical)}`, "winter heating parts: share of energy, electric-heat vs typical homes", K.dkgreen, { valueSize: 22, labelSize: 12, valueH: 0.45 });
  tile(s, x0, CT + 2.75, w0, 1.25, `AUC ${n2(NMF.l2_best_auc)}`, "L2 charging has no part: keep the event detector", K.orange, { valueSize: 22, labelSize: 12, valueH: 0.45 });
  s.addText("The evening 'PV' part is a metering artefact: clipped midday load shifts PV homes' measured energy to the evening.",
            { x: x0, y: CT + 4.15, w: w0, h: 0.75, fontFace: BODY, fontSize: 12, italic: true, color: K.muted, margin: 0, valign: "top", isTextBox: true });
}

// 14 Battery dispatch ------------------------------------------------------------
section("Batteries & flexibility");
{
  const s = content("Battery method", "Batteries run hour by hour within their energy limit",
    `2 × Enphase IQ 10C (${n2(BA.kwh)} kWh, ${n2(BA.kw)} kW, ${pct(BA.rte, 1)} round trip), simulated over the full year for ${n0(G.bess_candidates_total)} candidate homes`,
    "Screenshot from our app: a typical L2 EV home through the February cold snap. The battery discharges flat across the called window, recharges overnight up to its own p95 load, and carries state of charge from day to day.");
  const f = fig(s, asset("app_dispatch_winter_crop.png"), M, CT - 0.05, 8.0, CB - CT + 0.05, "l");
  const x0 = f.x + f.w + 0.4, w0 = W - M - x0;
  const rules = [
    [b("Flat discharge "), t_(`≤ ${n2(BA.kw)} kW across the event window, never more than the home's load (no export)`)],
    [b("Overnight recharge "), t_("00-05 h only, never above the home's own p95 load (no new peak)")],
    [b("Multi-day events: "), t_("state of charge carries across back-to-back hot or cold days")],
    [b("Realistic calls: "), t_(`${BA.events} events per season on forecast hottest / coldest days`)],
    [b("Claim firm kW, "), t_("not nameplate: the level held through the whole window")],
  ];
  s.addText(bullets(rules, 14), { x: x0, y: CT, w: w0, h: 3.6, fontFace: BODY, color: K.text, margin: 0, valign: "top", paraSpaceAfter: 8, isTextBox: true });
  pill(s, x0, 5.7, w0, 1.0, `Realistic dispatch delivers ${V.realistic_vs_perfect.replace(" / ", " (summer) / ")} (winter) of perfect-foresight relief`, 13);
}

// 15 Battery results ---------------------------------------------------------------
{
  const s = content("Battery results", `${n0(T1.customers)} Tier 1 homes cut ${n1(T1.battery_relief_mw_summer)} MW summer, ${n1(T1.battery_relief_mw_winter)} MW winter`,
    "Coincident-peak relief by tier (left) and what the kWh limit and realistic dispatch cost for Tier 1 (right)",
    `Tier 1 is ${pct(T1.share_electrified, 1)} electrified homes. Perfect foresight is the upper bound; one unit and a backup reserve both cost relief; demand flex alone, after rebound, gives about half of a battery.`);
  fig(s, figure("bess_tiers.png"), M, CT - 0.05, 5.6, 3.55, "c");
  fig(s, figure("bess_variants.png"), M + 5.9, CT + 0.2, W - 2 * M - 5.9, 3.1, "c");
  const sz = F.bess_sizing_tier1;
  const tw = (W - 2 * M - 3 * 0.3) / 4, y = 5.6, th = 1.15;
  tile(s, M, y, tw, th, n0(sz["2 units"]), "Tier 1 homes: 2 units cover their peaks", K.green, { valueSize: 22, labelSize: 12, valueH: 0.42 });
  tile(s, M + (tw + 0.3), y, tw, th, n0(sz["2 units + DR (battery alone insufficient)"]), "need 2 units plus demand flex", K.dkgreen, { valueSize: 22, labelSize: 12, valueH: 0.42 });
  tile(s, M + 2 * (tw + 0.3), y, tw, th, n0(sz["1 unit"]), "fully covered by 1 unit", K.blue, { valueSize: 22, labelSize: 12, valueH: 0.42 });
  tile(s, M + 3 * (tw + 0.3), y, tw, th, `${n2(T1.kw_per_home_summer)} / ${n2(T1.kw_per_home_winter)} kW`, "relief per Tier 1 home (summer / winter)", K.orange, { valueSize: 22, labelSize: 12, valueH: 0.42 });
}

// 16 Economics -----------------------------------------------------------------------
{
  const s = content("Business value", "Avoided buildout alone does not pay for a battery",
    "So the program should target where value stacks: constrained feeders, electrification headroom, customer value",
    `At mid placeholder rates a Tier 1 home avoids about ${usd(T1.avoided_usd_per_home_yr_mid)}/yr of buildout, NPV about ${usd(T1.npv_usd_per_home_mid)} over ${BA.years} years. That covers only part of a two-unit installation. Distribution deferral is location-specific and can be far above the system average.`);
  const tw = 2.8, th = 1.55;
  tile(s, M, CT, tw, th, usd(T1.avoided_usd_per_home_yr_mid) + "/yr", "avoided buildout per Tier 1 home (mid)", K.green, { valueSize: 26, labelSize: 12 });
  tile(s, M, CT + th + 0.25, tw, th, usd(T1.npv_usd_per_home_mid), `NPV per Tier 1 home over ${BA.years} years at ${pct(BA.discount)}`, K.dkgreen, { valueSize: 26, labelSize: 12 });
  tile(s, M, CT + 2 * (th + 0.25), tw, th, `$${n1(T1.avoided_musd_yr_low)}-${n1(T1.avoided_musd_yr_high)}M`, "per year for all Tier 1 homes (low-high)", K.orange, { valueSize: 24, labelSize: 12 });
  const x0 = M + tw + 0.4, w0 = W - M - x0;
  s.addText("Avoided-cost assumptions ($/kW-yr, placeholders pending Avangrid's values)", { x: x0, y: CT, w: w0, h: 0.35, fontFace: HEAD, bold: true, fontSize: 14, color: K.green, margin: 0, isTextBox: true });
  const A = BA.avoided;
  table(s, ["Component", "Low", "Mid", "High", "Applied to"], [
    ["Capacity", A.capacity.low, A.capacity.mid, A.capacity.high, "relief at the annual system peak"],
    ["Transmission", A.transmission.low, A.transmission.mid, A.transmission.high, "mean of summer and winter relief"],
    ["Distribution deferral", A.distribution_deferral.low, A.distribution_deferral.mid, A.distribution_deferral.high, "max of summer and winter relief"],
  ], x0, CT + 0.45, w0, [2.2, 0.8, 0.8, 0.8, w0 - 4.6], { size: 13, rowH: 0.42 });
  const stack = [
    ["Constrained feeders", "distribution deferral is local: place batteries where an upgrade is deferred"],
    ["Electrification headroom", "keep heat-pump and EV adopters inside 3 kVA without a service upgrade"],
    ["Customer value", "backup power and TOU bill savings stack on top of grid value"],
  ];
  const cw = (w0 - 2 * 0.25) / 3;
  stack.forEach(([h, t], i) => card(s, x0 + i * (cw + 0.25), 4.15, cw, 2.1, h, t, { size: 13, headSize: 14 }));
}

// 17 Future crossover -----------------------------------------------------------------
section("The winter future");
{
  const s = content("Forward look", `Winter overtakes summer at ~${pct(G.crossover_B)} heat pumps`,
    "System peak vs share of homes adding a heat pump, built from the measured heating response of labeled heat-pump homes",
    `Group B (headline) uses labeled HP homes with an electric-heat signature. Group C (label-free) and A (partial displacement) bracket the answer. Today the winter peak is ${pct(G.winter_over_summer_peak)} of summer.`);
  const f = fig(s, figure("future_peak_crossover.png"), M, CT - 0.05, 7.9, CB - CT + 0.05, "l");
  const x0 = f.x + f.w + 0.4, w0 = W - M - x0;
  tile(s, x0, CT - 0.05, w0, 1.15, pct(G.winter_over_summer_peak), `today's winter peak vs summer (${n1(G.peak_winter_mw)} vs ${n1(G.peak_summer_mw)} MW)`, K.row1, { valueSize: 24, labelSize: 12, valueH: 0.45 });
  tile(s, x0, CT + 1.25, w0, 1.15, pct(G.crossover_B), `heat-pump adoption where winter overtakes summer (range ${pct(G.crossover_B)}-${pct(G.crossover_A)} across donor groups)`, K.green, { valueSize: 24, labelSize: 12, valueH: 0.45 });
  tile(s, x0, CT + 2.55, w0, 1.15, `${n1(G.hp30_winter_mw)} MW`, `winter peak at ${pct(G.hp_top)} adoption (summer ${n1(G.hp30_summer_mw)} MW)`, K.dkgreen, { valueSize: 24, labelSize: 12, valueH: 0.45 });
  tile(s, x0, CT + 3.85, w0, 1.0, `+${n1(G.hp_added_kw_per_adopter)} kW`, "per adopting home at the winter peak", K.blue, { valueSize: 22, labelSize: 12, valueH: 0.4 });
}

// 18 Cold snap ----------------------------------------------------------------------
{
  const s = content("Forward look", "A winter peak demands coordinated battery recharge",
    "The coldest peak day under each adoption scenario (left) and the battery energy needed per MW of peak cut (right)",
    "Winter events are longer, with morning and evening peaks, so kWh limits bind harder. If every battery recharges overnight at once, it sets a new night peak above the winter peak itself.");
  fig(s, figure("future_winter_day.png"), M, CT - 0.05, W - 2 * M, 3.75, "c");
  const tw = (W - 2 * M - 2 * 0.3) / 3, y = 5.55, th = 1.2;
  tile(s, M, y, tw, th, `${n0(G.hp30_peak_after_uncoord_mw)} MW`, `night peak if batteries recharge uncoordinated (${pct(G.hp_top)} adoption)`, K.orange, { valueSize: 26, labelSize: 12, valueH: 0.48 });
  tile(s, M + tw + 0.3, y, tw, th, `${n1(G.hp30_winter_mw)} → ${n1(G.hp30_peak_after_coord_mw)} MW`, "annual peak with coordinated fleet recharge", K.green, { valueSize: 26, labelSize: 12, valueH: 0.48 });
  tile(s, M + 2 * (tw + 0.3), y, tw, th, `${n2(fb30.battery_winter_kw_coordinated)} kW`, "winter relief per adopter home, coordinated", K.dkgreen, { valueSize: 26, labelSize: 12, valueH: 0.48 });
}

// 19 3 kVA ----------------------------------------------------------------------------
{
  const s = content("Electrification enablement", "Batteries keep most overloaded adopters within 3 kVA",
    "Heat-pump adopters exceeding their old peak + 3 kVA, without and with a battery (left); winter relief per adopter (right)",
    "The 3 kVA rule from the brief: incremental load above 3 kVA would need a 200 A service upgrade. One unit brings two thirds of the exceeding homes back within the cap.");
  fig(s, figure("future_headroom_bess.png"), M, CT - 0.05, W - 2 * M, 3.6, "c");
  const tw = (W - 2 * M - 2 * 0.3) / 3, y = 5.5, th = 1.25;
  tile(s, M, y, tw, th, pct(G.kva_exceed), `of adopters exceed old peak + 3 kVA (median ${n0(G.kva_hours)} h/yr)`, K.orange, { valueSize: 28, labelSize: 12, valueH: 0.5 });
  tile(s, M + tw + 0.3, y, tw, th, pct(G.kva_fix_1), "of those brought within the cap by 1 unit", K.green, { valueSize: 28, labelSize: 12, valueH: 0.5 });
  tile(s, M + 2 * (tw + 0.3), y, tw, th, pct(G.kva_fix_2), "brought within the cap by 2 units", K.dkgreen, { valueSize: 28, labelSize: 12, valueH: 0.5 });
}

// 20 Playbook ----------------------------------------------------------------------------
section("From insight to action");
{
  const s = content("Program playbook", "A program for every segment, sized in homes and MW",
    "Now = launch with today's data · Next = after a pilot or confirmed avoided-cost values · Watch = monitor, no offer yet",
    "Rendered from the same facts as our segment cards; battery relief summed over each segment's Tier 1 and Tier 2 homes, realistic dispatch.");
  const rows = F.playbook.map((r) => [r.when, r.segment, r.program, r.target, r.mw]);
  table(s, ["When", "Segment", "Program", "Target", "Tier 1+2 MW, S / W"], rows, M, CT - 0.1, W - 2 * M,
        [0.75, 2.8, 4.55, 2.25, W - 2 * M - 10.35], { size: 12, rowH: 0.4 });
}

// 21 Demo -------------------------------------------------------------------------------
{
  const s = content("The tool", "A local app shows every home's evidence in one click",
    "streamlit run app/streamlit_app.py · four views on cached outputs, each under 0.2 s · customer IDs anonymised",
    "Overview with the playbook; customer lookup with load carpet, weather response, 'why flagged' evidence and battery dispatch with SOC; segment explorer; battery ranking with filters.");
  const shots = [["app_why_flagged_crop.png", "Customer lookup · why flagged: each appliance's evidence vs the population median"],
                 ["app_battery_ranking_crop.png", "Battery ranking: filter by tier, segment and size; select a home to see its dispatch"]];
  const cw = (W - 2 * M - 0.3) / 2;
  shots.forEach(([file, cap], i) => {
    const x = M + i * (cw + 0.3);
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y: CT - 0.05, w: cw, h: 4.0, fill: { color: K.row2 }, line: { color: K.row2 }, rectRadius: 0.1 });
    fig(s, asset(file), x + 0.15, CT + 0.1, cw - 0.3, 3.15, "c");
    s.addText(cap, { x: x + 0.2, y: CT + 3.3, w: cw - 0.4, h: 0.55, fontFace: BODY, bold: true, fontSize: 13, color: K.dkgreen, margin: 0, valign: "middle", isTextBox: true });
  });
  pill(s, M, 6.15, W - 2 * M, 0.6, "Repository: pipeline (scripts/run_all.sh), app, README with setup and run steps, requirements.txt" + (TEAM.repo ? " · " + TEAM.repo : ""), 14);
}

// 22 Limitations & next steps -----------------------------------------------------------
{
  const s = content("Next steps", "What to confirm with Avangrid, and where to go next",
    "Our assumptions are explicit and parameterised; these answers would sharpen the numbers",
    "Placeholders: avoided-cost $/kW-yr and TOU tariff. Label provenance decides how to frame PU. Feeder IDs would turn the system-average battery value into location-specific value.");
  const cw = (W - 2 * M - 0.3) / 2;
  card(s, M, CT, cw, 3.15, "Confirm with Avangrid", bullets([
    [b("Label provenance: "), t_("how were EV / HP labels collected? They are not a random sample")],
    [b("Metering: "), t_("delivered-only confirmed by zero negatives; is a net channel available?")],
    [b("Avoided-cost values: "), t_("capacity, transmission and distribution $/kW-yr; TOU tariff")],
    [b("Hourly limits: "), t_("water heater, hot tub and HP cycling need sub-hourly data")],
  ], 14), { size: 14, headSize: 17 });
  card(s, M + cw + 0.3, CT, cw, 3.15, "Next steps", bullets([
    [b("Pilot "), t_(`a battery + managed-charging bundle with the ${n0(EL.tier1)} Tier 1 electrified homes`)],
    [b("Add feeder / transformer IDs "), t_("to value distribution deferral by location")],
    [b("Re-score quarterly "), t_("as new AMI arrives: emerging-load homes become tomorrow's EV / HP homes")],
    [b("Coordinate fleet recharge "), t_("before heat-pump adoption approaches the winter crossover")],
  ], 14), { size: 14, headSize: 17 });
  pill(s, M, 5.45, W - 2 * M, 1.0, "Every assumption (battery spec, event budget, avoided-cost $/kW-yr low / mid / high, tariff) lives in src/avangrid/config.py, so new values from Avangrid rerun the whole pipeline and this deck.", 15);
}

// 23 Closing -----------------------------------------------------------------------------
{
  const s = pres.addSlide({ masterName: "COVER", sectionTitle: currentSection });
  s.addImage({ path: asset("tiles_wind_solar.png"), x: W - 2.55, y: 2.6, w: 2.55, h: 2.55 * 670 / 530 });
  s.addText("Thank you", { x: 0.75, y: 2.2, w: 8, h: 1.0, fontFace: HEAD, bold: true, fontSize: 48, color: K.green, margin: 0, isTextBox: true });
  s.addText([
    { text: "Electrified homes drive the peak, and the winter peak is coming. ", options: { bold: true, breakLine: true } },
    { text: "Target batteries where value stacks, pair them with managed charging and heat-pump programs, and coordinate the fleet before heat pumps scale." },
  ], { x: 0.75, y: 3.3, w: 8.6, h: 1.4, fontFace: BODY, fontSize: 18, color: K.dkgreen, margin: 0, valign: "top", isTextBox: true });
  if (TEAM.repo) s.addText(TEAM.repo, { x: 0.75, y: 4.85, w: 8.6, h: 0.4, fontFace: BODY, fontSize: 14, color: K.text, margin: 0, isTextBox: true });
  s.addImage({ path: asset("avangrid_logo.png"), x: 0.75, y: 6.15, w: 3.6, h: 3.6 * 220 / 1340 });
}

// Appendix ---------------------------------------------------------------------------------
section("Appendix");
{
  const s = content("Appendix", "Segment sizes and load-shape archetypes",
    "Rule-based segments (left); K-Means archetypes on normalised seasonal shapes form a continuum (right)",
    `K-Means silhouette and Davies-Bouldin: ${V.silhouette}. Shapes form a continuum, which is why we added NMF.`);
  fig(s, figure("segments.png"), M, CT, 5.6, 3.6, "c");
  fig(s, figure("archetypes.png"), M + 5.85, CT, W - 2 * M - 5.85, 3.6, "c");
  pill(s, M, 6.0, W - 2 * M, 0.7, `K-Means silhouette / Davies-Bouldin = ${V.silhouette}: archetypes on a continuum, not separable clusters, so the rule segments are primary.`, 14);
}
{
  const s = content("Appendix", "Electrification fingerprint by segment",
    "Mean share of each customer's annual energy explained by each NMF part",
    `Electric-heat homes: about ${pct(NMF.heat_share_eheat)} of energy in the winter heating parts. Emerging-load homes carry more year-round daytime and summer overnight load than typical homes.`);
  fig(s, figure("nmf_fingerprint.png"), M, CT - 0.05, W - 2 * M, CB - CT + 0.05, "c");
}
{
  const s = content("Appendix", "Battery dispatch for a Tier 1 home, summer and winter",
    "Static version of the app view: load, net load with battery, system top hours and state of charge across multi-day peak runs", "");
  fig(s, figure("bess_dispatch_example.png"), M, CT, W - 2 * M, CB - CT, "c");
}

// ---------------------------------------------------------------- write + theme
(async () => {
  await pres.writeFile({ fileName: OUT });
  const SK = process.env.PPTX_SKILL;
  if (SK) {
    const { applyTheme } = require(path.join(SK, "scripts", "apply_theme.js"));
    await applyTheme(OUT, THEME);
  }
  console.log("wrote", OUT);
})();
