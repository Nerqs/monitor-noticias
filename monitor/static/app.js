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
  sawRunning: false,
  unreadOnly: true,
  loadSeq: 0,
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
  tecnologia: "Tecnologia", favoritos: "Favoritos", todos: "Todas as notícias", silenciadas: "Silenciadas",
  games_destaques: "Destaques de games", games_meus: "Meus jogos", games_paravoce: "Games para você",
  games_favoritos: "Games favoritos", games: "Todas de games",
};

// Games ficam numa área separada: abas, fontes e curtidas próprias.
const isGamesView = (view) => view.startsWith("games");
const areaOfView = (view) => (isGamesView(view) ? "games" : "tech");
const isGame = (item) => item.category === "games";

function badges(item) {
  let html = "";
  if (isGame(item)) {
    if (item.level !== "normal") html += '<span class="badge destaque">DESTAQUE</span>';
  } else if (item.level === "critico") html += '<span class="badge critico">CRÍTICO</span>';
  else if (item.level === "alto") html += '<span class="badge alto">ALTO</span>';
  if (item.kev) html += '<span class="badge kev" title="Catálogo de falhas exploradas da CISA">EXPLORADA</span>';
  if (item.version_verdict === "afeta") {
    html += `<span class="badge afeta" title="${esc(item.version_note)}">AFETA VOCÊ</span>`;
  } else if (item.version_verdict === "nao_afeta") {
    html += `<span class="badge safe" title="${esc(item.version_note)}">SUA VERSÃO OK</span>`;
  }
  if (item.epss != null && item.epss >= 0.1) {
    html += `<span class="badge epss" title="Chance de exploração nos próximos 30 dias (EPSS)">EPSS ${Math.round(item.epss * 100)}%</span>`;
  }
  if (item.affinity >= 65 && item.liked !== -1) {
    html += `<span class="badge like" title="Afinidade com o que você curtiu">♥ ${Math.round(item.affinity)}%</span>`;
  }
  if (item.matched_tech) {
    for (const t of item.matched_tech.split(",")) html += `<span class="badge ${isGame(item) ? "game" : "tech"}">${esc(t)}</span>`;
  }
  return html;
}

async function openExternal(url, confirmed = false) {
  let res;
  try {
    res = await api("/api/open", { method: "POST", body: { url, confirm: confirmed } });
  } catch (e) {
    toast(e.message, true);
    return;
  }
  if (res.needs_confirm) {
    const dialog = $("#link-dialog");
    $("#link-host").textContent = res.display || res.host;
    $("#link-reasons").innerHTML = res.reasons.map((r) => `<li>${esc(capitalize(r))}.</li>`).join("");
    $("#link-url").textContent = url;
    dialog.returnValue = "";
    dialog.onclose = () => { if (dialog.returnValue === "open") openExternal(url, true); };
    dialog.showModal();
  }
}

function hostOf(url) {
  try { return new URL(url).hostname.replace(/^www\./, ""); } catch (_) { return ""; }
}

// --- Navegação --------------------------------------------------------------

const PAGE_TITLES = { fontes: "Fontes", erros: "Erros de coleta", saude: "Saúde", config: "Configurações" };
const mobile = () => window.matchMedia("(max-width: 900px)").matches;

function setReading(on) {
  $(".app").classList.toggle("reading", on);
  if (on && mobile()) $("#read-pane").scrollTop = 0;
}

function backButton() {
  return `<button class="btn back-btn" id="act-back">${icon("back")}<span>Voltar</span></button>`;
}

function bindBack() {
  const back = $("#act-back");
  if (back) back.onclick = () => setReading(false);
}

$("#menu-btn").addEventListener("click", () => $(".app").classList.add("nav-open"));
$("#scrim").addEventListener("click", () => $(".app").classList.remove("nav-open"));
$("#mobile-collect").addEventListener("click", () => startCollect());

function showPage(page) {
  state.page = page;
  $(".app").classList.remove("nav-open");
  if (page !== "reader") setReading(false);
  $("#mobile-title").textContent = page === "reader" ? VIEW_TITLES[state.view] : PAGE_TITLES[page];
  for (const id of ["reader", "fontes", "erros", "saude", "config"]) {
    $(`#page-${id}`).classList.toggle("hidden", id !== page);
  }
  $$(".nav-item").forEach((b) => {
    b.classList.toggle("active", page === "reader" ? b.dataset.view === state.view : b.dataset.page === page);
  });
  if (page === "fontes") loadSources();
  if (page === "erros") loadErrors();
  if (page === "config") loadConfig();
  if (page === "saude") loadHealth();
}

$$(".nav-item").forEach((btn) => {
  btn.addEventListener("click", () => {
    if (btn.dataset.view) {
      state.view = btn.dataset.view;
      state.selectedId = null;
      applyArea();
      setReading(false);
      showPage("reader");
      loadItems();
      renderEmptyReader();
    } else {
      showPage(btn.dataset.page);
    }
  });
});

// --- Leitor -----------------------------------------------------------------

const isFavoritesView = (view) => ["favoritos", "games_favoritos"].includes(view);
const FILTER_DEFAULTS = { days: "0", "level-filter": "", "source-select": "", sort: "" };

async function loadItems() {
  // Só a resposta do pedido mais recente vale: uma resposta atrasada de outra aba
  // ou filtro não pode trocar a lista que está na tela.
  const seq = ++state.loadSeq;
  updateFilterUI();
  const params = new URLSearchParams({
    view: state.view,
    q: $("#search").value,
    unread: state.unreadOnly && !isFavoritesView(state.view) ? "1" : "0",
    days: $("#days").value,
    sort: $("#sort").value,
    level: $("#level-filter").value,
    source: $("#source-select").value,
  });
  let prefs = state.prefs, items;
  try {
    if (["paravoce", "games_paravoce"].includes(state.view)) prefs = await api(`/api/preferences?area=${areaOfView(state.view)}`);
    items = await api(`/api/items?${params}`);
  } catch (e) {
    if (seq !== state.loadSeq) return;
    $("#item-list").innerHTML = `<div class="list-empty">${icon("error")}<p>Não foi possível carregar as notícias.</p>
      <p class="small">${esc(e.message)}</p><button class="btn small" id="list-retry">${icon("refresh")}Tentar de novo</button></div>`;
    $("#list-retry").onclick = loadItems;
    return;
  }
  if (seq !== state.loadSeq) return;
  state.prefs = prefs;
  state.items = items;
  renderList();
  loadCounts();
  $("#list-title").textContent = VIEW_TITLES[state.view];
  const unread = state.items.filter((i) => !i.is_read).length;
  $("#list-sub").textContent = `${state.items.length} notícia${state.items.length === 1 ? "" : "s"}` +
    (unread ? ` · ${unread} não lida${unread === 1 ? "" : "s"}` : "");
}

function renderList() {
  const list = $("#item-list");
  if (["paravoce", "games_paravoce"].includes(state.view) && state.prefs && !state.prefs.active) {
    const left = state.prefs.min_likes - state.prefs.likes;
    list.innerHTML = `<div class="list-hint">${icon("sparkle")}<span>Use <b>Gostei</b> em mais ${left} notícia${left > 1 ? "s" : ""}
      para o programa aprender seu gosto. Até lá, esta lista segue a ordem de prioridade.</span></div>`;
  } else {
    list.innerHTML = "";
  }
  const cveSearch = $("#search").value.trim().match(/^CVE-\d{4}-\d{4,7}$/i);
  if (cveSearch) {
    list.insertAdjacentHTML("afterbegin", `<div class="list-hint cve-hint" id="cve-hint">${icon("shield")}
      <span>Ver tudo sobre <b>${esc(cveSearch[0].toUpperCase())}</b>: gravidade, chance de exploração, correção e linha do tempo.</span></div>`);
    $("#cve-hint").onclick = () => { state.selectedId = null; showCve(cveSearch[0].toUpperCase()); };
  }
  if (!state.items.length) {
    list.innerHTML = emptyState();
    $("#empty-all")?.addEventListener("click", () => setUnreadOnly(false));
    $("#empty-clear")?.addEventListener("click", clearFilters);
    $("#empty-collect")?.addEventListener("click", () => startCollect());
    return;
  }
  list.insertAdjacentHTML("beforeend", state.items.map((item) => `
    <div class="item lvl-${item.level} ${isGame(item) ? "game" : ""} ${item.is_read ? "" : "unread"} ${item.id === state.selectedId ? "selected" : ""}" data-id="${item.id}">
      <div class="meta">${badges(item)}<span>${esc(item.source_name)}</span><span>·</span><span>${esc(timeAgo(item.published))}</span>${item.starred ? '<span class="star">★</span>' : ""}</div>
      <div class="title">${esc(item.title)}</div>
    </div>`).join(""));
  $$(".item", list).forEach((el) => el.addEventListener("click", () => selectItem(Number(el.dataset.id))));
}

function emptyState() {
  const searching = $("#search").value.trim() !== "";
  if (searching || filtersActive()) {
    return `<div class="list-empty">${icon("search")}<p><b>Nada com esses filtros.</b></p>
      <p class="small">Tente outra busca ou volte os filtros ao padrão.</p>
      <button class="btn small" id="empty-clear">Limpar filtros</button></div>`;
  }
  if (state.unreadOnly && !isFavoritesView(state.view)) {
    return `<div class="list-empty">${icon("check")}<p><b>Tudo lido por aqui.</b></p>
      <p class="small">Não há notícias novas nesta lista.</p>
      <button class="btn small" id="empty-all">Mostrar também as lidas</button></div>`;
  }
  if (isFavoritesView(state.view)) {
    return `<div class="list-empty">${icon("star")}<p><b>Nenhum favorito ainda.</b></p>
      <p class="small">Use a estrela (ou a tecla <kbd>s</kbd>) numa notícia para guardá-la aqui.</p></div>`;
  }
  return `<div class="list-empty">${icon("inbox")}<p><b>Nenhuma notícia.</b></p>
    <button class="btn small primary" id="empty-collect">${icon("refresh")}Coletar agora</button></div>`;
}

// --- Filtros ----------------------------------------------------------------

function filtersActive() {
  return Object.entries(FILTER_DEFAULTS).some(([id, value]) => $(`#${id}`).value !== value);
}

function updateFilterUI() {
  const favorites = isFavoritesView(state.view);
  const seg = $("#read-filter");
  seg.classList.toggle("disabled", favorites);
  seg.title = favorites ? "Favoritos mostram lidas e não lidas." : "";
  $$("button", seg).forEach((b) => {
    const on = !favorites && (b.dataset.unread === "1") === state.unreadOnly;
    b.classList.toggle("on", on);
    b.setAttribute("aria-checked", on ? "true" : "false");
  });
  for (const [id, value] of Object.entries(FILTER_DEFAULTS)) {
    $(`#${id}`).closest(".chip-select").classList.toggle("active", $(`#${id}`).value !== value);
  }
  $("#filters-clear").classList.toggle("hidden", !filtersActive() && !$("#search").value);
}

function setUnreadOnly(on, reload = true) {
  state.unreadOnly = on;
  if (reload) loadItems();
  else updateFilterUI();
}

function clearFilters() {
  for (const [id, value] of Object.entries(FILTER_DEFAULTS)) $(`#${id}`).value = value;
  $("#search").value = "";
  loadItems();
}

$$("#read-filter button").forEach((b) => b.addEventListener("click", () => {
  if (!isFavoritesView(state.view)) setUnreadOnly(b.dataset.unread === "1");
}));
$("#filters-clear").addEventListener("click", clearFilters);

async function renderEmptyReader() {
  let counts = {}, status = {}, healthInfo = {};
  try {
    [counts, status, healthInfo] = await Promise.all([api("/api/counts"), api("/api/status"), api("/api/health")]);
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
      <h1>${greeting}.${isGamesView(state.view) ? ' <span class="muted">Games</span>' : ""}</h1>
      <p class="muted">${run && run.finished_at ? `Última coleta ${timeAgo(run.finished_at)}, com ${run.new_items} notícias novas.` :
        "Nenhuma coleta feita ainda. Clique em <b>Coletar agora</b> para começar."}</p>
      <div class="tiles">${isGamesView(state.view) ? `
        ${tile(counts.games_destaques, "destaques de games não lidos", "games_destaques", "game")}
        ${tile(counts.games_meus, "dos jogos que você acompanha", "games_meus", "accent")}
        ${tile(counts.games, "notícias de games não lidas", "games")}
        ${counts.games_paravoce ? tile(counts.games_paravoce, "parecidas com o que você curtiu", "games_paravoce", "like") : ""}` : `
        ${tile(counts.criticos, "críticas e importantes não lidas", "criticos", "danger")}
        ${tile(counts.minhas, "citam suas tecnologias", "minhas", "accent")}
        ${tile(counts.seguranca, "de segurança não lidas", "seguranca")}
        ${tile(counts.tecnologia, "de tecnologia não lidas", "tecnologia")}
        ${counts.paravoce ? tile(counts.paravoce, "parecidas com o que você curtiu", "paravoce", "like") : ""}
        ${counts.games ? tile(counts.games, "notícias de games não lidas (área Games)", "games", "game") : ""}`}
      </div>
      ${run && run.sources_failed ? `<button class="notice" data-page-goto="erros">${icon("error")}
        <span><b>${run.sources_failed} fonte${run.sources_failed > 1 ? "s" : ""} com erro</b> na última coleta. Ver detalhes</span></button>` : ""}
      ${healthNotices(healthInfo)}
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
  setReading(true);
  if (!item.is_read) setFlag(item, "is_read", true, false);
}

function renderItem(item) {
  const cves = (item.cves || "").split(",").filter(Boolean);
  const related = item.related || [];
  const levelLabel = isGame(item) ? (item.level === "normal" ? "Por que está aqui" : "Destaque") :
    { critico: "Prioridade crítica", alto: "Prioridade alta", normal: "Por que está aqui" }[item.level];
  const reasonsClass = isGame(item) && item.level !== "normal" ? "game" : `lvl-${item.level}`;
  $("#read-pane").innerHTML = `
    <div class="read-toolbar">
      ${backButton()}
      <button class="btn" id="act-article">${icon("book")}<span>Ler artigo aqui</span></button>
      <button class="btn" id="act-open" title="Abre ${esc(hostOf(item.url))} no navegador">${icon("external")}<span>Abrir <span class="host">${esc(hostOf(item.url))}</span></span></button>
      <span class="spacer"></span>
      <button class="btn like-btn ${item.liked === 1 ? "liked" : ""}" id="act-like" title="Gostei (g)">${icon("up")}<span>Gostei</span></button>
      <button class="btn like-btn ${item.liked === -1 ? "disliked" : ""}" id="act-dislike" title="Não curti (n)">${icon("down")}<span>Não curti</span></button>
      <button class="btn icon-btn ${item.starred ? "on" : ""}" id="act-star" title="Favoritar (s)">${icon("star")}</button>
      <button class="btn icon-btn" id="act-read" title="${item.is_read ? "Marcar como não lida" : "Marcar como lida"} (m)">${icon("check")}</button>
    </div>
    <article class="article">
      <div class="meta">${badges(item)}<span>${esc(item.source_name)}</span><span>·</span>
        <span title="${esc(fullDate(item.published))}">${esc(fullDate(item.published))}</span>
        ${item.cvss != null ? `<span>· CVSS ${Number(item.cvss).toFixed(1)}</span>` : ""}
        ${item.epss != null && item.epss < 0.1 ? `<span title="Chance de exploração nos próximos 30 dias">· EPSS ${(item.epss * 100).toFixed(1)}%</span>` : ""}</div>
      <h1>${esc(item.title)}</h1>
      ${cves.length ? `<div class="meta">${cves.map((c) => `<a class="cve" href="#" data-cve="${esc(c)}" title="Tudo sobre ${esc(c)}">${esc(c)}</a>`).join(" ")}</div>` : ""}
      ${item.reasons ? `<div class="reasons ${reasonsClass}"><b>${levelLabel}</b><span>${esc(item.reasons)}</span></div>` : ""}
      ${item.affinity_terms && item.affinity_terms.length && item.liked !== -1 ? `<div class="affinity-note">${icon("sparkle")}
        <span>Parecida com o que você curtiu: ${item.affinity_terms.map(esc).join(", ")}</span></div>` : ""}
      ${item.summary ? `<div class="summary">${esc(item.summary)}</div>` : ""}
      <div id="article-body"></div>
      ${related.length ? `<div class="related"><h3>Também citado em</h3>${related.map((r) =>
        `<a data-id="${r.id}">${esc(r.source_name)}: ${esc(r.title)}</a>`).join("")}</div>` : ""}
    </article>`;
  bindBack();
  $("#act-open").onclick = () => openExternal(item.url);
  $("#act-star").onclick = () => setFlag(item, "starred", !item.starred);
  $("#act-read").onclick = () => setFlag(item, "is_read", !item.is_read);
  $("#act-article").onclick = () => loadArticle(item);
  $("#act-like").onclick = () => setLike(item, item.liked === 1 ? 0 : 1);
  $("#act-dislike").onclick = () => setLike(item, item.liked === -1 ? 0 : -1);
  $$(".cve[data-cve]").forEach((a) => a.addEventListener("click", (ev) => { ev.preventDefault(); showCve(a.dataset.cve, item); }));
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
$("#days").addEventListener("change", loadItems);
$("#sort").addEventListener("change", loadItems);
$("#level-filter").addEventListener("change", loadItems);
$("#source-select").addEventListener("change", loadItems);

async function fillSourceSelects() {
  try {
    const data = await api("/api/sources");
    state.sources = data.sources;
    state.streaks = data.streaks;
    state.health = data.health;
    state.silent = data.silent;
  } catch (_) { return; }
  const option = (s) => `<option value="${esc(s.id)}">${esc(s.name)}</option>`;
  const rule = $("#rule-source");
  const keep = rule.value;
  rule.innerHTML = rule.firstElementChild.outerHTML + state.sources.map(option).join("");
  rule.value = keep;
  renderSourceSelect();
}

// O filtro de fonte do leitor só mostra as fontes da área aberta (tecnologia ou games).
function renderSourceSelect() {
  const el = $("#source-select");
  const keep = el.value;
  const games = isGamesView(state.view);
  const sources = (state.sources || []).filter((s) => (s.category === "games") === games);
  el.innerHTML = el.firstElementChild.outerHTML +
    sources.map((s) => `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join("");
  el.value = sources.some((s) => s.id === keep) ? keep : "";
}

function applyArea() {
  const games = isGamesView(state.view);
  $(".app").classList.toggle("area-games", games);
  $("#level-chip").classList.toggle("hidden", games);
  if (games) $("#level-filter").value = "";
  renderSourceSelect();
}
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
    state.sawRunning = true;
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
    // Recarrega a lista quando termina uma coleta, inclusive a que já estava
    // rodando quando o programa abriu (agendada ou com --coletar).
    if (state.sawRunning || (state.lastRunId !== null && state.lastRunId !== run.id)) {
      toast(p.message || `${run.new_items} notícias novas.`, false);
      if (state.page === "reader") loadItems();
      if (state.page === "erros") loadErrors();
      if (state.page === "fontes") loadSources();
    }
    state.lastRunId = run.id;
    state.sawRunning = false;
  } else {
    status.textContent = "Nenhuma coleta ainda.";
    state.lastRunId = 0;
  }
}

$("#collect-btn").addEventListener("click", () => startCollect());

function healthNotices(h) {
  if (!h || !h.schedule) return "";
  let html = "";
  if (h.schedule.missed) {
    html += `<button class="notice warn" data-page-goto="saude">${icon("pulse")}<span><b>${esc(h.schedule.message)}</b>
      ${esc(h.schedule.fix || "")}</span></button>`;
  }
  const findings = (h.selfcheck && h.selfcheck.findings) || [];
  if (findings.length) {
    const pkgs = [...new Set(findings.map((f) => f.package))].join(", ");
    html += `<button class="notice" data-page-goto="saude">${icon("bug")}<span><b>As bibliotecas do programa têm
      ${findings.length} falha${findings.length > 1 ? "s" : ""} conhecida${findings.length > 1 ? "s" : ""}</b> (${esc(pkgs)}). Ver como atualizar</span></button>`;
  }
  if (h.silent && h.silent.length) {
    html += `<button class="notice warn" data-page-goto="erros">${icon("rss")}<span><b>${h.silent.length} fonte${h.silent.length > 1 ? "s" : ""}
      sem nada novo há dias</b> (${esc(h.silent.map((s) => s.source_name).slice(0, 3).join(", "))}). Pode ser feed quebrado</span></button>`;
  }
  const badge = (h.schedule.missed ? 1 : 0) + (findings.length ? 1 : 0);
  $("#health-count").textContent = badge || "";
  return html;
}

// --- Página do CVE ----------------------------------------------------------

async function showCve(cveId, fromItem = null) {
  $("#read-pane").innerHTML = `<div class="article"><div class="article-loading">Buscando ${esc(cveId)} no NVD e no EPSS…</div></div>`;
  let d;
  try { d = await api(`/api/cve/${encodeURIComponent(cveId)}`); } catch (e) {
    $("#read-pane").innerHTML = `<div class="article"><div class="article-loading">Não foi possível carregar: ${esc(e.message)}</div></div>`;
    return;
  }
  const n = d.nvd;
  const sev = n && n.cvss != null ? (n.cvss >= 9 ? "critico" : n.cvss >= 7 ? "alto" : "normal") : "normal";
  const fact = (label, value, cls = "") => `<div class="fact ${cls}"><span class="fact-l">${label}</span><span class="fact-v">${value}</span></div>`;
  const timeline = [];
  if (n && n.published) timeline.push({ at: n.published, text: "Publicado no NVD" });
  if (n && n.kev_added) timeline.push({ at: n.kev_added, text: "Entrou no catálogo de falhas exploradas (CISA KEV)", cls: "kev" });
  if (n && n.kev_due) timeline.push({ at: n.kev_due, text: "Prazo da CISA para corrigir", cls: "kev" });
  for (const item of d.news) timeline.push({ at: (item.published || item.fetched_at).replace(" ", "T") + "Z", text: `${item.source_name}: ${item.title}`, id: item.id });
  timeline.sort((a, b) => new Date(a.at) - new Date(b.at));
  const tags = (r) => r.tags.length ? ` <span class="muted small">(${esc(r.tags.join(", "))})</span>` : "";
  const keyRefs = n ? n.references.filter((r) => r.tags.some((t) => ["Patch", "Exploit", "Vendor Advisory", "Mitigation"].includes(t))) : [];
  const otherRefs = n ? n.references.filter((r) => !keyRefs.includes(r)).slice(0, 8) : [];
  const ranges = n ? n.ranges.slice(0, 12).map((r) => {
    const parts = [];
    if (r.start_incl) parts.push(`a partir de ${r.start_incl}`);
    if (r.start_excl) parts.push(`depois de ${r.start_excl}`);
    if (r.end_incl) parts.push(`até ${r.end_incl}`);
    if (r.end_excl) parts.push(`antes de ${r.end_excl}`);
    const version = r.version && !["*", "-"].includes(r.version) ? r.version : (parts.join(", ") || "todas as versões");
    return `<li><code>${esc(r.product)}</code> ${esc(version)}</li>`;
  }).join("") : "";
  $("#read-pane").innerHTML = `
    <div class="read-toolbar">
      ${fromItem ? `<button class="btn" id="cve-back">← Voltar à notícia</button>` : backButton()}
      <button class="btn" id="cve-nvd">${icon("external")}<span>Abrir no NVD</span></button>
      <span class="spacer"></span>
    </div>
    <article class="article cve-page">
      <div class="meta"><span class="badge ${sev}">${n && n.severity ? esc(n.severity) : "CVE"}</span>
        ${d.kev ? '<span class="badge kev">EXPLORADA</span>' : ""}
        ${d.verdict === "afeta" ? '<span class="badge afeta">AFETA VOCÊ</span>' : d.verdict === "nao_afeta" ? '<span class="badge safe">SUA VERSÃO OK</span>' : ""}</div>
      <h1>${esc(d.id)}${n && n.kev_name ? `: ${esc(n.kev_name)}` : ""}</h1>
      <div class="facts">
        ${fact("CVSS", n && n.cvss != null ? n.cvss.toFixed(1) : "—", sev)}
        ${fact("Chance de exploração (30 dias)", d.epss != null ? `${(d.epss * 100).toFixed(d.epss < 0.1 ? 1 : 0)}%` : "—", d.epss >= 0.5 ? "critico" : d.epss >= 0.1 ? "alto" : "")}
        ${fact("Explorada ativamente", d.kev ? "Sim (CISA KEV)" : "Não consta", d.kev ? "critico" : "")}
        ${fact("Correção publicada", n ? (n.has_patch ? "Sim" : "Não indicada") : "—", n && n.has_patch ? "ok" : "")}
        ${fact("Exploit público", n ? (n.has_exploit ? "Sim" : "Não indicado") : "—", n && n.has_exploit ? "critico" : "")}
      </div>
      ${d.verdict ? `<div class="reasons lvl-${d.verdict === "afeta" ? "critico" : "normal"}"><b>${d.verdict === "afeta" ? "Sua versão é afetada" : "Sua versão não aparece como afetada"}</b><span>${esc(d.verdict_note)}</span></div>` : ""}
      ${n && n.kev_action ? `<div class="reasons lvl-alto"><b>Ação recomendada pela CISA</b><span>${esc(n.kev_action)}</span></div>` : ""}
      ${n ? `<div class="summary">${esc(n.description)}</div>` : `<p class="muted">${esc(d.nvd_error || "O NVD ainda não tem detalhes deste CVE.")}</p>`}
      ${n && n.weaknesses.length ? `<p class="muted small">Tipo de falha: ${n.weaknesses.map(esc).join(", ")} · Vetor: <code>${esc(n.vector)}</code></p>` : ""}
      ${ranges ? `<h3>Versões afetadas</h3><ul class="ranges">${ranges}</ul>` : ""}
      <h3>Linha do tempo</h3>
      <ol class="timeline">${timeline.map((t) => `<li class="${t.cls || ""}"><span class="when">${esc(new Date(t.at).toLocaleDateString("pt-BR"))}</span>
        ${t.id ? `<a data-id="${t.id}">${esc(t.text)}</a>` : `<span>${esc(t.text)}</span>`}</li>`).join("")}</ol>
      ${keyRefs.length ? `<h3>Correções e avisos</h3><ul class="refs">${keyRefs.map((r) => `<li><a href="#" data-url="${esc(r.url)}">${esc(hostOf(r.url))}</a>${tags(r)}</li>`).join("")}</ul>` : ""}
      ${otherRefs.length ? `<h3>Outras referências</h3><ul class="refs">${otherRefs.map((r) => `<li><a href="#" data-url="${esc(r.url)}">${esc(hostOf(r.url))}</a>${tags(r)}</li>`).join("")}</ul>` : ""}
    </article>`;
  if (fromItem) $("#cve-back").onclick = () => renderItem(fromItem);
  bindBack();
  setReading(true);
  $("#cve-nvd").onclick = () => openExternal(`https://nvd.nist.gov/vuln/detail/${d.id}`);
  $$(".timeline a[data-id]").forEach((a) => a.onclick = () => selectItem(Number(a.dataset.id)));
  $$(".refs a[data-url]").forEach((a) => a.onclick = (ev) => { ev.preventDefault(); openExternal(a.dataset.url); });
}

// --- Fontes -----------------------------------------------------------------

const TYPE_LABELS = { rss: "RSS", scrape: "Raspagem", cisa_kev: "CISA KEV", nvd: "NVD", github_advisories: "GitHub" };
const CATEGORY_LABELS = { seguranca: "Segurança", tecnologia: "Tecnologia", games: "Games" };

async function loadSources() {
  try {
    const data = await api("/api/sources");
    state.sources = data.sources;
    state.streaks = data.streaks;
    state.health = data.health;
    state.silent = data.silent;
  } catch (e) {
    toast(e.message, true);
    return;
  }
  renderSources();
}

function renderSources() {
  const filter = $("#source-filter").value.toLowerCase();
  const area = $("#source-area").value;
  const rows = state.sources.filter((s) => (!filter || `${s.name} ${s.url}`.toLowerCase().includes(filter)) &&
    (!area || (s.category === "games") === (area === "games")));
  $("#sources-table tbody").innerHTML = rows.map((s) => {
    const streak = state.streaks[s.id] || 0;
    const silent = (state.silent || []).includes(s.id);
    const statusHtml = !s.enabled ? '<span class="muted">Desativada</span>' :
      streak ? `<span class="status-bad">Falhou ${streak}x seguida${streak > 1 ? "s" : ""}</span>` :
      silent ? '<span class="status-warn" title="Responde, mas não traz nada novo há dias">● Silenciosa</span>' :
      '<span class="status-ok">● OK</span>';
    const h = (state.health || {})[s.id];
    const healthHtml = !h || !h.runs ? '<span class="muted small">sem dados</span>' :
      `<div class="health-cell"><span class="${h.success_rate >= 90 ? "status-ok" : h.success_rate >= 60 ? "status-warn" : "status-bad"}">${h.success_rate}% OK</span>
       <span class="muted small">${h.median_ms != null ? (h.median_ms / 1000).toFixed(1) + " s · " : ""}${h.per_day}/dia${h.likes ? ` · ♥ ${h.likes}` : ""}</span></div>`;
    return `<tr class="${s.enabled ? "" : "disabled"}" data-id="${esc(s.id)}">
      <td><label class="switch"><input type="checkbox" class="toggle" ${s.enabled ? "checked" : ""}><span></span></label></td>
      <td><b>${esc(s.name)}</b><div class="url">${esc(s.url)}</div></td>
      <td><span class="pill">${esc(TYPE_LABELS[s.type] || s.type)}</span></td>
      <td>${esc(CATEGORY_LABELS[s.category] || s.category)}</td>
      <td>${statusHtml}</td>
      <td>${healthHtml}</td>
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
$("#source-area").addEventListener("change", renderSources);
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
  const s = source || { type: "rss", category: $("#source-area").value === "games" ? "games" : "tecnologia", language: "pt", enabled: true };
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
  const silentBox = $("#errors-silent");
  silentBox.innerHTML = data.silent.length ? `<h2>Fontes silenciosas</h2>
    <p class="muted small">Respondem normalmente, mas não trazem nada novo há dias. Costuma ser feed que mudou de endereço.</p>` +
    data.silent.map((s) => `<div class="error-card warn" data-id="${esc(s.source_id)}">
      <div><b>${esc(s.source_name)}</b>
        <div class="msg">${s.days != null ? `Nada novo há ${s.days} dias` : "Nunca trouxe notícia nova"} (limite: ${s.limit} dias).</div>
        <div class="muted small">${esc(s.url || "")}</div></div>
      <div><button class="btn small edit">Revisar fonte</button></div></div>`).join("") : "";
  $$(".error-card.warn .edit", silentBox).forEach((btn) => btn.onclick = async () => {
    if (!state.sources.length) await loadSources();
    const source = state.sources.find((s) => s.id === btn.closest(".error-card").dataset.id);
    if (source) openSourceDialog(source);
  });
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
  if (!state.sources.length) await fillSourceSelects();
  renderTechs();
  renderGames();
  renderVersions();
  renderRules();
  renderChat();
  renderAuth();
  loadPreferences();
  $("#password-status").textContent = "Verificando senha…";
  api("/api/email/password").then((pw) => {
    $("#password-status").textContent = pw.has_password ? "Senha salva." :
      pw.keyring_available ? "Nenhuma senha salva." : "Cofre de senhas indisponível: use a variável MONITOR_SMTP_PASSWORD.";
  }).catch(() => { $("#password-status").textContent = ""; });
}

async function loadPreferences() {
  for (const area of ["tech", "games"]) loadAreaPreferences(area);
}

async function loadAreaPreferences(area) {
  const box = $(area === "games" ? "#pref-status-games" : "#pref-status");
  let p;
  try { p = await api(`/api/preferences?area=${area}`); } catch (_) { return; }
  const counts = `<b>${p.likes}</b> curtida${p.likes === 1 ? "" : "s"} · <b>${p.dislikes}</b> não curtida${p.dislikes === 1 ? "" : "s"}`;
  box.innerHTML = p.active ?
    `<p>${counts}. O ranking "Para você" está <b class="status-ok">ativo</b>.</p>` +
      (p.top_terms.length ? `<p class="muted small">O que mais pesa hoje:</p><div class="tags">${p.top_terms.map((t) => `<span class="tag plain">${esc(t)}</span>`).join("")}</div>` : "") :
    `<p>${counts}. Curta pelo menos ${p.min_likes} notícias para ativar o ranking "Para você".</p>`;
}

$("#pref-reset").addEventListener("click", async () => {
  if (!confirm("Apagar as curtidas de tecnologia e o que o programa aprendeu com elas?")) return;
  await api("/api/preferences/reset", { method: "POST", body: { area: "tech" } });
  toast("Preferências de tecnologia apagadas.");
  loadPreferences();
});
$("#pref-reset-games").addEventListener("click", async () => {
  if (!confirm("Apagar as curtidas de games e o que o programa aprendeu com elas?")) return;
  await api("/api/preferences/reset", { method: "POST", body: { area: "games" } });
  toast("Preferências de games apagadas.");
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
function renderGames() {
  const games = state.config.games = state.config.games || {};
  const list = games.following = games.following || [];
  $("#game-tags").innerHTML = list.map((t, i) => `<span class="tag">${esc(t)}<button data-i="${i}" title="Remover">×</button></span>`).join("") ||
    '<span class="muted small">Nenhum jogo cadastrado.</span>';
  $$("#game-tags button").forEach((b) => b.onclick = () => {
    list.splice(Number(b.dataset.i), 1);
    renderGames();
  });
}

function addGame() {
  const input = $("#game-input");
  const list = state.config.games.following = state.config.games.following || [];
  for (const t of input.value.split(",").map((s) => s.trim().toLowerCase()).filter(Boolean)) {
    if (!list.includes(t)) list.push(t);
  }
  input.value = "";
  renderGames();
}
$("#game-add").addEventListener("click", addGame);
$("#game-input").addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); addGame(); } });

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
  cfg.interests.versions = (cfg.interests.versions || []).filter((v) => v.cpe && v.version);
  const res = await api("/api/config", { method: "PUT", body: { config: cfg } });
  state.config = cfg;
  document.documentElement.dataset.theme = cfg.appearance.theme;
  if (!quiet) toast(res.rescored ? `Configurações salvas. Prioridade de ${res.rescored} notícias recalculada.` : "Configurações salvas.");
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
$("#email-send").addEventListener("click", () => emailAction("/api/email/send", {}, (r) =>
  `Resumo enviado com ${r.items} notícias${r.emails > 1 ? " (dois e-mails: tecnologia e games)" : ""}.`));
$("#email-preview-games").addEventListener("click", () => {
  window.open(`/email/preview?t=${encodeURIComponent(TOKEN)}&all=1&area=games`, "_blank", "width=720,height=900");
});
$("#email-preview").addEventListener("click", () => {
  window.open(`/email/preview?t=${encodeURIComponent(TOKEN)}&all=1`, "_blank", "width=720,height=900");
});

// --- Saúde do programa --------------------------------------------------------

const CHECK_LABEL = { ok: "OK", aviso: "Atenção", erro: "Erro" };

function renderChecks(checks) {
  $("#diag-list").innerHTML = checks.map((c) => `<div class="check-row ${c.status}">
    <span class="check-pill ${c.status}">${CHECK_LABEL[c.status]}</span>
    <div><b>${esc(c.name)}</b> <span class="muted">${esc(c.detail)}</span>
      ${c.fix && c.status !== "ok" ? `<div class="small fix">Como corrigir: ${esc(c.fix)}</div>` : ""}</div></div>`).join("");
}

async function loadHealth() {
  let h;
  try { h = await api("/api/health"); } catch (e) { toast(e.message, true); return; }
  state.healthInfo = h;
  $("#health-sub").textContent = `Versão ${h.version} · dados em ${h.data_dir}`;
  const report = h.selfcheck;
  const box = $("#selfcheck-box");
  if (!report) box.innerHTML = '<p class="muted small">Ainda não conferido. Clique em "Conferir agora" ou faça uma coleta.</p>';
  else if (!report.findings.length) box.innerHTML = `<div class="ok-card">${report.packages} bibliotecas conferidas em ${esc(new Date(report.checked_at).toLocaleString("pt-BR"))}: nenhuma falha conhecida.</div>`;
  else box.innerHTML = report.findings.map((f) => `<div class="error-card"><div>
      <b>${esc(f.package)} ${esc(f.version)}</b> · <a href="#" data-url="${esc(f.url)}">${esc(f.id)}</a>${f.aliases.length ? ` <span class="muted small">(${esc(f.aliases.join(", "))})</span>` : ""}
      <div class="msg">${esc(f.summary || "Falha conhecida.")}</div>
      <div class="small">${f.fixed.length ? `Corrigida na versão <b>${esc(f.fixed.join(", "))}</b>.` : "Sem versão corrigida informada."}</div></div></div>`).join("") +
    `<p class="small">Para corrigir: baixe a versão mais nova do programa (abaixo) ou, rodando pelo Python, <code>pip install -U ${esc([...new Set(report.findings.map((f) => f.package))].join(" "))}</code>.</p>`;
  $$("#selfcheck-box a[data-url]").forEach((a) => a.onclick = (ev) => { ev.preventDefault(); openExternal(a.dataset.url); });

  const sched = h.schedule;
  $("#sched-time").value = sched.time;
  $("#sched-status").className = "small " + (sched.missed ? "status-bad" : sched.installed ? "status-ok" : "muted");
  $("#sched-status").textContent = sched.message + (sched.fix ? " " + sched.fix : "");
  $("#sched-remove").classList.toggle("hidden", !sched.installed);
  $("#resident-status").className = "small " + (h.resident ? "status-ok" : "muted");
  $("#resident-status").textContent = h.resident ? "Os alertas iniciam junto com o sistema." : "Os alertas não iniciam sozinhos.";
  $("#resident-on").classList.toggle("hidden", h.resident);
  $("#resident-off").classList.toggle("hidden", !h.resident);
  $("#db-status").textContent = `${h.db.items} notícias (${h.db.starred} favoritas, ${h.db.rated} avaliadas) · ` +
    `${(h.db.bytes / 1048576).toFixed(1)} MB` + (h.cleanup_at ? ` · última faxina ${new Date(h.cleanup_at).toLocaleString("pt-BR")}` : "");
  if (state.config) {
    const sc = state.config.schedule || {};
    $("#sched-weekdays").checked = sc.weekdays_only !== false;
    $("#sched-email").checked = sc.send_email !== false;
    $("#sched-alert").checked = sc.alert_missed !== false;
  } else {
    api("/api/config").then((d) => { state.config = d.config; loadHealth(); }).catch(() => {});
  }
  if (!state.checks) {
    api("/api/diagnostics", { method: "POST", body: { quick: true } }).then((d) => renderChecks(d.checks)).catch(() => {});
  }
}

$("#diag-run").addEventListener("click", async (ev) => {
  const btn = ev.currentTarget;
  btn.disabled = true;
  $("#diag-list").innerHTML = '<p class="muted small">Rodando diagnóstico completo (internet, relógio, e-mail)…</p>';
  try {
    const d = await api("/api/diagnostics", { method: "POST", body: {} });
    state.checks = d.checks;
    renderChecks(d.checks);
  } catch (e) { toast(e.message, true); }
  btn.disabled = false;
});

$("#selfcheck-run").addEventListener("click", async (ev) => {
  ev.currentTarget.disabled = true;
  try { await api("/api/selfcheck", { method: "POST" }); await loadHealth(); toast("Bibliotecas conferidas."); }
  catch (e) { toast(e.message, true); }
  ev.target.disabled = false;
});

async function scheduleAction(body) {
  try {
    const res = await api("/api/schedule", { method: "POST", body });
    toast(res.message);
    state.config = null;
    loadHealth();
  } catch (e) { toast(e.message, true); }
}
$("#sched-install").addEventListener("click", () => scheduleAction({
  time: $("#sched-time").value || "07:00", weekdays_only: $("#sched-weekdays").checked,
  send_email: $("#sched-email").checked, alert_missed: $("#sched-alert").checked,
}));
$("#sched-remove").addEventListener("click", () => scheduleAction({ action: "remove" }));
$("#resident-on").addEventListener("click", () => scheduleAction({ action: "resident_on" }));
$("#resident-off").addEventListener("click", () => scheduleAction({ action: "resident_off" }));

$("#cleanup-run").addEventListener("click", async () => {
  try {
    const r = await api("/api/maintenance", { method: "POST" });
    toast(`Faxina feita: ${r.items} notícias antigas apagadas, ${(r.freed_bytes / 1048576).toFixed(1)} MB liberados.`);
    loadHealth();
  } catch (e) { toast(e.message, true); }
});

$$("[data-folder]").forEach((b) => b.addEventListener("click", () =>
  api("/api/open-folder", { method: "POST", body: { folder: b.dataset.folder } }).catch((e) => toast(e.message, true))));

function updateLabel(u) {
  return u.hint || "Baixa, confere o SHA-256, fecha, troca os arquivos e abre de novo. Seus dados não mudam.";
}
async function applyUpdate(button, status) {
  const mark = (cls) => { if (status.id === "update-status") status.className = `small ${cls}`; };
  if (button) button.disabled = true;
  mark("");
  status.textContent = "Baixando e conferindo o hash…";
  try {
    const r = await api("/api/updates/apply", { method: "POST" });
    mark("status-ok");
    status.textContent = r.message;
    toast(r.message);
  } catch (e) {
    mark("status-bad");
    status.textContent = e.message;
    if (button) button.disabled = false;
  }
}

$("#update-check").addEventListener("click", async () => {
  const status = $("#update-status");
  status.textContent = "Consultando o GitHub…";
  try {
    const u = await api("/api/updates/check", { method: "POST" });
    if (!u.newer) {
      status.className = "small status-ok";
      status.textContent = `Você está na versão mais recente (${u.current}).`;
      return;
    }
    status.className = "small";
    status.innerHTML = `Versão <b>${esc(u.latest)}</b> disponível (você tem ${esc(u.current)}).` +
      (u.frozen ? (u.auto || u.mode === "flatpak" ? " " + esc(updateLabel(u)) : "") : " Como você roda pelo código-fonte, atualize com <code>git pull</code>.");
    $("#update-apply").classList.toggle("hidden", !u.auto);
    $("#update-download").classList.toggle("hidden", !(u.frozen && u.has_package));
  } catch (e) { status.className = "small status-bad"; status.textContent = e.message; }
});
$("#update-apply").addEventListener("click", (ev) => applyUpdate(ev.currentTarget, $("#update-status")));
$("#update-download").addEventListener("click", async (ev) => {
  const status = $("#update-status");
  ev.currentTarget.disabled = true;
  status.textContent = "Baixando e conferindo o hash…";
  try {
    const r = await api("/api/updates/download", { method: "POST" });
    status.className = "small status-ok";
    status.innerHTML = `Pacote da versão ${esc(r.version)} baixado e conferido (SHA-256 ${esc(r.sha256.slice(0, 16))}…).
      <code>${esc(r.how)}</code>`;
    $("#update-folder").classList.remove("hidden");
  } catch (e) { status.className = "small status-bad"; status.textContent = e.message; }
  ev.target.disabled = false;
});

// Aviso de versão nova ao abrir (silencioso se estiver sem internet).
async function checkUpdatesOnStart() {
  if (document.body.dataset.checkUpdates !== "1") return;
  let u;
  try { u = await api("/api/updates/check", { method: "POST" }); } catch (_) { return; }
  if (!u.newer || u.skipped || !u.frozen) return;
  const banner = $("#update-banner");
  $("#update-banner-title").textContent = `Versão ${u.latest} disponível (você tem ${u.current})`;
  $("#update-banner-text").textContent = u.auto ? updateLabel(u)
    : u.mode === "flatpak" ? updateLabel(u) + " Veja em Saúde → Atualização." : "Veja em Saúde → Atualização.";
  $("#update-now").classList.toggle("hidden", !u.auto);
  $("#update-skip").onclick = () => {
    banner.classList.add("hidden");
    api("/api/updates/skip", { method: "POST", body: { version: u.latest } }).catch(() => {});
  };
  banner.classList.remove("hidden");
}
$("#update-later").addEventListener("click", () => $("#update-banner").classList.add("hidden"));
$("#update-now").addEventListener("click", (ev) => applyUpdate(ev.currentTarget, $("#update-banner-text")));

$("#report-copy").addEventListener("click", async () => {
  const area = $("#report-text");
  let text;
  try { text = (await api("/api/report")).text; } catch (e) { toast(e.message, true); return; }
  area.value = text;
  area.classList.remove("hidden");
  try {
    await navigator.clipboard.writeText(text);
    toast("Relatório copiado.");
  } catch (_) {
    area.select();
    document.execCommand("copy");
    toast("Relatório selecionado. Se não copiou, use Ctrl+C.");
  }
});

// --- Versões, filtros, celular, login Microsoft e backup ------------------------

const VERSION_PRESETS = {
  "PHP": "php:php", "MariaDB": "mariadb:mariadb", "MySQL": "oracle:mysql", "Laravel": "laravel:framework",
  "Node.js": "nodejs:node.js", "nginx": "f5:nginx", "Apache HTTP Server": "apache:http_server",
  "OpenSSL": "openssl:openssl", "PostgreSQL": "postgresql:postgresql", "WordPress": "wordpress:wordpress",
  "Redis": "redis:redis", "Composer": "getcomposer:composer",
};
$("#version-preset").insertAdjacentHTML("beforeend",
  Object.keys(VERSION_PRESETS).map((k) => `<option>${esc(k)}</option>`).join("") + '<option value="__outro">Outro…</option>');

function renderVersions() {
  const list = state.config.interests.versions = state.config.interests.versions || [];
  $("#versions-table tbody").innerHTML = list.map((v, i) => `<tr data-i="${i}">
    <td><input class="v-name" value="${esc(v.name || "")}"></td>
    <td><input class="v-cpe mono" value="${esc(v.cpe || "")}" placeholder="fornecedor:produto"></td>
    <td><input class="v-version" value="${esc(v.version || "")}" placeholder="8.2.10"></td>
    <td><button class="btn small ghost danger">Remover</button></td></tr>`).join("") ||
    '<tr><td colspan="4" class="muted small">Nenhuma versão cadastrada.</td></tr>';
  $$("#versions-table tbody tr[data-i]").forEach((tr) => {
    const v = list[Number(tr.dataset.i)];
    $(".v-name", tr).oninput = (ev) => { v.name = ev.target.value.trim(); };
    $(".v-cpe", tr).oninput = (ev) => { v.cpe = ev.target.value.trim().toLowerCase(); };
    $(".v-version", tr).oninput = (ev) => { v.version = ev.target.value.trim(); };
    $("button", tr).onclick = () => { list.splice(Number(tr.dataset.i), 1); renderVersions(); };
  });
}
$("#version-add").addEventListener("click", () => {
  const choice = $("#version-preset").value;
  if (!choice) return;
  const list = state.config.interests.versions = state.config.interests.versions || [];
  list.push(choice === "__outro" ? { name: "", cpe: "", version: "" } : { name: choice, cpe: VERSION_PRESETS[choice], version: "" });
  renderVersions();
  $("#versions-table tbody tr:last-child .v-version")?.focus();
});

const ACTION_LABELS = { mute: "Silenciar", highlight: "Destacar" };
function renderRules() {
  const filters = state.config.filters = state.config.filters || {};
  const rules = filters.rules = filters.rules || [];
  const sourceName = (id) => (state.sources.find((s) => s.id === id) || {}).name || id;
  $("#rules-table tbody").innerHTML = rules.map((r, i) => `<tr>
    <td>${esc(r.term)}</td><td><span class="pill ${r.action}">${ACTION_LABELS[r.action] || r.action}</span></td>
    <td>${r.source ? esc(sourceName(r.source)) : '<span class="muted">Todas</span>'}</td>
    <td><button class="btn small ghost danger" data-i="${i}">Remover</button></td></tr>`).join("") ||
    '<tr><td colspan="4" class="muted small">Nenhuma regra.</td></tr>';
  $$("#rules-table button[data-i]").forEach((b) => b.onclick = () => { rules.splice(Number(b.dataset.i), 1); renderRules(); });
}
$("#rule-add").addEventListener("click", () => {
  const term = $("#rule-term").value.trim();
  if (!term) return;
  state.config.filters.rules.push({ term, action: $("#rule-action").value, source: $("#rule-source").value });
  $("#rule-term").value = "";
  renderRules();
});
$("#see-muted").addEventListener("click", (ev) => {
  ev.preventDefault();
  state.view = "silenciadas";
  state.selectedId = null;
  applyArea();
  showPage("reader");
  setUnreadOnly(false);
  renderEmptyReader();
});

const CHAT_HELP = {
  telegram: ["Token do robô", "123456:ABC…", "No Telegram, fale com @BotFather, envie /newbot e copie o token. Depois mande um \"oi\" para o seu robô e clique em Descobrir."],
  discord: ["Endereço do webhook", "https://discord.com/api/webhooks/…", "No Discord: Configurações do canal → Integrações → Webhooks → Novo webhook → Copiar URL."],
  teams: ["Endereço do webhook", "https://….logic.azure.com/…", "No Teams: no canal, ··· → Fluxos de trabalho → \"Postar em um canal quando uma solicitação de webhook for recebida\" e copie o endereço."],
};
async function renderChat() {
  const provider = $("#chat-provider").value;
  const [label, placeholder, help] = CHAT_HELP[provider];
  $("#chat-secret-label").firstChild.textContent = label + " ";
  $("#chat-secret").placeholder = placeholder;
  $("#chat-help").textContent = help;
  $("#chat-id-label").classList.toggle("hidden", provider !== "telegram");
  try {
    const st = await api("/api/chat/status");
    $("#chat-secret-status").textContent = st[provider] ? "Salvo no cofre." : "Nada salvo ainda.";
  } catch (_) { /* silencioso */ }
}
$("#chat-provider").addEventListener("change", renderChat);
$("#chat-secret-save").addEventListener("click", async () => {
  const value = $("#chat-secret").value.trim();
  if (!value) return toast("Cole o token ou o endereço.", true);
  try {
    await api("/api/chat/secret", { method: "POST", body: { provider: $("#chat-provider").value, value } });
    $("#chat-secret").value = "";
    toast("Salvo no cofre do sistema.");
    renderChat();
  } catch (e) { toast(e.message, true); }
});
$("#chat-discover").addEventListener("click", async () => {
  try {
    const r = await api("/api/chat/discover", { method: "POST" });
    $('[name="chat.telegram_chat_id"]').value = r.chat_id;
    toast(`Chat encontrado: ${r.name || r.chat_id}. Clique em Salvar configurações.`);
  } catch (e) { toast(e.message, true); }
});
$("#chat-test").addEventListener("click", async () => {
  const status = $("#chat-status");
  status.className = "small muted";
  status.textContent = "Enviando…";
  try {
    await saveConfig(true);
    await api("/api/chat/test", { method: "POST" });
    status.className = "small status-ok";
    status.textContent = "Mensagem de teste enviada.";
  } catch (e) { status.className = "small status-bad"; status.textContent = e.message; }
});

function renderAuth() {
  const oauthMode = $("#email-auth").value === "oauth_microsoft";
  $("#auth-password").classList.toggle("hidden", oauthMode);
  $("#auth-oauth").classList.toggle("hidden", !oauthMode);
  if (oauthMode) refreshOauth();
}
$("#email-auth").addEventListener("change", renderAuth);

let oauthTimer;
async function refreshOauth() {
  clearTimeout(oauthTimer);
  let st;
  try { st = await api("/api/email/oauth/status"); } catch (_) { return; }
  const box = $("#oauth-code");
  const status = $("#oauth-status");
  if (st.status === "pending") {
    box.classList.remove("hidden");
    box.innerHTML = `Abra <a href="#" data-url="${esc(st.url)}">${esc(st.url)}</a> e digite o código <b class="mono">${esc(st.user_code)}</b>`;
    $("a", box).onclick = (ev) => { ev.preventDefault(); openExternal(st.url); };
    status.className = "small muted";
    status.textContent = "Aguardando você autorizar…";
    oauthTimer = setTimeout(refreshOauth, 3000);
    return;
  }
  box.classList.add("hidden");
  if (st.status === "error") { status.className = "small status-bad"; status.textContent = st.message; return; }
  status.className = "small " + (st.connected ? "status-ok" : "muted");
  status.textContent = st.connected ? "Conta Microsoft conectada." : "Não conectada.";
}
$("#oauth-connect").addEventListener("click", async () => {
  try {
    await saveConfig(true);
    await api("/api/email/oauth/start", { method: "POST" });
    refreshOauth();
  } catch (e) { toast(e.message, true); }
});
$("#oauth-disconnect").addEventListener("click", async () => {
  await api("/api/email/oauth/disconnect", { method: "POST" });
  refreshOauth();
});

function downloadText(filename, text, type) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}
function readFile(input) {
  return new Promise((resolve, reject) => {
    const file = input.files[0];
    if (!file) return reject(new Error("Nenhum arquivo escolhido."));
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error("Não foi possível ler o arquivo."));
    reader.readAsText(file, "utf-8");
  });
}
$("#backup-export").addEventListener("click", async () => {
  try {
    const r = await api("/api/backup/export", { method: "POST" });
    downloadText(r.path.split(/[\\/]/).pop(), JSON.stringify(r.data, null, 1), "application/json");
    $("#backup-status").className = "small status-ok";
    $("#backup-status").textContent = `Backup salvo também em ${r.path}`;
  } catch (e) { toast(e.message, true); }
});
$("#backup-import").addEventListener("change", async (ev) => {
  try {
    const data = JSON.parse(await readFile(ev.target));
    const r = await api("/api/backup/import", { method: "POST", body: { backup: data } });
    toast(`Importado: ${r.sources} fontes novas, ${r.items} notícias avaliadas/favoritas${r.config ? ", configurações" : ""}.`);
    loadConfig();
  } catch (e) { toast(e.message, true); }
  ev.target.value = "";
});
$("#opml-export").addEventListener("click", async () => {
  try { downloadText("monitor-fontes.opml", (await api("/api/opml")).opml, "text/x-opml"); }
  catch (e) { toast(e.message, true); }
});
$("#opml-import").addEventListener("change", async (ev) => {
  try {
    const r = await api("/api/opml", { method: "POST", body: { opml: await readFile(ev.target) } });
    toast(`${r.added} fontes adicionadas${r.skipped ? `, ${r.skipped} já existiam` : ""}.`);
  } catch (e) { toast(e.message, true); }
  ev.target.value = "";
});

// --- Início -----------------------------------------------------------------

loadItems();
renderEmptyReader();
pollStatus();
fillSourceSelects();
setTimeout(checkUpdatesOnStart, 3000);
setInterval(() => { if (state.page === "reader") loadCounts(); }, 60000);

// Sinal de vida: no modo navegador o programa encerra sozinho quando a última aba fecha.
const heartbeat = () => api("/api/heartbeat", { method: "POST" }).catch(() => {});
heartbeat();
setInterval(heartbeat, 20000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) heartbeat(); });
window.addEventListener("pagehide", () => {
  fetch("/api/bye", { method: "POST", keepalive: true, headers: { "X-Token": TOKEN } }).catch(() => {});
});
