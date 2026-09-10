/* admin.js — панель оператора. Без библиотек, без CDN. */

const API = {
  tickets: "/api/tickets",
  queue: "/api/tickets?queue=1",
  ticket: (id) => `/api/tickets/${id}`,
  take: (id) => `/api/tickets/${id}/take`,
  stats: "/api/stats",
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

function pct(x) {
  return `${Math.round((x ?? 0) * 100)}%`;
}

/**
 * Статус обращения. Сервер отдаёт состояние автомата и флаги —
 * человеческую подпись собираем здесь.
 */
function statusInfo(t) {
  if (t.needs_specialist && !t.operator_taken) return { text: "Ждёт специалиста", cls: "badge--warn" };
  if (t.needs_specialist && t.operator_taken) return { text: "У специалиста", cls: "badge--info" };
  if (t.resolved_by_bot) return { text: "Решено ботом", cls: "badge--ok" };
  if (t.state === "RESOLVED") return { text: "Завершено", cls: "badge--ok" };
  return { text: "В работе", cls: "badge--info" };
}

function statusKey(t) {
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
      el("td", { text: t.ticket_id ?? "—" }),
      el("td", { text: fmtDate(t.created_at) }),
      el("td", {}, [
        el("span", { text: t.category ?? "—" }),
        ...(t.assist_used
          ? [el("span", { class: "badge badge--warn", text: "нет в базе", title: "Решения в базе знаний не нашлось" })]
          : []),
      ]),
      el("td", { text: t.confidence != null ? pct(t.confidence) : "—" }),
      el("td", {}, [el("span", { class: `badge ${s.cls}`, text: s.text })]),
      el("td", { text: String(t.user_actions_count ?? "—") }),
      el("td", { text: t.rating != null ? String(t.rating) : "—" }),
    );
    tr.addEventListener("click", () => openDrawer(t.ticket_id));
    tbody.append(tr);
  }
}

/* ---------- Вкладка «Очередь» ---------- */

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

  document.getElementById("queue-count").textContent = String(items.length);
  box.innerHTML = "";

  if (!items.length) {
    box.append(el("p", { class: "empty", text: "Очередь пуста — все обращения разобраны" }));
    return;
  }

  for (const t of items) {
    const card = el("article", { class: "queue-item" });
    const head = el("div", { class: "queue-head" }, [
      el("span", { class: "queue-id", text: t.ticket_id }),
      el("span", { class: "badge badge--info", text: t.category ?? "не определено" }),
      ...(t.assist_used
        ? [el("span", { class: "badge badge--warn", text: "нет статьи в базе" })]
        : []),
      el("span", { class: "queue-time", text: fmtDate(t.created_at) }),
    ]);

    const btn = el("button", { class: "btn", text: "Взять в работу" });
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      btn.disabled = true;
      try {
        await apiPost(API.take(t.ticket_id));
        card.remove();
        const left = document.querySelectorAll("#queue-list .queue-item").length;
        document.getElementById("queue-count").textContent = String(left);
        if (!left) loadQueue();
      } catch {
        btn.disabled = false;
        alert("Не удалось взять обращение");
      }
    });

    card.append(
      head,
      el("p", { class: "queue-summary", text: t.problem_summary || "без описания" }),
      el("div", { class: "queue-actions" }, [btn]),
    );
    card.addEventListener("click", () => openDrawer(t.ticket_id));
    box.append(card);
  }
}

/* ---------- Боковая панель ---------- */

async function openDrawer(id) {
  state.activeTicketId = id;
  const drawer = document.getElementById("drawer");
  const body = document.getElementById("drawer-body");
  document.getElementById("drawer-title").textContent = `Обращение ${id}`;
  body.innerHTML = '<p class="muted">Загрузка…</p>';
  drawer.classList.remove("hidden");
  drawer.setAttribute("aria-hidden", "false");

  document.getElementById("toggle-share").checked = false;

  try {
    renderDrawer(await apiGet(API.ticket(id)));
  } catch {
    body.innerHTML = '<p class="muted">Не удалось загрузить карточку</p>';
  }
}

function renderDrawer(card) {
  const body = document.getElementById("drawer-body");
  body.innerHTML = "";
  const s = statusInfo(card);

  body.append(el("h3", { text: "Карточка" }));
  const dl = el("dl", { class: "card-dl" });
  const rows = [
    ["Категория", card.category],
    ["Статья", card.article_id],
    ["Уверенность", card.confidence != null ? pct(card.confidence) : "—"],
    ["Статус", s.text],
    ["Действий пользователя", card.user_actions_count],
    ["Оценка", card.rating],
  ];
  if (card.assist_used) rows.push(["Источник ответа", "общая рекомендация, статьи в базе нет"]);
  for (const [label, value] of rows) {
    dl.append(el("dt", { text: label }), el("dd", { text: String(value ?? "—") }));
  }
  body.append(dl);

  const slots = Object.entries(card.slots ?? {});
  if (slots.length) {
    body.append(el("h3", { text: "Ответы пользователя" }));
    const sdl = el("dl", { class: "card-dl" });
    for (const [k, v] of slots) sdl.append(el("dt", { text: k }), el("dd", { text: String(v) }));
    body.append(sdl);
  }

  const steps = card.steps ?? [];
  if (steps.length) {
    body.append(el("h3", { text: "Выданные шаги" }));
    body.append(el("ol", { class: "drawer-steps" }, steps.map((x) => el("li", { text: x }))));
  }

  body.append(el("h3", { text: "Переписка" }));
  const log = el("div", { class: "dialog-log" });
  for (const m of card.messages ?? []) {
    // сервер отдаёт content, не text
    log.append(el("div", {
      class: m.role === "user" ? "msg msg-user" : "msg msg-bot",
      text: m.content ?? "",
    }));
  }
  body.append(log);
}

/* ---------- Кнопки боковой панели ---------- */

document.getElementById("btn-download-json").addEventListener("click", () => {
  if (!state.activeTicketId) return;
  window.location.href = API.exportJson(state.activeTicketId);
});

document.getElementById("toggle-share").addEventListener("change", async (e) => {
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
    alert("Не удалось изменить доступ");
  }
});

document.getElementById("btn-copy-link").addEventListener("click", async () => {
  const url = sessionStorage.getItem(`share:${state.activeTicketId}`);
  if (!url) return alert("Сначала откройте доступ по ссылке");
  try {
    await navigator.clipboard.writeText(url);
    alert("Ссылка скопирована");
  } catch {
    prompt("Скопируйте ссылку:", url);
  }
});

document.getElementById("drawer-close").addEventListener("click", () => {
  const drawer = document.getElementById("drawer");
  drawer.classList.add("hidden");
  drawer.setAttribute("aria-hidden", "true");
  state.activeTicketId = null;
});

/* ---------- Вкладка «Аналитика» ---------- */

async function loadStats() {
  try {
    renderStats(await apiGet(API.stats));
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

  document.getElementById("metric-rating").textContent =
    s.avg_rating != null ? s.avg_rating.toFixed(2) : "—";
  document.getElementById("metric-rating-count").textContent = String(s.ratings_count ?? 0);
  document.getElementById("metric-first-step").textContent =
    s.avg_time_to_first_step_ms != null
      ? `${(s.avg_time_to_first_step_ms / 1000).toFixed(1)} с`
      : "—";
  document.getElementById("metric-accuracy").textContent =
    s.classification_accuracy != null ? pct(s.classification_accuracy) : "—";

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
document.getElementById("btn-refresh").addEventListener("click", loadTickets);
document.getElementById("filter-category").addEventListener("change", applyFilters);
document.getElementById("filter-status").addEventListener("change", applyFilters);
document.getElementById("filter-search").addEventListener("input", applyFilters);
document.getElementById("kb-btn").addEventListener("click", searchKB);
document.getElementById("kb-search").addEventListener("keydown", (e) => {
  if (e.key === "Enter") searchKB();
});

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
