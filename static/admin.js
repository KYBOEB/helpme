/* admin.js — панель оператора. Без библиотек, без CDN. */

const API = {
  tickets: "/api/tickets",
  queue: "/api/tickets?queue=1",
  ticket: (id) => `/api/tickets/${id}`,
  take: (id) => `/api/tickets/${id}/take`,
  reply: (id) => `/api/tickets/${id}/reply`,
  closeByOperator: (id) => `/api/tickets/${id}/resolve`,
  stats: (period) => `/api/stats?period=${encodeURIComponent(period || "all")}`,
  kbSearch: (q) => `/api/kb/search?q=${encodeURIComponent(q)}`,
  kbGaps: "/api/kb/gaps",
  kbCreate: "/api/kb/articles",
  share: (id) => `/api/tickets/${id}/share`,
  exportJson: (id) => `/api/tickets/${id}/export.json`,
  exportCsv: "/api/tickets/export.csv",
};

const state = {
  tickets: [],
  filtered: [],
  activeTicketId: null,
  activeTab: "tickets",
  ticketTimer: null,      // автообновление открытого обращения
  queueSignature: null,   // состав очереди на прошлом опросе
};

/* ---------- Сеть ---------- */

/** Сессия истекла — молча пустеть нельзя, уводим на форму входа. */
function handleUnauthorized() {
  location.href = "/operator";
}

async function apiGet(url) {
  const r = await fetch(url, { headers: { Accept: "application/json" } });
  if (r.status === 401) return handleUnauthorized(), Promise.reject(new Error("401"));
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

async function apiPost(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  if (r.status === 401) return handleUnauthorized(), Promise.reject(new Error("401"));
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
  return data;
}

/* ---------- Мелочи ---------- */

/**
 * Повесить обработчик, не падая на отсутствующем элементе.
 *
 * Зачем: если браузер закешировал старую версию admin.html, а admin.js приехал
 * новый (или наоборот), обычный getElementById(...).addEventListener кидает
 * TypeError на этапе загрузки. Скрипт умирает целиком, и панель выглядит
 * «ничего не грузит, вкладки не нажимаются». Пропущенный обработчик — гораздо
 * меньшая беда, чем мёртвая страница, поэтому такой промах только логируем.
 */
function on(id, event, handler) {
  const node = document.getElementById(id);
  if (!node) {
    console.warn(`admin.js: элемент #${id} не найден, обработчик ${event} не повешен`);
    return null;
  }
  node.addEventListener(event, handler);
  return node;
}


function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else node.setAttribute(k, v);
  }
  for (const c of children) node.append(c);
  return node;
}

function fmtDate(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" });
}

function fmtTime(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
}

function pct(x) {
  return `${Math.round((x ?? 0) * 100)}%`;
}

/**
 * Статус обращения. Сервер отдаёт состояние автомата и флаги —
 * человеческую подпись собираем здесь.
 */
/**
 * Уверенность относится к ПОДОБРАННОЙ СТАТЬЕ базы знаний.
 * Статьи нет — показывать нечего: «90 %» рядом с пустотой вводило бы в заблуждение.
 */
function confidenceText(t) {
  if (t.out_of_scope) return "—";
  return t.article_id ? pct(t.confidence) : "—";
}

function statusInfo(t) {
  if (t.out_of_scope) return { text: "Закрыто: не по теме", cls: "badge--muted" };
  if (t.closed_by_user) return { text: "Пользователь вышел", cls: "badge--muted" };
  if (t.needs_specialist && !t.operator_taken) return { text: "Ждёт специалиста", cls: "badge--warn" };
  if (t.needs_specialist && t.operator_taken) return { text: "У специалиста", cls: "badge--info" };
  if (t.resolved_by_bot) return { text: "Решено ботом", cls: "badge--ok" };
  if (t.state === "RESOLVED") return { text: "Завершено", cls: "badge--ok" };
  return { text: "В работе", cls: "badge--info" };
}

function statusKey(t) {
  if (t.out_of_scope || t.closed_by_user) return "closed";
  if (t.needs_specialist) return "escalated";
  if (t.resolved_by_bot) return "resolved_by_bot";
  return "in_progress";
}

/* ---------- Вкладки ---------- */

function switchTab(name) {
  state.activeTab = name;
  document.querySelectorAll(".tab").forEach((t) => {
    t.classList.toggle("is-active", t.dataset.tab === name);
  });
  document.querySelectorAll(".tab-panel").forEach((p) => {
    p.classList.toggle("is-active", p.id === `tab-${name}`);
  });
  if (name === "tickets") loadTickets();
  if (name === "queue") loadQueue();
  if (name === "analytics") loadStats();
  if (name === "kb") loadGaps();
}

/* ---------- Вкладка «Обращения» ---------- */

async function loadTickets() {
  const tbody = document.getElementById("tickets-body");
  tbody.innerHTML = "";
  try {
    const data = await apiGet(API.tickets);
    state.tickets = Array.isArray(data) ? data : (data.items ?? []);
  } catch {
    tbody.append(el("tr", {}, [
      el("td", { colspan: "7", text: "Не удалось загрузить обращения" }),
    ]));
    return;
  }
  fillCategoryFilter();
  applyFilters();
}

function fillCategoryFilter() {
  const sel = document.getElementById("filter-category");
  const cats = [...new Set(state.tickets.map((t) => t.category).filter(Boolean))].sort();
  const current = sel.value;
  sel.innerHTML = '<option value="">Все</option>';
  for (const c of cats) sel.append(el("option", { value: c, text: c }));
  sel.value = current;
}

function applyFilters() {
  const cat = document.getElementById("filter-category").value;
  const status = document.getElementById("filter-status").value;
  const q = document.getElementById("filter-search").value.trim().toLowerCase();

  state.filtered = state.tickets.filter((t) => {
    if (cat && t.category !== cat) return false;
    if (status && statusKey(t) !== status) return false;
    if (q) {
      const hay = `${t.problem_summary ?? ""} ${t.ticket_id ?? ""}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
  renderTickets();
}

function renderTickets() {
  const tbody = document.getElementById("tickets-body");
  const empty = document.getElementById("tickets-empty");
  tbody.innerHTML = "";

  if (!state.filtered.length) {
    empty.classList.remove("hidden");
    return;
  }
  empty.classList.add("hidden");

  for (const t of state.filtered) {
    const s = statusInfo(t);
    const tr = el("tr", { "data-id": t.ticket_id });
    tr.append(
      el("td", { text: t.public_no ? `№${t.public_no}` : (t.ticket_id ?? "—"), title: t.ticket_id ?? "" }),
      el("td", { text: fmtDate(t.created_at) }),
      el("td", {}, [
        el("span", { text: t.category ?? "—" }),
        ...(t.assist_used
          ? [el("span", { class: "badge badge--warn", text: "нет в базе", title: "Решения в базе знаний не нашлось" })]
          : []),
      ]),
      el("td", { text: confidenceText(t),
                 title: t.article_id ? "Уверенность в подобранной статье базы знаний"
                                     : "Статья в базе знаний не подобрана" }),
      el("td", {}, [el("span", { class: `badge ${s.cls}`, text: s.text })]),
      el("td", { text: String(t.user_actions_count ?? "—") }),
      el("td", { text: t.rating == null ? "—" : (t.rating ? "👍" : "👎") }),
    );
    tr.addEventListener("click", () => openTicket(t.ticket_id));
    tbody.append(tr);
  }
}

/* ---------- Вкладка «Очередь» ---------- */

/* Очередь опрашивается сама: специалист не должен щёлкать по вкладкам,
   чтобы узнать, что заявка пришла. Список перерисовывается, только если
   его состав изменился — иначе кнопка «Взять в работу» убегала бы
   из-под курсора каждые десять секунд. */
const QUEUE_REFRESH_MS = 10000;

function queueSignature(items) {
  return items.map((t) => t.ticket_id).join(",");
}

async function refreshQueueBadge() {
  let items;
  try {
    items = await apiGet(API.queue);
  } catch {
    return;                       // сеть моргнула, попробуем на следующем тике
  }
  document.getElementById("queue-count").textContent = String(items.length);
  const sig = queueSignature(items);
  if (sig === state.queueSignature) return;
  state.queueSignature = sig;
  if (state.activeTab === "queue") renderQueue(items);
}

setInterval(refreshQueueBadge, QUEUE_REFRESH_MS);
document.addEventListener("DOMContentLoaded", refreshQueueBadge);

async function loadQueue() {
  const box = document.getElementById("queue-list");
  box.innerHTML = '<p class="muted">Загрузка…</p>';
  let items;
  try {
    items = await apiGet(API.queue);
  } catch {
    box.innerHTML = '<p class="muted">Не удалось загрузить очередь</p>';
    return;
  }
  state.queueSignature = queueSignature(items);
  renderQueue(items);
}

function renderQueue(items) {
  const box = document.getElementById("queue-list");
  document.getElementById("queue-count").textContent = String(items.length);
  box.innerHTML = "";

  if (!items.length) {
    box.append(el("p", { class: "empty", text: "Очередь пуста — все обращения разобраны" }));
    return;
  }

  for (const t of items) {
    const card = el("article", { class: "queue-item" });
    const head = el("div", { class: "queue-head" }, [
      el("span", { class: "queue-id", text: t.public_no ? `№${t.public_no}` : t.ticket_id }),
      el("span", { class: "badge badge--info", text: t.category ?? "не определено" }),
      ...(t.assist_used
        ? [el("span", { class: "badge badge--warn", text: "нет статьи в базе" })]
        : []),
      el("span", { class: "queue-time", text: fmtDate(t.created_at) }),
    ]);

    const btn = el("button", { class: "btn btn--primary", text: "Взять в работу" });
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      btn.disabled = true;
      try {
        await apiPost(API.take(t.ticket_id));
        state.queueSignature = null;      // состав очереди изменился нами
        // Взял в работу — сразу открываем диалог. Раньше карточка просто
        // исчезала из очереди, и специалисту приходилось искать её заново.
        openTicket(t.ticket_id);
      } catch {
        btn.disabled = false;
        const c = document.getElementById("queue-count"); if (c) c.title = "Не удалось взять обращение";
      }
    });

    card.append(
      head,
      el("p", { class: "queue-summary", text: t.problem_summary || "без описания" }),
      el("div", { class: "queue-actions" }, [btn]),
    );
    card.addEventListener("click", () => openTicket(t.ticket_id));
    box.append(card);
  }
}

/* ---------- Вкладка открытого обращения ----------
   Раньше карточка открывалась модальным окном: узкая колонка, в которой
   переписка, шаги и форма ответа не помещались одновременно. Теперь это
   полноценная вкладка — слева диалог, справа карточка и действия. */

const TICKET_REFRESH_MS = 8000;

function ticketTitle(card) {
  return card.public_no ? `№${card.public_no}` : (card.ticket_id || "");
}

async function openTicket(id) {
  state.activeTicketId = id;
  const btn = document.getElementById("tab-btn-ticket");
  btn.hidden = false;
  switchTab("ticket");

  document.getElementById("ticket-log").innerHTML =
    '<p class="muted">Загрузка…</p>';
  document.getElementById("ticket-reply").replaceChildren();
  document.getElementById("ticket-info").replaceChildren();
  document.getElementById("ticket-action-status").textContent = "";
  document.getElementById("toggle-share").checked = false;

  try {
    renderTicket(await apiGet(API.ticket(id)));
    startTicketRefresh(id);
  } catch {
    document.getElementById("ticket-log").innerHTML =
      '<p class="muted">Не удалось загрузить обращение</p>';
  }
}

function closeTicketView() {
  stopTicketRefresh();
  state.activeTicketId = null;
  document.getElementById("tab-btn-ticket").hidden = true;
  document.getElementById("tab-ticket-no").textContent = "";
  switchTab("tickets");
}

function renderTicket(card) {
  const s = statusInfo(card);
  document.getElementById("tab-ticket-no").textContent = ticketTitle(card);
  document.getElementById("ticket-chat-title").textContent =
    `Обращение ${ticketTitle(card)} · ${card.category || "без категории"}`;

  // ----- карточка справа -----
  const info = document.getElementById("ticket-info");
  info.replaceChildren();
  info.append(el("h3", { text: "Карточка" }));

  const dl = el("dl", { class: "card-dl" });
  const rows = [
    ["Статус", s.text],
    ["Категория", card.category],
    ["Проблема", card.problem_summary],
    ["Статья", card.article_id],
    ["Уверенность в статье", confidenceText(card)],
    ["Действий пользователя", card.user_actions_count],
    ["Создано", fmtDate(card.created_at)],
    ["Оценка", card.rating == null ? "—"
              : (card.rating ? "👍 помогло" : "👎 не помогло")],
  ];
  if (card.assist_used) rows.push(["Источник ответа", "совет ИИ-ассистента, статьи в базе нет"]);
  if (card.out_of_scope) rows.push(["Закрыто системой", "обращение вне тематики поддержки"]);
  if (card.closed_by_user) rows.push(["Закрыто", "пользователь вышел из диалога"]);
  for (const [label, value] of rows) {
    dl.append(el("dt", { text: label }), el("dd", { text: String(value ?? "—") }));
  }
  info.append(dl);

  const slots = Object.entries(card.slots ?? {});
  if (slots.length) {
    info.append(el("h3", { text: "Ответы пользователя" }));
    const sdl = el("dl", { class: "card-dl" });
    for (const [k, v] of slots) sdl.append(el("dt", { text: k }), el("dd", { text: String(v) }));
    info.append(sdl);
  }

  const steps = card.steps ?? [];
  if (steps.length) {
    info.append(el("h3", { text: "Выданные шаги" }));
    info.append(el("ol", { class: "drawer-steps" }, steps.map((x) => el("li", { text: x }))));
  }

  // ----- переписка слева -----
  const log = document.getElementById("ticket-log");
  log.replaceChildren();
  for (const m of card.messages ?? []) {
    const cls = m.role === "user" ? "msg msg-user"
              : m.role === "operator" ? "msg msg-operator"
              : "msg msg-bot";
    const row = el("div", { class: cls }, [
      el("span", { class: "msg-text", text: m.content ?? "" }),
    ]);
    if (m.created_at) {
      row.append(el("span", { class: "msg-time", text: fmtTime(m.created_at) }));
    }
    log.append(row);
  }
  log.scrollTop = log.scrollHeight;

  document.getElementById("ticket-reply").replaceChildren(renderReplyBox(card));

  // Завершать уже закрытое обращение незачем
  const closeBtn = document.getElementById("btn-close-ticket");
  const finished = card.state === "RESOLVED";
  closeBtn.disabled = finished;
  closeBtn.textContent = finished ? "Обращение завершено" : "Завершить обращение";
}

function stopTicketRefresh() {
  if (state.ticketTimer) clearInterval(state.ticketTimer);
  state.ticketTimer = null;
}

/* Пока вкладка открыта, подтягиваем ответы пользователя: специалист ведёт
   диалог здесь и не должен нажимать F5, чтобы увидеть реплику. */
function startTicketRefresh(id) {
  stopTicketRefresh();
  state.ticketTimer = setInterval(async () => {
    if (state.activeTicketId !== id || state.activeTab !== "ticket") return;
    // Не перерисовываем, пока специалист печатает: иначе набранный текст
    // пропал бы у него из-под рук.
    const area = document.querySelector("#ticket-reply textarea");
    if (area && (area.value.trim() || document.activeElement === area)) return;
    try {
      renderTicket(await apiGet(API.ticket(id)));
    } catch { /* сеть моргнула — попробуем на следующем тике */ }
  }, TICKET_REFRESH_MS);
}

/**
 * Ответ специалиста пользователю.
 *
 * Как только специалист написал, обращение считается взятым в работу, бот
 * в переписку больше не вмешивается, а страница пользователя подхватывает
 * реплику опросом — перезагружать её не нужно.
 */
function renderReplyBox(card) {
  const wrap = el("div", { class: "reply-box" });

  if (card.out_of_scope) {
    wrap.append(el("p", {
      class: "reply-status",
      text: "Обращение закрыто системой как нецелевое — отвечать по нему нельзя.",
    }));
    return wrap;
  }
  if (card.closed_by_user) {
    wrap.append(el("p", {
      class: "reply-status",
      text: "Пользователь вышел из диалога — ответ он уже не увидит.",
    }));
    return wrap;
  }

  const area = el("textarea", {
    rows: "3",
    maxlength: "2000",
    placeholder: "Ответ пользователю. Ctrl+Enter — отправить.",
  });
  const btn = el("button", { class: "btn btn--primary", text: "Отправить" });
  const status = el("p", { class: "reply-status" });
  status.setAttribute("role", "status");

  async function submit() {
    const text = area.value.trim();
    if (!text) {
      status.textContent = "Введите текст ответа.";
      return;
    }
    btn.disabled = true;
    area.disabled = true;
    status.textContent = "Отправляем…";
    try {
      await apiPost(API.reply(card.ticket_id), { text });
      area.value = "";
      status.textContent = "Ответ отправлен — он уже виден пользователю.";
      // Дописываем реплику сразу, не перерисовывая вкладку: так подтверждение
      // об отправке остаётся на экране. Остальное обновит автообновление.
      const log = document.getElementById("ticket-log");
      if (log) {
        log.append(el("div", { class: "msg msg-operator" }, [
          el("span", { class: "msg-text", text }),
        ]));
        log.scrollTop = log.scrollHeight;
      }
    } catch (e) {
      status.textContent = `Не удалось отправить: ${e.message}`;
    } finally {
      btn.disabled = false;
      area.disabled = false;
    }
  }

  area.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      submit();
    }
  });
  btn.addEventListener("click", submit);

  wrap.append(area, el("div", { class: "reply-row" }, [btn, status]));
  return wrap;
}

/* ---------- Действия справа ---------- */

on("btn-back-to-list", "click", () => {
  // Выходим к списку, НЕ закрывая обращение: к нему можно вернуться
  stopTicketRefresh();
  switchTab("tickets");
});

on("btn-close-ticket", "click", async () => {
  const id = state.activeTicketId;
  if (!id) return;
  const status = document.getElementById("ticket-action-status");
  const btn = document.getElementById("btn-close-ticket");
  btn.disabled = true;
  status.textContent = "Закрываем…";
  try {
    await apiPost(API.closeByOperator(id));
    status.textContent = "Обращение завершено.";
    renderTicket(await apiGet(API.ticket(id)));
    loadTickets();
  } catch (e) {
    btn.disabled = false;
    status.textContent = `Не удалось завершить: ${e.message}`;
  }
});

on("btn-download-json", "click", () => {
  if (!state.activeTicketId) return;
  window.location.href = API.exportJson(state.activeTicketId);
});

on("toggle-share", "change", async (e) => {
  if (!state.activeTicketId) {
    e.target.checked = false;
    return;
  }
  try {
    const res = await apiPost(API.share(state.activeTicketId), { enabled: e.target.checked });
    if (e.target.checked && res.url) {
      sessionStorage.setItem(`share:${state.activeTicketId}`, location.origin + res.url);
    } else {
      sessionStorage.removeItem(`share:${state.activeTicketId}`);
    }
  } catch {
    e.target.checked = !e.target.checked;
    document.getElementById("ticket-action-status").textContent =
      "Не удалось изменить доступ";
  }
});

on("btn-copy-link", "click", async () => {
  const status = document.getElementById("ticket-action-status");
  const url = sessionStorage.getItem(`share:${state.activeTicketId}`);
  if (!url) {
    status.textContent = "Сначала откройте доступ по ссылке";
    return;
  }
  try {
    await navigator.clipboard.writeText(url);
    status.textContent = "Ссылка скопирована";
  } catch {
    prompt("Скопируйте ссылку:", url);
  }
});

/* ---------- Вкладка «Аналитика» ---------- */

async function loadStats() {
  const sel = document.getElementById("period-select");
  const period = sel ? sel.value : "all";
  try {
    renderStats(await apiGet(API.stats(period)));
  } catch (e) {
    console.warn("метрики недоступны", e);
  }
}

function renderStats(s) {
  document.getElementById("metric-actions").textContent =
    s.avg_user_actions ? s.avg_user_actions.toFixed(2) : "—";

  const share = s.resolved_by_bot_share ?? 0;
  const donut = document.getElementById("donut-resolved");
  donut.style.setProperty("--p", String(Math.round(share * 100)));
  document.getElementById("donut-resolved-value").textContent = pct(share);
  document.getElementById("donut-resolved-caption").textContent =
    `${s.resolved_by_bot ?? 0} из ${s.finished ?? 0} завершённых обращений`;

  const bars = document.getElementById("category-bars");
  bars.innerHTML = "";
  const dist = s.by_category ?? {};
  const max = Math.max(1, ...Object.values(dist));
  for (const [cat, n] of Object.entries(dist).sort((a, b) => b[1] - a[1])) {
    bars.append(el("div", { class: "bar" }, [
      el("span", { class: "bar-label", text: cat, title: cat }),
      el("span", { class: "bar-track" }, [
        el("span", { class: "bar-fill", style: `width:${(n / max) * 100}%` }),
      ]),
      el("span", { class: "bar-value", text: String(n) }),
    ]));
  }

  // Полезность: доля ответов, отмеченных 👍, в процентах
  document.getElementById("metric-usefulness").textContent =
    s.usefulness != null ? pct(s.usefulness) : "—";
  document.getElementById("metric-rating-count").textContent = String(s.ratings_count ?? 0);
  document.getElementById("metric-first-step").textContent =
    s.avg_time_to_first_step_ms != null
      ? `${(s.avg_time_to_first_step_ms / 1000).toFixed(1)} с`
      : "—";
  document.getElementById("metric-accuracy").textContent =
    s.classification_accuracy != null ? pct(s.classification_accuracy) : "—";

  const oos = document.getElementById("metric-out-of-scope");
  if (oos) oos.textContent = String(s.out_of_scope ?? 0);

  const waiting = document.getElementById("metric-waiting");
  if (waiting) waiting.textContent = String(s.waiting_operator ?? 0);
  const badge = document.getElementById("queue-count");
  if (badge) badge.textContent = String(s.waiting_operator ?? 0);
}

/* ---------- Вкладка «База знаний» ---------- */

/** Обращения, для которых статьи не нашлось. Готовый список тем для новых карточек. */
async function loadGaps() {
  const box = document.getElementById("kb-gaps");
  if (!box) return;
  box.innerHTML = '<p class="muted">Загрузка…</p>';
  let gaps;
  try {
    gaps = await apiGet(API.kbGaps);
  } catch {
    box.innerHTML = '<p class="muted">Не удалось загрузить список</p>';
    return;
  }

  box.innerHTML = "";
  if (!gaps.length) {
    box.append(el("p", { class: "muted", text: "Пробелов нет — на все обращения нашлись статьи" }));
    return;
  }
  for (const g of gaps) {
    box.append(el("div", { class: "gap-row" }, [
      el("span", { class: "gap-query", text: g.query || "—" }),
      el("span", { class: "badge badge--info", text: g.category ?? "не определено" }),
      el("span", { class: "gap-time", text: fmtDate(g.created_at) }),
    ]));
  }
}

async function searchKB() {
  const q = document.getElementById("kb-search").value.trim();
  const box = document.getElementById("kb-results");
  if (!q) {
    box.innerHTML = "";
    return;
  }
  box.innerHTML = '<p class="muted">Поиск…</p>';
  try {
    const items = await apiGet(API.kbSearch(q));
    box.innerHTML = "";
    if (!items.length) {
      box.append(el("p", { class: "muted", text: "Ничего не найдено" }));
      return;
    }
    for (const a of items) {
      box.append(el("article", { class: "kb-card" }, [
        el("h4", { text: a.title ?? a.id ?? "Статья" }),
        el("p", { class: "muted", text: `${a.category ?? ""} · ${a.id ?? ""}` }),
        ...(a.has_solution
          ? [el("ol", {}, (a.steps ?? []).map((s) => el("li", { text: s })))]
          : [el("p", { class: "muted", text: "Готового решения нет — передаётся специалисту" })]),
      ]));
    }
  } catch {
    box.innerHTML = '<p class="muted">Ошибка поиска</p>';
  }
}

/* ---------- Инициализация ---------- */

document.querySelectorAll(".tab").forEach((t) => {
  t.addEventListener("click", () => switchTab(t.dataset.tab));
});
on("btn-refresh", "click", loadTickets);
on("filter-category", "change", applyFilters);
on("filter-status", "change", applyFilters);
on("filter-search", "input", applyFilters);
on("kb-btn", "click", searchKB);
on("kb-search", "keydown", (e) => {
  if (e.key === "Enter") searchKB();
});

// Период на вкладке «Аналитика»
on("period-select", "change", loadStats);

/* Форма добавления карточки живёт в модальном окне: вкладка «База знаний»
   раньше открывалась сразу большой формой, за которой не было видно
   ни поиска, ни списка пробелов. */
const kbModal = document.getElementById("kb-modal");

function openKbModal() {
  if (!kbModal) return;
  kbModal.hidden = false;
  document.getElementById("kb-title")?.focus();
}

function closeKbModal() {
  if (kbModal) kbModal.hidden = true;
}

on("kb-open-form", "click", openKbModal);
if (kbModal) {
  kbModal.querySelectorAll("[data-close-modal]").forEach((n) => {
    n.addEventListener("click", closeKbModal);
  });
}
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && kbModal && !kbModal.hidden) closeKbModal();
});
window.closeKbModal = closeKbModal;

const csvBtn = document.getElementById("btn-export-csv");
if (csvBtn) csvBtn.addEventListener("click", () => { window.location.href = API.exportCsv; });

const logoutBtn = document.getElementById("btn-logout");
if (logoutBtn) {
  logoutBtn.addEventListener("click", async () => {
    await fetch("/api/operator/logout", { method: "POST" });
    location.href = "/operator";
  });
}

// Счётчик очереди виден на любой вкладке
loadStats();
switchTab("tickets");

/* ==========================================================================
   Форма добавления статьи в базу знаний — вкладка «База знаний».
   Разметка формы лежит в admin.html, внутри #kb-form-slot.
   ========================================================================== */

// Форма вызывает их после успешного добавления
window.loadGaps = loadGaps;
window.searchKB = searchKB;

(function initKbForm() {
  const form = document.getElementById("kb-form");
  if (!form) return; // формы нет на странице — тихо выходим

  const symptomsList = document.getElementById("kb-symptoms");
  const stepsList = document.getElementById("kb-steps");
  const errorEl = document.getElementById("kb-error");
  const successEl = document.getElementById("kb-success");
  const submitBtn = document.getElementById("kb-submit");

  addRow(symptomsList, "symptoms", "Например: сломалась кофемашина");
  addRow(stepsList, "steps", "Например: проверьте, включён ли он в розетку");

  form.addEventListener("click", (e) => {
    const addBtn = e.target.closest("[data-add]");
    if (addBtn) {
      const target = addBtn.dataset.add === "symptoms" ? symptomsList : stepsList;
      addRow(target, addBtn.dataset.add);
      target.lastElementChild.querySelector("input").focus();
      return;
    }
    const removeBtn = e.target.closest("[data-remove]");
    if (removeBtn) {
      const list = removeBtn.closest(".kb-list");
      removeBtn.closest(".kb-row").remove();
      if (list.children.length === 0) {
        addRow(list, list.id === "kb-symptoms" ? "symptoms" : "steps");
      }
    }
  });

  form.addEventListener("submit", onSubmit);

  function addRow(list, kind, placeholder = "") {
    const row = document.createElement("div");
    row.className = "kb-row";
    const input = document.createElement("input");
    input.type = "text";
    input.dataset.kind = kind;
    input.placeholder = placeholder;
    const del = document.createElement("button");
    del.type = "button";
    del.className = "kb-remove";
    del.dataset.remove = "";
    del.setAttribute("aria-label", "Удалить");
    del.textContent = "✕";
    row.append(input, del);
    list.append(row);
  }

  function collect(list) {
    return [...list.querySelectorAll("input")].map((i) => i.value.trim()).filter(Boolean);
  }

  async function onSubmit(e) {
    e.preventDefault();
    hide(errorEl);
    hide(successEl);

    const category = document.getElementById("kb-category").value;
    const title = document.getElementById("kb-title").value.trim();
    const symptoms = collect(symptomsList);
    const steps = collect(stepsList);
    const escalateIf = document.getElementById("kb-escalate").value.trim();

    // Проверка на клиенте дублирует контракт, но не заменяет 422 с сервера
    if (!category) return showError("Выберите категорию.");
    if (!title) return showError("Укажите заголовок статьи.");
    if (symptoms.length === 0) return showError("Добавьте хотя бы один симптом.");
    if (steps.length === 0 && !escalateIf) {
      return showError("Нужны либо шаги решения, либо условие передачи специалисту.");
    }

    const payload = { category, title, symptoms };
    if (steps.length > 0) payload.steps = steps;
    if (escalateIf) payload.escalate_if = escalateIf;

    submitBtn.disabled = true;
    submitBtn.textContent = "Добавляю…";

    try {
      const res = await fetch("/api/kb/articles", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (res.status === 401) return handleUnauthorized();

      if (!res.ok) {
        let detail = `Ошибка ${res.status}`;
        try {
          const body = await res.json();
          if (body?.detail) detail = body.detail;
        } catch { /* тело не JSON — оставляем сообщение по умолчанию */ }
        return showError(detail);
      }

      const data = await res.json().catch(() => ({}));
      form.reset();
      symptomsList.replaceChildren();
      stepsList.replaceChildren();
      addRow(symptomsList, "symptoms");
      addRow(stepsList, "steps");
      showSuccess(
        `Статья ${data.id ?? ""} добавлена и уже доступна в поиске. ` +
        `Всего статей: ${data.articles_total ?? "—"}.`
      );

      loadGaps();
      searchKB();
      // Окно закрываем не сразу: пусть оператор увидит подтверждение
      setTimeout(() => { if (window.closeKbModal) window.closeKbModal(); }, 1600);
    } catch {
      showError("Не удалось связаться с сервером. Проверьте соединение и повторите.");
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = "Добавить статью";
    }
  }

  function showError(text) { errorEl.textContent = text; errorEl.hidden = false; }
  function showSuccess(text) { successEl.textContent = text; successEl.hidden = false; }
  function hide(el) { el.hidden = true; el.textContent = ""; }
})();
