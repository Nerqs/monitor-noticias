"use strict";

const TOKEN = window.MONITOR.token;
const PRESETS = window.MONITOR.presets;

const state = {
  view: "criticos",
  page: "reader",
  items: [],
  selectedId: null,
  sources: [],
  config: null,
  editingSource: null,
  lastRunId: null,
};

// --- Utilidades -------------------------------------------------------------

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

async function api(path, options = {}) {
  const opts = { ...options, headers: { "X-Token": TOKEN, ...(options.headers || {}) } };
  if (opts.body && typeof opts.body !== "string") {
    opts.body = JSON.stringify(opts.body);
    opts.headers["Content-Type"] = "application/json";
  }
  const res = await fetch(path, opts);
  let data = null;
  try { data = await res.json(); } catch (_) { /* resposta sem JSON */ }
  if (!res.ok || (data && data.error && res.status >= 400)) {
    throw new Error((data && data.error) || `Erro ${res.status}`);
  }
  return data;
}

let toastTimer;
function toast(message, isError = false) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.toggle("error", isError);
  el.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), isError ? 7000 : 3500);
}

function parseDbTime(value) {
  if (!value) return null;
  return new Date(value.replace(" ", "T") + "Z");
}

function timeAgo(value) {
  const date = parseDbTime(value);
  if (!date || isNaN(date)) return "";
  const minutes = Math.round((Date.now() - date) / 60000);
  if (minutes < 1) return "agora";
  if (minutes < 60) return `há ${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `há ${hours} h`;
  const days = Math.round(hours / 24);
  if (days < 8) return `há ${days} d`;
  return date.toLocaleDateString("pt-BR");
}

function capitalize(text) {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function fullDate(value) {
  const date = parseDbTime(value);
  return date && !isNaN(date) ? date.toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) : "";
}

const icon = (name) => `<svg class="icon"><use href="#i-${name}"/></svg>`;

const VIEW_TITLES = {
  criticos: "Críticos", minhas: "Minhas tecnologias", paravoce: "Para você", seguranca: "Segurança",
  tecnologia: "Tecnologia", favoritos: "Favoritos", todos: "Todas as notícias",
};

function badges(item) {
  let html = "";
  if (item.level === "critico") html += '<span class="badge critico">CRÍTICO</span>';
  else if (item.level === "alto") html += '<span class="badge alto">ALTO</span>';
  if (item.kev) html += '<span class="badge kev" title="Catálogo de falhas exploradas da CISA">EXPLORADA</span>';
  if (item.affinity >= 65 && item.liked !== -1) {
    html += `<span class="badge like" title="Afinidade com o que você curtiu">♥ ${Math.round(item.affinity)}%</span>`;
  }
  if (item.matched_tech) {
    for (const t of item.matched_tech.split(",")) html += `<span class="badge tech">${esc(t)}</span>`;
  }
  return html;
}

function openExternal(url) {
  api("/api/open", { method: "POST", body: { url } }).catch((e) => toast(e.message, true));
}

// --- Navegação --------------------------------------------------------------

function showPage(page) {
  state.page = page;
  for (const id of ["reader", "fontes", "erros", "config"]) {
    $(`#page-${id}`).classList.toggle("hidden", id !== page);
  }
  $$(".nav-item").forEach((b) => {
    b.classList.toggle("active", page === "reader" ? b.dataset.view === state.view : b.dataset.page === page);
  });
  if (page === "fontes") loadSources();
  if (page === "erros") loadErrors();
  if (page === "config") loadConfig();
}

$$(".nav-item").forEach((btn) => {
  btn.addEventListener("click", () => {
    if (btn.dataset.view) {
      state.view = btn.dataset.view;
      state.selectedId = null;
      showPage("reader");
      loadItems();
      renderEmptyReader();
    } else {
      showPage(btn.dataset.page);
    }
  });
});

// --- Leitor -----------------------------------------------------------------

async function loadItems() {
  const params = new URLSearchParams({
    view: state.view,
    q: $("#search").value,
    unread: $("#unread-only").checked && state.view !== "favoritos" ? "1" : "0",
    days: $("#days").value,
    sort: $("#sort").value,
  });
  try {
    if (state.view === "paravoce") state.prefs = await api("/api/preferences");
    state.items = await api(`/api/items?${params}`);
  } catch (e) {
    toast(e.message, true);
    return;
  }
  renderList();
  loadCounts();
  $("#list-title").textContent = VIEW_TITLES[state.view];
  const unread = state.items.filter((i) => !i.is_read).length;
  $("#list-sub").textContent = `${state.items.length} notícia${state.items.length === 1 ? "" : "s"}` +
    (unread ? ` · ${unread} não lida${unread === 1 ? "" : "s"}` : "");
}

function renderList() {
  const list = $("#item-list");
  if (state.view === "paravoce" && state.prefs && !state.prefs.active) {
    const left = state.prefs.min_likes - state.prefs.likes;
    list.innerHTML = `<div class="list-hint">${icon("sparkle")}<span>Use <b>Gostei</b> em mais ${left} notícia${left > 1 ? "s" : ""}
      para o programa aprender seu gosto. Até lá, esta lista segue a ordem de prioridade.</span></div>`;
  } else {
    list.innerHTML = "";
  }
  if (!state.items.length) {
    const hint = $("#unread-only").checked ? "Nada novo por aqui. Desmarque \"Só não lidas\" para ver tudo." :
      "Nenhuma notícia. Clique em \"Coletar agora\".";
    list.innerHTML = `<div class="list-empty">${esc(hint)}</div>`;
    return;
  }
  list.insertAdjacentHTML("beforeend", state.items.map((item) => `
    <div class="item lvl-${item.level} ${item.is_read ? "" : "unread"} ${item.id === state.selectedId ? "selected" : ""}" data-id="${item.id}">
      <div class="meta">${badges(item)}<span>${esc(item.source_name)}</span><span>·</span><span>${esc(timeAgo(item.published))}</span>${item.starred ? '<span class="star">★</span>' : ""}</div>
      <div class="title">${esc(item.title)}</div>
    </div>`).join(""));
  $$(".item", list).forEach((el) => el.addEventListener("click", () => selectItem(Number(el.dataset.id))));
}

async function renderEmptyReader() {
  let counts = {}, status = {};
  try {
    [counts, status] = await Promise.all([api("/api/counts"), api("/api/status")]);
  } catch (_) { /* mostra o painel mesmo assim */ }
  if (state.selectedId !== null || state.page !== "reader") return;
  const run = status.last_run;
  const hour = new Date().getHours();
  const greeting = hour < 12 ? "Bom dia" : hour < 18 ? "Boa tarde" : "Boa noite";
  const tile = (n, label, view, cls = "") =>
    `<button class="tile ${cls}" data-goto="${view}"><span class="tile-n">${n || 0}</span><span class="tile-l">${label}</span></button>`;
  $("#read-pane").innerHTML = `
    <div class="overview">
      <p class="eyebrow">${esc(capitalize(new Date().toLocaleDateString("pt-BR", { weekday: "long", day: "numeric", month: "long" })))}</p>
      <h1>${greeting}.</h1>
      <p class="muted">${run && run.finished_at ? `Última coleta ${timeAgo(run.finished_at)}, com ${run.new_items} notícias novas.` :
        "Nenhuma coleta feita ainda. Clique em <b>Coletar agora</b> para começar."}</p>
      <div class="tiles">
        ${tile(counts.criticos, "críticas e importantes não lidas", "criticos", "danger")}
        ${tile(counts.minhas, "citam suas tecnologias", "minhas", "accent")}
        ${tile(counts.seguranca, "de segurança não lidas", "seguranca")}
        ${tile(counts.tecnologia, "de tecnologia não lidas", "tecnologia")}
        ${counts.paravoce ? tile(counts.paravoce, "parecidas com o que você curtiu", "paravoce", "like") : ""}
      </div>
      ${run && run.sources_failed ? `<button class="notice" data-page-goto="erros">${icon("error")}
        <span><b>${run.sources_failed} fonte${run.sources_failed > 1 ? "s" : ""} com erro</b> na última coleta. Ver detalhes</span></button>` : ""}
      <p class="muted small shortcuts">Atalhos: <kbd>j</kbd>/<kbd>k</kbd> próxima/anterior · <kbd>s</kbd> favoritar ·
        <kbd>m</kbd> lida/não lida · <kbd>g</kbd> gostei · <kbd>n</kbd> não curti · <kbd>a</kbd> ler artigo · <kbd>o</kbd> abrir no navegador</p>
    </div>`;
  $$(".tile").forEach((t) => t.onclick = () => $(`.nav-item[data-view="${t.dataset.goto}"]`).click());
  $$("[data-page-goto]").forEach((t) => t.onclick = () => showPage(t.dataset.pageGoto));
}

async function selectItem(id) {
  state.selectedId = id;
  $$(".item").forEach((el) => el.classList.toggle("selected", Number(el.dataset.id) === id));
  const el = $(`.item[data-id="${id}"]`);
  if (el) el.scrollIntoView({ block: "nearest" });

  let item;
  try {
    item = await api(`/api/items/${id}`);
  } catch (e) {
    toast(e.message, true);
    return;
  }
  renderItem(item);
  if (!item.is_read) setFlag(item, "is_read", true, false);
}

function renderItem(item) {
  const cves = (item.cves || "").split(",").filter(Boolean);
  const related = item.related || [];
  const levelLabel = { critico: "Prioridade crítica", alto: "Prioridade alta", normal: "Por que está aqui" }[item.level];
  $("#read-pane").innerHTML = `
    <div class="read-toolbar">
      <button class="btn" id="act-article">${icon("book")}<span>Ler artigo aqui</span></button>
      <button class="btn" id="act-open">${icon("external")}<span>Abrir no navegador</span></button>
      <span class="spacer"></span>
      <button class="btn like-btn ${item.liked === 1 ? "liked" : ""}" id="act-like" title="Gostei (g)">${icon("up")}<span>Gostei</span></button>
      <button class="btn like-btn ${item.liked === -1 ? "disliked" : ""}" id="act-dislike" title="Não curti (n)">${icon("down")}<span>Não curti</span></button>
      <button class="btn icon-btn ${item.starred ? "on" : ""}" id="act-star" title="Favoritar (s)">${icon("star")}</button>
      <button class="btn icon-btn" id="act-read" title="${item.is_read ? "Marcar como não lida" : "Marcar como lida"} (m)">${icon("check")}</button>
    </div>
    <article class="article">
      <div class="meta">${badges(item)}<span>${esc(item.source_name)}</span><span>·</span>
        <span title="${esc(fullDate(item.published))}">${esc(fullDate(item.published))}</span>
        ${item.cvss != null ? `<span>· CVSS ${Number(item.cvss).toFixed(1)}</span>` : ""}</div>
      <h1>${esc(item.title)}</h1>
      ${cves.length ? `<div class="meta">${cves.map((c) => `<a class="cve" href="#" data-url="https://nvd.nist.gov/vuln/detail/${esc(c)}">${esc(c)}</a>`).join(" ")}</div>` : ""}
      ${item.reasons ? `<div class="reasons lvl-${item.level}"><b>${levelLabel}</b><span>${esc(item.reasons)}</span></div>` : ""}
      ${item.affinity_terms && item.affinity_terms.length && item.liked !== -1 ? `<div class="affinity-note">${icon("sparkle")}
        <span>Parecida com o que você curtiu: ${item.affinity_terms.map(esc).join(", ")}</span></div>` : ""}
      ${item.summary ? `<div class="summary">${esc(item.summary)}</div>` : ""}
      <div id="article-body"></div>
      ${related.length ? `<div class="related"><h3>Também citado em</h3>${related.map((r) =>
        `<a data-id="${r.id}">${esc(r.source_name)}: ${esc(r.title)}</a>`).join("")}</div>` : ""}
    </article>`;
  $("#act-open").onclick = () => openExternal(item.url);
  $("#act-star").onclick = () => setFlag(item, "starred", !item.starred);
  $("#act-read").onclick = () => setFlag(item, "is_read", !item.is_read);
  $("#act-article").onclick = () => loadArticle(item);
  $("#act-like").onclick = () => setLike(item, item.liked === 1 ? 0 : 1);
  $("#act-dislike").onclick = () => setLike(item, item.liked === -1 ? 0 : -1);
  $$(".cve[data-url]").forEach((a) => a.addEventListener("click", (ev) => { ev.preventDefault(); openExternal(a.dataset.url); }));
  $$(".related a").forEach((a) => a.addEventListener("click", () => selectItem(Number(a.dataset.id))));
  if (item.has_article) loadArticle(item);
}

async function setFlag(item, flag, value, rerender = true) {
  try {
    await api(`/api/items/${item.id}/flag`, { method: "POST", body: { flag, value } });
  } catch (e) {
    toast(e.message, true);
    return;
  }
  item[flag] = value ? 1 : 0;
  const listItem = state.items.find((i) => i.id === item.id);
  if (listItem) listItem[flag] = value ? 1 : 0;
  renderList();
  loadCounts();
  if (rerender) renderItem(item);
}

async function setLike(item, value) {
  let res;
  try {
    res = await api(`/api/items/${item.id}/flag`, { method: "POST", body: { flag: "liked", value } });
  } catch (e) {
    toast(e.message, true);
    return;
  }
  item.liked = value;
  const p = res.preferences;
  if (value === 1 && !p.active) {
    const left = p.min_likes - p.likes;
    toast(`Anotado! Curta mais ${left} notícia${left > 1 ? "s" : ""} para ativar o "Para você".`);
  } else if (value !== 0) {
    toast(value === 1 ? "Anotado: mais notícias assim." : "Anotado: menos notícias assim.");
  }
  // As afinidades mudaram: recarrega lista e notícia aberta.
  const fresh = await api(`/api/items/${item.id}`);
  await loadItems();
  if (state.selectedId === item.id) renderItem(fresh);
}

function articleDocument(html) {
  const cs = getComputedStyle(document.documentElement);
  const v = (name) => cs.getPropertyValue(name).trim();
  return `<!doctype html><html><head><meta charset="utf-8"><base target="_blank"><style>
    body { margin: 0; color: ${v("--text")}; background: transparent; font: 17px/1.65 ${v("--reading")}; }
    img, video, figure { max-width: 100%; height: auto; }
    a { color: ${v("--accent")}; }
    pre, code { font-family: ${v("--mono")}; font-size: 13px; white-space: pre-wrap; background: ${v("--panel-2")}; }
    pre { padding: 10px; border-radius: 6px; overflow-x: auto; }
    h1, h2, h3 { font-family: ${v("--font")}; line-height: 1.3; }
    table { border-collapse: collapse; max-width: 100%; } td, th { border: 1px solid ${v("--border")}; padding: 4px 6px; }
    blockquote { border-left: 3px solid ${v("--border")}; margin-left: 0; padding-left: 14px; color: ${v("--muted")}; }
  </style></head><body>${html}</body></html>`;
}

async function loadArticle(item, refresh = false) {
  const box = $("#article-body");
  if (!box) return;
  box.innerHTML = '<div class="article-loading">Carregando o artigo…</div>';
  let data;
  try {
    data = await api(`/api/items/${item.id}/article${refresh ? "?refresh=1" : ""}`);
  } catch (e) {
    box.innerHTML = `<div class="article-loading">Não foi possível carregar o artigo: ${esc(e.message)}.
      Use "Abrir no navegador".</div>`;
    return;
  }
  if (state.selectedId !== item.id) return;
  const frame = document.createElement("iframe");
  frame.className = "article-frame";
  // Sem "allow-scripts": o conteúdo do site não executa nada dentro do programa.
  frame.setAttribute("sandbox", "allow-same-origin allow-popups allow-popups-to-escape-sandbox");
  frame.srcdoc = articleDocument(data.html);
  frame.onload = () => {
    const doc = frame.contentDocument;
    if (!doc) return;
    const resize = () => { frame.style.height = doc.documentElement.scrollHeight + 20 + "px"; };
    resize();
    $$("img", doc).forEach((img) => img.addEventListener("load", resize));
    doc.addEventListener("click", (ev) => {
      const link = ev.target.closest("a[href]");
      if (link) { ev.preventDefault(); openExternal(link.href); }
    });
  };
  box.innerHTML = "";
  box.appendChild(frame);
  $("#act-article").innerHTML = `${icon("refresh")}<span>Recarregar artigo</span>`;
  $("#act-article").onclick = () => loadArticle(item, true);
}

async function loadCounts() {
  try {
    const counts = await api("/api/counts");
    $$("[data-count]").forEach((el) => {
      const n = counts[el.dataset.count];
      el.textContent = n ? n : "";
    });
  } catch (_) { /* silencioso */ }
}

let searchTimer;
$("#search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(loadItems, 250); });
$("#unread-only").addEventListener("change", loadItems);
$("#days").addEventListener("change", loadItems);
$("#sort").addEventListener("change", loadItems);
$("#mark-all").addEventListener("click", async () => {
  const res = await api("/api/items/mark-all-read", { method: "POST", body: { view: state.view } });
  toast(`${res.marked} notícias marcadas como lidas.`);
  loadItems();
});

document.addEventListener("keydown", (ev) => {
  if (state.page !== "reader" || ev.ctrlKey || ev.metaKey || ev.altKey) return;
  if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) return;
  const idx = state.items.findIndex((i) => i.id === state.selectedId);
  const current = state.items[idx];
  if (ev.key === "j" || ev.key === "ArrowDown") {
    const next = state.items[idx + 1] || state.items[0];
    if (next) { selectItem(next.id); ev.preventDefault(); }
  } else if (ev.key === "k" || ev.key === "ArrowUp") {
    const prev = state.items[Math.max(0, idx - 1)];
    if (prev) { selectItem(prev.id); ev.preventDefault(); }
  } else if (current && ev.key === "s") {
    $("#act-star")?.click();
  } else if (current && ev.key === "m") {
    $("#act-read")?.click();
  } else if (current && ev.key === "o") {
    openExternal(current.url);
  } else if (current && ev.key === "g") {
    $("#act-like")?.click();
  } else if (current && ev.key === "n") {
    $("#act-dislike")?.click();
  } else if (current && ev.key === "a") {
    $("#act-article")?.click();
  }
});

// --- Coleta -----------------------------------------------------------------

let pollTimer = null;

async function startCollect(sourceIds = null) {
  try {
    const res = await api("/api/collect", { method: "POST", body: sourceIds ? { source_ids: sourceIds } : {} });
    if (!res.started) toast("Já existe uma coleta em andamento.");
  } catch (e) {
    toast(e.message, true);
    return;
  }
  pollStatus();
}

async function pollStatus() {
  clearTimeout(pollTimer);
  let data;
  try { data = await api("/api/status"); } catch (_) { return; }
  const p = data.progress;
  const btn = $("#collect-btn");
  const status = $("#collect-status");
  if (p.running) {
    btn.disabled = true;
    btn.lastElementChild.textContent = "Coletando…";
    btn.classList.add("busy");
    const pct = p.total ? Math.round((p.done / p.total) * 100) : 0;
    $("#collect-bar").style.width = pct + "%";
    status.textContent = `${p.done} de ${p.total} fontes · ${p.new_items} novas` +
      (p.current.length ? ` · ${p.current.slice(0, 2).join(", ")}` : "");
    pollTimer = setTimeout(pollStatus, 700);
    return;
  }
  btn.disabled = false;
  btn.lastElementChild.textContent = "Coletar agora";
  btn.classList.remove("busy");
  $("#collect-bar").style.width = "0";
  const run = data.last_run;
  if (run && run.finished_at) {
    status.textContent = `Última coleta ${timeAgo(run.finished_at)} · ${run.new_items} novas`;
    $("#error-count").textContent = run.sources_failed ? run.sources_failed : "";
    if (state.lastRunId !== null && state.lastRunId !== run.id) {
      toast(p.message || `${run.new_items} notícias novas.`, false);
      if (state.page === "reader") loadItems();
      if (state.page === "erros") loadErrors();
      if (state.page === "fontes") loadSources();
    }
    state.lastRunId = run.id;
  } else {
    status.textContent = "Nenhuma coleta ainda.";
    state.lastRunId = 0;
  }
}

$("#collect-btn").addEventListener("click", () => startCollect());

// --- Fontes -----------------------------------------------------------------

const TYPE_LABELS = { rss: "RSS", scrape: "Raspagem", cisa_kev: "CISA KEV", nvd: "NVD", github_advisories: "GitHub" };
const CATEGORY_LABELS = { seguranca: "Segurança", tecnologia: "Tecnologia" };

async function loadSources() {
  try {
    const data = await api("/api/sources");
    state.sources = data.sources;
    state.streaks = data.streaks;
  } catch (e) {
    toast(e.message, true);
    return;
  }
  renderSources();
}

function renderSources() {
  const filter = $("#source-filter").value.toLowerCase();
  const rows = state.sources.filter((s) => !filter || `${s.name} ${s.url}`.toLowerCase().includes(filter));
  $("#sources-table tbody").innerHTML = rows.map((s) => {
    const streak = state.streaks[s.id] || 0;
    const statusHtml = !s.enabled ? '<span class="muted">Desativada</span>' :
      streak ? `<span class="status-bad">Falhou ${streak}x seguida${streak > 1 ? "s" : ""}</span>` :
      '<span class="status-ok">● OK</span>';
    return `<tr class="${s.enabled ? "" : "disabled"}" data-id="${esc(s.id)}">
      <td><label class="switch"><input type="checkbox" class="toggle" ${s.enabled ? "checked" : ""}><span></span></label></td>
      <td><b>${esc(s.name)}</b><div class="url">${esc(s.url)}</div></td>
      <td><span class="pill">${esc(TYPE_LABELS[s.type] || s.type)}</span></td>
      <td>${esc(CATEGORY_LABELS[s.category] || s.category)}</td>
      <td>${statusHtml}</td>
      <td class="row-actions">
        <button class="btn small test">Testar</button>
        <button class="btn small edit">Editar</button>
        <button class="btn small ghost danger delete">Remover</button>
      </td></tr>`;
  }).join("");
  $$("#sources-table tbody tr").forEach((tr) => {
    const source = state.sources.find((s) => s.id === tr.dataset.id);
    $(".toggle", tr).onchange = async (ev) => {
      try {
        await api(`/api/sources/${encodeURIComponent(source.id)}`, { method: "PUT", body: { ...source, enabled: ev.target.checked } });
        loadSources();
      } catch (e) { toast(e.message, true); }
    };
    $(".edit", tr).onclick = () => openSourceDialog(source);
    $(".test", tr).onclick = async (ev) => {
      ev.target.disabled = true;
      ev.target.textContent = "Testando…";
      try {
        const res = await api("/api/sources/test", { method: "POST", body: source });
        toast(res.ok ? `${source.name}: ${res.count} notícias encontradas.` : `${source.name}: ${res.error}`, !res.ok);
      } catch (e) { toast(e.message, true); }
      ev.target.disabled = false;
      ev.target.textContent = "Testar";
    };
    $(".delete", tr).onclick = async () => {
      if (!confirm(`Remover a fonte "${source.name}"? As notícias já coletadas continuam no leitor.`)) return;
      await api(`/api/sources/${encodeURIComponent(source.id)}`, { method: "DELETE" });
      toast("Fonte removida.");
      loadSources();
    };
  });
}

$("#source-filter").addEventListener("input", renderSources);
$("#add-source").addEventListener("click", () => openSourceDialog(null));

const sourceForm = $("#source-form");
const SOURCE_FIELDS = ["name", "type", "url", "category", "language", "item_selector", "title_selector", "link_selector", "summary_selector"];

function openSourceDialog(source) {
  state.editingSource = source;
  $("#source-dialog-title").textContent = source ? `Editar "${source.name}"` : "Adicionar fonte";
  $(".discover").classList.toggle("hidden", !!source);
  $("#discover-url").value = "";
  $("#discover-result").innerHTML = "";
  $("#source-test-result").innerHTML = "";
  const s = source || { type: "rss", category: "tecnologia", language: "pt", enabled: true };
  for (const f of SOURCE_FIELDS) sourceForm.elements[f].value = s[f] || "";
  sourceForm.elements.type.value = s.type || "rss";
  sourceForm.elements.category.value = s.category || "tecnologia";
  sourceForm.elements.language.value = s.language || "pt";
  sourceForm.elements.enabled.checked = s.enabled !== false;
  toggleScrapeFields();
  $("#source-dialog").showModal();
}

function sourceFromForm() {
  const data = {};
  for (const f of SOURCE_FIELDS) data[f] = sourceForm.elements[f].value.trim();
  data.enabled = sourceForm.elements.enabled.checked;
  if (state.editingSource) {
    // Mantém opções extras que não aparecem no formulário (ex.: severity).
    return { ...state.editingSource, ...data };
  }
  return data;
}

function toggleScrapeFields() {
  $("#scrape-fields").classList.toggle("hidden", sourceForm.elements.type.value !== "scrape");
}
sourceForm.elements.type.addEventListener("change", toggleScrapeFields);
$("#source-cancel").addEventListener("click", () => $("#source-dialog").close());

$("#discover-btn").addEventListener("click", async () => {
  const url = $("#discover-url").value.trim();
  if (!url) return;
  const box = $("#discover-result");
  box.textContent = "Procurando…";
  try {
    const res = await api("/api/sources/discover", { method: "POST", body: { url } });
    if (!res.feeds.length) {
      box.innerHTML = 'Nenhum feed encontrado. Você pode usar o tipo "Página sem RSS" com seletores CSS.';
      sourceForm.elements.type.value = "scrape";
      sourceForm.elements.url.value = url;
      toggleScrapeFields();
      return;
    }
    box.innerHTML = "Feeds encontrados (clique para usar):" + res.feeds.map((f, i) =>
      `<a class="discover-option" data-i="${i}">${esc(f.title || f.url)} <span class="muted">${esc(f.url)}</span></a>`).join("");
    const use = (f) => {
      sourceForm.elements.url.value = f.url;
      sourceForm.elements.type.value = "rss";
      if (!sourceForm.elements.name.value) sourceForm.elements.name.value = f.title || "";
      toggleScrapeFields();
    };
    use(res.feeds[0]);
    $$(".discover-option", box).forEach((a) => a.onclick = () => use(res.feeds[Number(a.dataset.i)]));
  } catch (e) {
    box.textContent = `Erro: ${e.message}`;
  }
});

$("#source-test").addEventListener("click", async () => {
  const box = $("#source-test-result");
  box.textContent = "Testando…";
  try {
    const res = await api("/api/sources/test", { method: "POST", body: sourceFromForm() });
    if (!res.ok) {
      box.innerHTML = `<div class="test-preview status-bad">${esc(res.error)}</div>`;
      return;
    }
    box.innerHTML = `<div class="test-preview"><b class="status-ok">${res.count} notícias encontradas.</b> Exemplos:<ul>` +
      res.preview.map((p) => `<li>${p.level !== "normal" ? `<span class="badge ${p.level}">${p.level.toUpperCase()}</span> ` : ""}${esc(p.title)}</li>`).join("") +
      "</ul></div>";
  } catch (e) {
    box.textContent = `Erro: ${e.message}`;
  }
});

sourceForm.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const data = sourceFromForm();
  try {
    if (state.editingSource) {
      await api(`/api/sources/${encodeURIComponent(state.editingSource.id)}`, { method: "PUT", body: data });
    } else {
      await api("/api/sources", { method: "POST", body: data });
    }
  } catch (e) {
    $("#source-test-result").innerHTML = `<div class="test-preview status-bad">${esc(e.message)}</div>`;
    return;
  }
  $("#source-dialog").close();
  toast("Fonte salva.");
  loadSources();
});

// --- Erros ------------------------------------------------------------------

async function loadErrors() {
  let data;
  try { data = await api("/api/errors"); } catch (e) { toast(e.message, true); return; }
  const run = data.last_run;
  $("#errors-summary").textContent = run ?
    `Última coleta: ${fullDate(run.finished_at || run.started_at)} · ${run.sources_ok} fontes OK, ${run.sources_failed} com erro.` :
    "Nenhuma coleta feita ainda.";
  $("#retry-failed").classList.toggle("hidden", !data.current.length);
  $("#retry-failed").onclick = () => startCollect(data.current.map((e) => e.source_id));
  const box = $("#errors-current");
  if (!run) { box.innerHTML = ""; }
  else if (!data.current.length) {
    box.innerHTML = '<div class="ok-card">Todas as fontes funcionaram na última coleta.</div>';
  } else {
    box.innerHTML = data.current.map((e) => {
      const streak = data.streaks[e.source_id] || 1;
      return `<div class="error-card" data-id="${esc(e.source_id)}">
        <div><b>${esc(e.source_name)}</b> ${streak > 1 ? `<span class="status-bad small">· falhou ${streak} vezes seguidas</span>` : ""}
          <div class="msg">${esc(e.error)}</div>
          <div class="muted small">${esc(e.url || "")}</div></div>
        <div><button class="btn small edit">Corrigir fonte</button></div></div>`;
    }).join("");
    $$(".error-card .edit", box).forEach((btn) => btn.onclick = async () => {
      if (!state.sources.length) await loadSources();
      const source = state.sources.find((s) => s.id === btn.closest(".error-card").dataset.id);
      if (source) openSourceDialog(source); else toast("Essa fonte não existe mais.", true);
    });
  }
  $("#errors-history tbody").innerHTML = data.history.map((e) =>
    `<tr><td class="small">${esc(fullDate(e.at))}</td><td>${esc(e.source_name)}</td><td class="small">${esc(e.error)}</td></tr>`
  ).join("") || '<tr><td colspan="3" class="muted">Nenhum erro registrado.</td></tr>';
}

// --- Configurações ----------------------------------------------------------

function getPath(obj, path) { return path.split(".").reduce((o, k) => (o || {})[k], obj); }
function setPath(obj, path, value) {
  const keys = path.split(".");
  let o = obj;
  for (const k of keys.slice(0, -1)) o = o[k] = o[k] || {};
  o[keys.at(-1)] = value;
}

async function loadConfig() {
  let data;
  try { data = await api("/api/config"); } catch (e) { toast(e.message, true); return; }
  state.config = data.config;
  for (const el of $$("#page-config [name]")) {
    const value = getPath(state.config, el.name);
    if (el.type === "checkbox") el.checked = !!value;
    else if (el.dataset.list === "comma") el.value = (value || []).join(", ");
    else if (el.dataset.list === "lines") el.value = (value || []).join("\n");
    else el.value = value ?? "";
  }
  renderTechs();
  loadPreferences();
  $("#password-status").textContent = "Verificando senha…";
  api("/api/email/password").then((pw) => {
    $("#password-status").textContent = pw.has_password ? "Senha salva." :
      pw.keyring_available ? "Nenhuma senha salva." : "Cofre de senhas indisponível: use a variável MONITOR_SMTP_PASSWORD.";
  }).catch(() => { $("#password-status").textContent = ""; });
}

async function loadPreferences() {
  const box = $("#pref-status");
  let p;
  try { p = await api("/api/preferences"); } catch (_) { return; }
  const counts = `<b>${p.likes}</b> curtida${p.likes === 1 ? "" : "s"} · <b>${p.dislikes}</b> não curtida${p.dislikes === 1 ? "" : "s"}`;
  box.innerHTML = p.active ?
    `<p>${counts}. O ranking "Para você" está <b class="status-ok">ativo</b>.</p>` +
      (p.top_terms.length ? `<p class="muted small">O que mais pesa hoje:</p><div class="tags">${p.top_terms.map((t) => `<span class="tag plain">${esc(t)}</span>`).join("")}</div>` : "") :
    `<p>${counts}. Curta pelo menos ${p.min_likes} notícias para ativar o ranking "Para você".</p>`;
}

$("#pref-reset").addEventListener("click", async () => {
  if (!confirm("Apagar todas as curtidas e o que o programa aprendeu com elas?")) return;
  await api("/api/preferences/reset", { method: "POST" });
  toast("Preferências apagadas.");
  loadPreferences();
});

function renderTechs() {
  const techs = state.config.interests.technologies || [];
  $("#tech-tags").innerHTML = techs.map((t, i) => `<span class="tag">${esc(t)}<button data-i="${i}" title="Remover">×</button></span>`).join("") ||
    '<span class="muted small">Nenhuma tecnologia cadastrada.</span>';
  $$("#tech-tags button").forEach((b) => b.onclick = () => {
    techs.splice(Number(b.dataset.i), 1);
    renderTechs();
  });
}

function addTech() {
  const input = $("#tech-input");
  const techs = state.config.interests.technologies = state.config.interests.technologies || [];
  for (const t of input.value.split(",").map((s) => s.trim().toLowerCase()).filter(Boolean)) {
    if (!techs.includes(t)) techs.push(t);
  }
  input.value = "";
  renderTechs();
}
$("#tech-add").addEventListener("click", addTech);
$("#tech-input").addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); addTech(); } });

function configFromForm() {
  const cfg = structuredClone(state.config);
  for (const el of $$("#page-config [name]")) {
    let value;
    if (el.type === "checkbox") value = el.checked;
    else if (el.type === "number") value = el.value === "" ? null : Number(el.value);
    else if (el.dataset.list === "comma") value = el.value.split(",").map((s) => s.trim()).filter(Boolean);
    else if (el.dataset.list === "lines") value = el.value.split("\n").map((s) => s.trim()).filter(Boolean);
    else value = el.value.trim();
    setPath(cfg, el.name, value);
  }
  return cfg;
}

async function saveConfig(quiet = false) {
  const cfg = configFromForm();
  await api("/api/config", { method: "PUT", body: { config: cfg } });
  state.config = cfg;
  document.documentElement.dataset.theme = cfg.appearance.theme;
  if (!quiet) toast("Configurações salvas.");
}

$("#save-config").addEventListener("click", () => saveConfig().catch((e) => toast(e.message, true)));
$("#theme-select").addEventListener("change", (ev) => { document.documentElement.dataset.theme = ev.target.value; });

$("#email-provider").addEventListener("change", (ev) => {
  const preset = PRESETS[ev.target.value] || {};
  for (const [key, value] of Object.entries(preset)) {
    const el = $(`#page-config [name="email.${key}"]`);
    if (el) el.value = value;
  }
});

$("#save-password").addEventListener("click", async () => {
  const password = $("#email-password").value;
  if (!password) return toast("Digite a senha.", true);
  try {
    await saveConfig(true);
    await api("/api/email/password", { method: "POST", body: { password } });
    $("#email-password").value = "";
    $("#password-status").textContent = "Senha salva.";
    toast("Senha salva no cofre do sistema.");
  } catch (e) { toast(e.message, true); }
});

async function emailAction(path, body, okMessage) {
  const status = $("#email-status");
  status.className = "small muted";
  status.textContent = "Enviando…";
  try {
    await saveConfig(true);
    const res = await api(path, { method: "POST", body });
    status.className = "small status-ok";
    status.textContent = res && res.sent === false ? res.reason : okMessage(res);
  } catch (e) {
    status.className = "small status-bad";
    status.textContent = e.message;
  }
}

$("#email-test").addEventListener("click", () => emailAction("/api/email/test", {}, () => "E-mail de teste enviado."));
$("#email-send").addEventListener("click", () => emailAction("/api/email/send", {}, (r) => `Resumo enviado com ${r.items} notícias.`));
$("#email-preview").addEventListener("click", () => {
  window.open(`/email/preview?t=${encodeURIComponent(TOKEN)}&all=1`, "_blank", "width=720,height=900");
});

// --- Início -----------------------------------------------------------------

loadItems();
renderEmptyReader();
pollStatus();
setInterval(() => { if (state.page === "reader") loadCounts(); }, 60000);
