/* Plants app — front end. Plain JS, hash routing, relative URLs (works under HA ingress). */
"use strict";

const view = document.getElementById("view");
const titleEl = document.getElementById("title");
const backEl = document.getElementById("back");
const settingsLink = document.getElementById("settings-link");
const photoLink = document.getElementById("photo-link");

// A photo picked on the plant page, handed to the photo screen.
let pendingPhoto = null;

const LEAF = '<svg viewBox="0 0 100 100" aria-hidden="true"><path d="M90 10C45 10 12 35 12 72c0 6 1 11 3 16 4-20 18-38 40-50-18 15-29 32-33 52 6 2 12 2 18 2 37 0 50-40 50-82z"/></svg>';

// ---------- helpers ----------

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function api(path, opts = {}) {
  const init = { headers: {}, ...opts };
  if (opts.json !== undefined) {
    init.method = init.method || "POST";
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.json);
    delete init.json;
  }
  const res = await fetch("api/" + path, init);
  let data = null;
  try { data = await res.json(); } catch (_) { /* no body */ }
  if (!res.ok) throw new Error((data && data.error) || `Request failed (${res.status})`);
  return data;
}

let toastTimer;
function toast(msg, isError = false) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.className = isError ? "err" : "";
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, isError ? 5000 : 2500);
}

// In-page dialog (native confirm/prompt pop-ups may not show inside the phone app).
function dialog(text, { yesLabel = "Remove", danger = true, input = null } = {}) {
  return new Promise(resolve => {
    const m = document.getElementById("modal");
    const inp = document.getElementById("modal-input");
    const yes = document.getElementById("modal-yes");
    const no = document.getElementById("modal-no");
    document.getElementById("modal-text").textContent = text;
    yes.textContent = yesLabel;
    yes.className = danger ? "btn danger" : "btn primary";
    inp.hidden = input === null;
    inp.value = input ?? "";
    m.hidden = false;
    if (input !== null) { inp.focus(); inp.select(); }
    const done = v => { m.hidden = true; yes.onclick = no.onclick = inp.onkeydown = null; resolve(v); };
    yes.onclick = () => done(input === null ? true : inp.value.trim());
    no.onclick = () => done(input === null ? false : null);
    inp.onkeydown = e => { if (e.key === "Enter") yes.onclick(); if (e.key === "Escape") no.onclick(); };
  });
}
const confirmBox = text => dialog(text);
const promptBox = (text, value) => dialog(text, { yesLabel: "Save", danger: false, input: value });

function setBar(title, backHref) {
  titleEl.textContent = title;
  document.title = title === "Plants" ? "Plants" : `${title} · Plants`;
  backEl.hidden = !backHref;
  if (backHref) backEl.setAttribute("href", backHref);
  settingsLink.hidden = location.hash.startsWith("#/settings");
  photoLink.hidden = location.hash.startsWith("#/photo");
}

function fmtWhen(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return d.toLocaleString("en-GB", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
}

function fmtDate(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

function ago(iso) {
  const days = Math.floor((Date.now() - new Date(iso)) / 86400000);
  if (isNaN(days)) return "";
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 30) return `${days} d ago`;
  return fmtDate(iso);
}

function photo(p, big = false) {
  if (p.latest_photo) {
    return `<div class="photo has-img"><img src="api/photos/${p.latest_photo.id}/thumb" alt="" loading="lazy"><span class="badge">${p.number}</span></div>`;
  }
  return `<div class="photo${big ? " big" : ""}">${LEAF}<span class="num">${p.number}</span>${big ? '<span class="note">No photo yet</span>' : ""}</div>`;
}

// One hidden file input styled as a button. The Companion app always opens the
// file picker (it ignores a camera request), so photos are taken with the camera app first.
function pickButtons(id, label = "Add photo") {
  return `<label class="btn primary" for="${id}-file">${label}</label>
    <input type="file" id="${id}-file" accept="image/*" hidden>`;
}

function onPick(id, handler) {
  const input = document.getElementById(`${id}-file`);
  input.addEventListener("change", () => { if (input.files[0]) handler(input.files[0]); input.value = ""; });
}

function gallery(p) {
  if (!p.photos.length) {
    return `<div class="photo big">${LEAF}<span class="num">${p.number}</span><span class="note">No photo yet</span></div>`;
  }
  const slides = p.photos.map((ph, i) => `<figure class="slide" data-i="${i}">
      <img src="api/photos/${ph.id}/full" alt="Plant ${p.number}, ${esc(fmtDate(ph.taken_at))}" ${i ? 'loading="lazy"' : ""}></figure>`).join("");
  const nav = p.photos.length > 1
    ? `<button class="btn icon" id="gal-prev" aria-label="Newer photo">‹</button><button class="btn icon" id="gal-next" aria-label="Older photo">›</button>`
    : "";
  return `<div class="gallery" id="gal">${slides}</div>
    <div class="gal-meta">
      <span class="grow"><strong id="gal-date"></strong> <span class="muted" id="gal-count"></span></span>
      ${nav}
      <button class="btn small danger" id="gal-del">Delete</button>
    </div>
    <p class="gal-note" id="gal-note"></p>`;
}

function wireGallery(p) {
  const gal = document.getElementById("gal");
  if (!gal) return;
  let index = 0;
  const show = () => {
    const ph = p.photos[index];
    document.getElementById("gal-date").textContent = fmtWhen(ph.taken_at);
    document.getElementById("gal-count").textContent = p.photos.length > 1 ? `· ${index + 1} of ${p.photos.length}${index === 0 ? " (latest)" : ""}` : "";
    const note = document.getElementById("gal-note");
    note.textContent = ph.note;
    note.hidden = !ph.note;
    const prev = document.getElementById("gal-prev"), next = document.getElementById("gal-next");
    if (prev) prev.disabled = index === 0;
    if (next) next.disabled = index === p.photos.length - 1;
  };
  const go = i => gal.scrollTo({ left: i * gal.clientWidth, behavior: "smooth" });
  gal.addEventListener("scroll", () => {
    const i = Math.round(gal.scrollLeft / gal.clientWidth);
    if (i !== index && i >= 0 && i < p.photos.length) { index = i; show(); }
  }, { passive: true });
  document.getElementById("gal-prev")?.addEventListener("click", () => go(index - 1));
  document.getElementById("gal-next")?.addEventListener("click", () => go(index + 1));
  document.getElementById("gal-del").addEventListener("click", async () => {
    const ph = p.photos[index];
    if (!await confirmBox(`Delete the photo from ${fmtDate(ph.taken_at)}? This can't be undone.`)) return;
    try {
      await api(`photos/${ph.id}`, { method: "DELETE" });
      toast("Photo deleted");
      await viewPlant(p.number);
    } catch (err) { toast(err.message, true); }
  });
  show();
}

// ---------- views ----------

async function viewHome() {
  setBar("Plants");
  const data = await api("overview");
  if (data.count === 0) {
    view.innerHTML = `<div class="empty">
      <h2>No plants yet</h2>
      <p>Add plants one by one, or import a plant list in Settings.</p>
      <div class="row-actions" style="justify-content:center">
        <a class="btn primary" href="#/settings">Open Settings</a>
      </div></div>`;
    return;
  }
  const tile = p => `<a class="tile" href="#/plant/${p.number}">
      ${photo(p)}
      <div class="tile-body">
        <div class="tile-name">${esc(p.name)}</div>
        <div class="tile-sub">${p.position ? esc(p.position) : esc(p.species) || "&nbsp;"}</div>
        <span class="chip${p.last_watered ? "" : " dim"}">${p.last_watered ? "💧 " + esc(ago(p.last_watered)) : "💧 not logged"}</span>
        <span class="chip${p.latest_photo ? "" : " dim"}">${p.latest_photo ? "📷 " + esc(ago(p.latest_photo.taken_at)) : "No photo"}</span>
      </div></a>`;
  let html = `<div class="home-head">
      <p class="summary">${data.count} plants · next: ${esc(data.next_check.label)}, ${esc(fmtWhen(data.next_check.at))}</p>
      <a class="btn primary" href="#/round">💧 Water round</a>
    </div>`;
  for (const a of data.areas) {
    const spots = a.spots.filter(s => s.plants.length);
    if (!spots.length) continue;
    html += `<section class="area"><h2>${esc(a.name)}</h2>`;
    for (const s of spots) {
      html += `<div class="spot"><h3>${esc(s.name)}</h3><div class="grid">${s.plants.map(tile).join("")}</div></div>`;
    }
    html += `</section>`;
  }
  if (data.unplaced.length) {
    html += `<section class="area"><h2>No location</h2><div class="spot"><div class="grid">${data.unplaced.map(tile).join("")}</div></div></section>`;
  }
  view.innerHTML = html;
}

async function viewPlant(number) {
  const p = await api(`plants/${number}`);
  setBar(`${p.number} · ${p.name}`, "#/");
  const fact = (label, value) => value ? `<div><dt>${label}</dt><dd>${esc(value)}</dd></div>` : "";
  const log = p.location_log.length
    ? `<ul class="log">${p.location_log.map(l => `<li><span class="muted">${esc(fmtDate(l.at))}</span><br>${esc(l.from_text)} → ${esc(l.to_text)}</li>`).join("")}</ul>`
    : `<p class="later">No moves recorded yet.</p>`;
  view.innerHTML = `
    ${gallery(p)}
    <div class="row-actions photo-actions">${pickButtons("pp")}</div>
    <div class="plant-head">
      <h2>${esc(p.name)}</h2>
      ${p.species ? `<div class="species">${esc(p.species)}</div>` : ""}
    </div>
    <div class="card"><dl class="facts">
      ${fact("Location", p.location)}
        ${fact("Grow light", p.grow_light)}
      ${fact("Pot", p.pot)}
      ${fact("Soil sensor", p.soil_sensor)}
    </dl></div>
    ${p.care_notes ? `<div class="card"><h3>Care notes</h3><p class="prose">${esc(p.care_notes)}</p></div>` : ""}
    ${wateringCard(p)}
    <div class="card"><h3>Diagnoses</h3><p class="later">Coming in the next stage: Claude's diagnoses from the “plant check” in chat.</p></div>
    <div class="card"><h3>Location history</h3>${log}</div>
    <div class="row-actions"><a class="btn" href="#/plant/${p.number}/edit">Edit details</a></div>`;
  wireGallery(p);
  wireWatering(p);
  onPick("pp", file => { pendingPhoto = { number: p.number, file }; location.hash = `#/photo/${p.number}`; });
}

function careLine(c) {
  const what = c.kind === "watered" ? (c.fed ? "Watered + fed" : "Watered") : "Checked, still moist";
  const via = c.source === "ha" ? " · from HA" : c.source === "round" ? " · water round" : "";
  return `<li data-care="${c.id}"><span class="grow"><strong>${what}</strong> <span class="muted">${esc(fmtWhen(c.at))}${via}</span>${c.note ? `<br>${esc(c.note)}` : ""}</span>
    <button class="btn icon small-x" data-act="care-del" aria-label="Delete entry">✕</button></li>`;
}

function wateringCard(p) {
  const fact = (label, value) => `<div><dt>${label}</dt><dd>${value}</dd></div>`;
  const when = iso => iso ? `${esc(ago(iso))} <span class="muted">· ${esc(fmtWhen(iso))}</span>` : `<span class="muted">not logged yet</span>`;
  const log = p.care_log.length
    ? `<ul class="log care">${p.care_log.slice(0, 10).map(careLine).join("")}</ul>`
    : `<p class="later">Nothing logged yet.</p>`;
  return `<div class="card"><h3>Watering</h3>
    <dl class="facts">
      ${p.watering_rule ? fact("Rule", esc(p.watering_rule)) : ""}
      ${fact("Last watered", when(p.last_watered))}
      ${fact("Last checked", when(p.last_checked))}
      ${fact("Last fed", when(p.last_fed))}
      ${fact("Next check", `${esc(p.next_check.label)}, ${esc(fmtWhen(p.next_check.at))}`)}
    </dl>
    <div class="row-actions care-actions">
      <button class="btn primary" id="w-water">💧 Watered</button>
      <label class="check"><input type="checkbox" id="w-fed"> with feed</label>
      <button class="btn" id="w-check">Checked, still moist</button>
    </div>
    <h3>History</h3>${log}</div>`;
}

function wireWatering(p) {
  const log = async (kind) => {
    const fed = kind === "watered" && document.getElementById("w-fed").checked;
    try {
      await api(`plants/${p.number}/care`, { json: { kind, fed } });
      toast(kind === "watered" ? (fed ? "Watered + fed" : "Watered") : "Checked");
      await viewPlant(p.number);
    } catch (err) { toast(err.message, true); }
  };
  document.getElementById("w-water").addEventListener("click", () => log("watered"));
  document.getElementById("w-check").addEventListener("click", () => log("checked"));
  view.querySelectorAll('[data-act="care-del"]').forEach(btn => btn.addEventListener("click", async () => {
    const id = btn.closest("[data-care]").dataset.care;
    if (!await confirmBox("Delete this entry?")) return;
    try {
      await api(`care/${id}`, { method: "DELETE" });
      toast("Entry deleted");
      await viewPlant(p.number);
    } catch (err) { toast(err.message, true); }
  }));
}

async function viewRound() {
  setBar("Water round", "#/");
  const data = await api("overview");
  const row = p => `<div class="round-row" data-number="${p.number}">
      <div class="round-info">
        <div><span class="n">${p.number}</span> ${esc(p.name)}</div>
        <div class="muted small">${p.watering_rule ? esc(p.watering_rule) + " · " : ""}${p.last_watered ? "watered " + esc(ago(p.last_watered)) : "not logged yet"}</div>
      </div>
      <div class="seg" role="group" aria-label="Plant ${p.number}">
        <button type="button" data-kind="checked">Moist</button>
        <button type="button" data-kind="watered">Watered</button>
      </div>
      <label class="check feed" hidden><input type="checkbox"> feed</label>
    </div>`;
  let html = `<p class="muted" style="margin:4px 0 0">Tap <strong>Watered</strong> or <strong>Moist</strong> (checked, didn't need water) for each plant you looked at. Leave the rest blank. Tap again to clear.</p>`;
  for (const a of data.areas) {
    const plants = a.spots.flatMap(s => s.plants);
    if (!plants.length) continue;
    html += `<section class="area"><h2>${esc(a.name)}</h2>`;
    for (const s of a.spots.filter(s => s.plants.length)) {
      html += `<div class="spot"><h3>${esc(s.name)}</h3><div class="list">${s.plants.map(row).join("")}</div></div>`;
    }
    html += `</section>`;
  }
  if (data.unplaced.length) html += `<section class="area"><h2>No location</h2><div class="list">${data.unplaced.map(row).join("")}</div></section>`;
  html += `<div class="round-save"><span class="muted" id="round-count">Nothing marked yet</span>
    <button class="btn primary" id="round-save" disabled>Save</button></div>`;
  view.innerHTML = html;

  const state = new Map();
  const count = () => {
    const vals = [...state.values()];
    const w = vals.filter(v => v === "watered").length, c = vals.length - w;
    document.getElementById("round-count").textContent = vals.length ? `${w} watered · ${c} moist` : "Nothing marked yet";
    document.getElementById("round-save").disabled = !vals.length;
  };
  view.querySelectorAll(".round-row").forEach(r => {
    const n = Number(r.dataset.number);
    const feed = r.querySelector(".feed");
    r.querySelectorAll(".seg button").forEach(b => b.addEventListener("click", () => {
      const kind = state.get(n) === b.dataset.kind ? null : b.dataset.kind;
      if (kind) state.set(n, kind); else state.delete(n);
      r.querySelectorAll(".seg button").forEach(x => x.classList.toggle("on", x.dataset.kind === kind));
      feed.hidden = kind !== "watered";
      if (kind !== "watered") feed.querySelector("input").checked = false;
      count();
    }));
  });
  document.getElementById("round-save").addEventListener("click", async e => {
    const entries = [...state.entries()].map(([number, kind]) => ({
      number, kind,
      fed: kind === "watered" && view.querySelector(`.round-row[data-number="${number}"] .feed input`).checked,
    }));
    e.target.disabled = true;
    e.target.textContent = "Saving…";
    try {
      const res = await api("round", { json: { entries } });
      toast(`Saved: ${res.watered} watered, ${res.checked} moist`);
      location.hash = "#/";
    } catch (err) {
      toast(err.message, true);
      e.target.disabled = false;
      e.target.textContent = "Save";
    }
  });
}

async function viewPhoto(number) {
  const ov = await api("overview");
  let file = pendingPhoto && (number === null || pendingPhoto.number === number) ? pendingPhoto.file : null;
  pendingPhoto = null;
  const back = number ? `#/plant/${number}` : "#/";
  setBar("New photo", back);

  let options = number ? "" : `<option value="">— Choose a plant —</option>`;
  for (const a of ov.areas) {
    const inArea = a.spots.flatMap(s => s.plants);
    if (!inArea.length) continue;
    options += `<optgroup label="${esc(a.name)}">` + inArea.map(pl =>
      `<option value="${pl.number}"${pl.number === number ? " selected" : ""}>${pl.number} · ${esc(pl.name)}</option>`).join("") + `</optgroup>`;
  }
  if (ov.unplaced.length) options += `<optgroup label="No location">` + ov.unplaced.map(pl =>
    `<option value="${pl.number}"${pl.number === number ? " selected" : ""}>${pl.number} · ${esc(pl.name)}</option>`).join("") + `</optgroup>`;

  view.innerHTML = `<form id="photo-form" autocomplete="off">
    <div class="field"><label for="ph-plant">Plant</label><select id="ph-plant" name="plant">${options}</select></div>
    <div id="ph-preview"></div>
    <div class="row-actions" id="ph-pick">${pickButtons("np")}</div>
    <p class="muted hint-line">Take the photo with the camera app first, then pick it here.</p>
    <div class="field"><label for="ph-note">Note <span class="hint">optional — e.g. new leaf, yellowing on the left</span></label>
      <textarea id="ph-note" name="note" maxlength="500"></textarea></div>
    <div class="row-actions">
      <button class="btn primary" type="submit" id="ph-save" disabled>Save photo</button>
      <a class="btn" href="${back}">Cancel</a>
    </div></form>`;

  const preview = document.getElementById("ph-preview");
  const save = document.getElementById("ph-save");
  let previewUrl = null;
  const setFile = f => {
    file = f;
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = URL.createObjectURL(f);
    preview.innerHTML = `<div class="preview"><img src="${previewUrl}" alt="Photo to save"></div>`;
    save.disabled = false;
    const pick = document.querySelector('#ph-pick label[for="np-file"]');
    pick.textContent = "Choose another";
    pick.classList.remove("primary");
  };
  onPick("np", setFile);
  if (file) setFile(file);

  document.getElementById("photo-form").addEventListener("submit", async e => {
    e.preventDefault();
    const target = Number(document.getElementById("ph-plant").value);
    if (!target) { toast("Choose the plant first.", true); return; }
    if (!file) { toast("Take or choose a photo first.", true); return; }
    const fd = new FormData();
    fd.append("file", file);
    fd.append("note", document.getElementById("ph-note").value);
    fd.append("taken_hint", String(file.lastModified || Date.now()));
    save.disabled = true;
    save.textContent = "Saving…";
    try {
      await api(`plants/${target}/photos`, { method: "POST", body: fd });
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      toast("Photo saved");
      location.hash = `#/plant/${target}`;
    } catch (err) {
      toast(err.message, true);
      save.disabled = false;
      save.textContent = "Save photo";
    }
  });
}

async function viewEdit(number) {
  const isNew = number === null;
  const [locs, p, ov] = await Promise.all([
    api("locations"),
    isNew ? Promise.resolve(null) : api(`plants/${number}`),
    isNew ? api("overview") : Promise.resolve(null),
  ]);
  const plant = p || { number: ov.next_number, name: "", species: "", spot_id: null, position: "", pot: "", watering_rule: "", grow_light: "", care_notes: "", soil_sensor: "" };
  setBar(isNew ? "New plant" : `Edit ${plant.number}`, isNew ? "#/settings" : `#/plant/${plant.number}`);

  let spotOptions = `<option value="">— No location —</option>`;
  for (const a of locs.areas) {
    if (!a.spots.length) continue;
    spotOptions += `<optgroup label="${esc(a.name)}">` +
      a.spots.map(s => `<option value="${s.id}"${s.id === plant.spot_id ? " selected" : ""}>${esc(a.name)} › ${esc(s.name)}</option>`).join("") +
      `</optgroup>`;
  }
  const text = (id, label, hint = "") => `<div class="field"><label for="f-${id}">${label}${hint ? ` <span class="hint">${hint}</span>` : ""}</label>
      <input type="text" id="f-${id}" name="${id}" value="${esc(plant[id])}"></div>`;
  const area = (id, label, hint = "") => `<div class="field"><label for="f-${id}">${label}${hint ? ` <span class="hint">${hint}</span>` : ""}</label>
      <textarea id="f-${id}" name="${id}">${esc(plant[id])}</textarea></div>`;

  view.innerHTML = `<form id="plant-form" autocomplete="off">
    ${isNew ? `<div class="field"><label for="f-number">Number <span class="hint">permanent — never reused</span></label>
      <input type="number" id="f-number" name="number" min="1" value="${plant.number}"></div>` : ""}
    ${text("name", "Name")}
    ${text("species", "Species")}
    <div class="field"><label for="f-spot">Area › spot <span class="hint">manage the list in Settings</span></label>
      <select id="f-spot" name="spot_id">${spotOptions}</select></div>
    ${text("position", "Position in the spot", "e.g. left front")}
    ${text("watering_rule", "Watering rule", "e.g. top ~5 cm dry")}
    ${text("grow_light", "Grow light")}
    ${text("pot", "Pot")}
    ${area("care_notes", "Care notes")}
    ${text("soil_sensor", "Soil sensor entity", "when one is fitted")}
    <div class="row-actions">
      <button class="btn primary" type="submit">${isNew ? "Add plant" : "Save"}</button>
      <a class="btn" href="${isNew ? "#/settings" : `#/plant/${plant.number}`}">Cancel</a>
      ${isNew ? "" : `<button class="btn danger" type="button" id="del">Remove plant</button>`}
    </div></form>`;

  const form = document.getElementById("plant-form");
  form.addEventListener("submit", async e => {
    e.preventDefault();
    const fd = Object.fromEntries(new FormData(form).entries());
    try {
      if (isNew) {
        const created = await api("plants", { json: fd });
        toast(`Plant ${created.number} added`);
        location.hash = `#/plant/${created.number}`;
      } else {
        await api(`plants/${plant.number}`, { method: "PUT", json: fd });
        toast("Saved");
        location.hash = `#/plant/${plant.number}`;
      }
    } catch (err) { toast(err.message, true); }
  });
  const del = document.getElementById("del");
  if (del) del.addEventListener("click", async () => {
    if (!await confirmBox(`Remove plant ${plant.number} (${plant.name})? Its details and history are deleted. The number won't be reused.`)) return;
    try {
      await api(`plants/${plant.number}`, { method: "DELETE" });
      toast(`Plant ${plant.number} removed`);
      location.hash = "#/settings";
    } catch (err) { toast(err.message, true); }
  });
}

async function viewSettings() {
  setBar("Settings", "#/");
  const [ov, locs] = await Promise.all([api("overview"), api("locations")]);
  const plants = [...ov.areas.flatMap(a => a.spots.flatMap(s => s.plants)), ...ov.unplaced].sort((a, b) => a.number - b.number);

  const plantRows = plants.length
    ? `<div class="list">${plants.map(p => `<a class="list-row" href="#/plant/${p.number}/edit">
        <span class="n">${p.number}</span>
        <span class="grow"><div>${esc(p.name)}</div><div class="muted" style="font-size:13px">${esc(p.location)}</div></span>
        <span class="muted">Edit</span></a>`).join("")}</div>`
    : `<p class="muted">No plants yet.</p>`;

  const areaCards = locs.areas.map((a, ai) => `
    <div class="area-card" data-area="${a.id}">
      <div class="head">
        <strong>${esc(a.name)}</strong>
        <button class="btn icon" data-act="area-up" ${ai === 0 ? "disabled" : ""} aria-label="Move up">↑</button>
        <button class="btn icon" data-act="area-down" ${ai === locs.areas.length - 1 ? "disabled" : ""} aria-label="Move down">↓</button>
        <button class="btn small" data-act="area-rename">Rename</button>
        <button class="btn small danger" data-act="area-delete">Remove</button>
      </div>
      ${a.spots.map((s, si) => `
        <div class="spot-row" data-spot="${s.id}">
          <span>${esc(s.name)} <span class="muted">· ${s.plant_count}</span></span>
          <button class="btn icon" data-act="spot-up" ${si === 0 ? "disabled" : ""} aria-label="Move up">↑</button>
          <button class="btn icon" data-act="spot-down" ${si === a.spots.length - 1 ? "disabled" : ""} aria-label="Move down">↓</button>
          <button class="btn small" data-act="spot-rename">Rename</button>
          <button class="btn small danger" data-act="spot-delete" aria-label="Remove spot">✕</button>
        </div>`).join("")}
      <form class="inline-add" data-act="spot-add">
        <input type="text" name="name" placeholder="New spot in ${esc(a.name)}" aria-label="New spot name">
        <button class="btn small" type="submit">Add</button>
      </form>
    </div>`).join("");

  view.innerHTML = `
    <section class="section">
      <h2>Plants</h2>
      ${plantRows}
      <div class="row-actions"><a class="btn primary" href="#/new">Add plant</a></div>
    </section>
    <section class="section">
      <h2>Areas &amp; spots</h2>
      <p class="muted" style="margin:0">Tiles on the home page follow this order.</p>
      ${areaCards}
      <form class="inline-add" data-act="area-add" style="margin-top:12px">
        <input type="text" name="name" placeholder="New area" aria-label="New area name">
        <button class="btn small" type="submit">Add area</button>
      </form>
    </section>
    <section class="section">
      <h2>Import &amp; export</h2>
      <p class="muted" style="margin:0 0 8px">Import adds new plants and updates existing ones by number. Areas and spots are added if missing. Nothing is deleted.</p>
      <form id="import-form">
        <div class="field"><label for="imp-file">Plant list file (.json)</label><input type="file" id="imp-file" name="file" accept=".json,application/json"></div>
        <details class="field"><summary class="muted">…or paste the text instead</summary>
          <textarea name="text" id="imp-text" placeholder='{"areas": [...], "plants": [...]}'></textarea></details>
        <div class="row-actions">
          <button class="btn primary" type="submit">Import</button>
          <a class="btn" href="api/export" download>Export all</a>
        </div>
      </form>
    </section>
    <p class="muted" style="margin-top:28px;font-size:13px">Plants app ${esc(ov.version)}</p>`;

  // areas & spots actions
  view.querySelectorAll("[data-act]").forEach(el => {
    const act = el.dataset.act;
    const areaId = el.closest("[data-area]")?.dataset.area;
    const spotId = el.closest("[data-spot]")?.dataset.spot;
    const handler = async e => {
      e.preventDefault();
      try {
        if (act === "area-up" || act === "area-down") await api(`areas/${areaId}`, { method: "PUT", json: { move: act === "area-up" ? -1 : 1 } });
        else if (act === "spot-up" || act === "spot-down") await api(`spots/${spotId}`, { method: "PUT", json: { move: act === "spot-up" ? -1 : 1 } });
        else if (act === "area-rename" || act === "spot-rename") {
          const row = el.closest(act === "area-rename" ? ".head" : ".spot-row");
          const current = act === "area-rename" ? row.querySelector("strong").textContent : row.querySelector("span").firstChild.textContent.trim();
          const name = await promptBox("New name", current);
          if (!name || name.trim() === current) return;
          await api(act === "area-rename" ? `areas/${areaId}` : `spots/${spotId}`, { method: "PUT", json: { name } });
        }
        else if (act === "area-delete") {
          if (!await confirmBox("Remove this area? It must have no spots.")) return;
          await api(`areas/${areaId}`, { method: "DELETE" });
        }
        else if (act === "spot-delete") {
          if (!await confirmBox("Remove this spot? It must have no plants.")) return;
          await api(`spots/${spotId}`, { method: "DELETE" });
        }
        else if (act === "spot-add") await api("spots", { json: { area_id: Number(areaId), name: el.name.value } });
        else if (act === "area-add") await api("areas", { json: { name: el.name.value } });
        await viewSettings();
      } catch (err) { toast(err.message, true); }
    };
    el.addEventListener(el.tagName === "FORM" ? "submit" : "click", handler);
  });

  // import
  document.getElementById("import-form").addEventListener("submit", async e => {
    e.preventDefault();
    const form = e.target;
    const file = form.file.files[0];
    const text = form.text.value.trim();
    if (!file && !text) { toast("Choose a file or paste the text first.", true); return; }
    const fd = new FormData();
    if (file) fd.append("file", file); else fd.append("text", text);
    try {
      const res = await api("import", { method: "POST", body: fd });
      toast(`Imported: ${res.added} added, ${res.updated} updated`);
      await viewSettings();
    } catch (err) { toast(err.message, true); }
  });
}

// ---------- router ----------

async function route() {
  const h = location.hash || "#/";
  let m;
  try {
    if ((m = h.match(/^#\/plant\/(\d+)\/edit$/))) await viewEdit(Number(m[1]));
    else if ((m = h.match(/^#\/plant\/(\d+)$/))) await viewPlant(Number(m[1]));
    else if (h === "#/new") await viewEdit(null);
    else if (h === "#/round") await viewRound();
    else if ((m = h.match(/^#\/photo(?:\/(\d+))?$/))) await viewPhoto(m[1] ? Number(m[1]) : null);
    else if (h === "#/settings") await viewSettings();
    else await viewHome();
    window.scrollTo(0, 0);
  } catch (err) {
    setBar("Plants", "#/");
    view.innerHTML = `<div class="empty"><h2>Something went wrong</h2><p>${esc(err.message)}</p>
      <div class="row-actions" style="justify-content:center"><a class="btn" href="#/">Back to plants</a></div></div>`;
  }
}

window.addEventListener("hashchange", route);
route();
