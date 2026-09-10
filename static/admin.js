/* admin.js — панель оператора. Без библиотек, без CDN. */

const API = {
  tickets: "/api/tickets",
  ticket: (id) => `/api/tickets/${id}`,
  stats: "/api/stats",
  kbSearch: (q) => `/api/kb/search?q=${encodeURIComponent(q)}`,
  share: (id) => `/api/tickets/${id}/share`,
};

const state = {
  tickets: [],
  filtered: [],
  activeTicketId: null,
  activeTab: "tickets",
};

/* ---------- Утилиты ---------- */

async function apiGet(url) {
  const r = await fetch(url, { headers: { Accept: "application/json" } });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

async function apiPost(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
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
  const d = new Date(iso);
  return d.toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" });
}

function pct(x) {
  return `${Math.round((x ?? 0) * 100)}%`;
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
  if (name === "analytics") loadStats();
}

/* ---------- Вкладка «Обращения» ---------- */

async function loadTickets() {
  const tbody = document.getElementById("tickets-body");
  tbody.innerHTML = "";
  try {
    const data = await apiGet(API.tickets);
    state.tickets = Array.isArray(data) ? data : (data.items ?? []);
  } catch (e) {
    tbody.append(
      el("tr", {}, [
        el("td", { colspan: "7", text: "Не удалось загрузить обращения" }),
      ]),
    );
    return;
  }
  fillCategoryFilter();
  applyFilters();
}

function fillCategoryFilter() {
  const sel = document.getElementById("filter-category");
  const cats = [
    ...new Set(state.tickets.map((t) => t.category).filter(Boolean)),
  ].sort();
  sel.innerHTML = '<option value="">Все</option>';
  for (const c of cats) sel.append(el("option", { value: c, text: c }));
}

function applyFilters() {
  const cat = document.getElementById("filter-category").value;
  const status = document.getElementById("filter-status").value;
  const q = document.getElementById("filter-search").value.trim().toLowerCase();

  state.filtered = state.tickets.filter((t) => {
    if (cat && t.category !== cat) return false;
    if (status && t.status !== status) return false;
    if (q) {
      const hay =
        `${t.problem_summary ?? ""} ${t.ticket_id ?? ""}`.toLowerCase();
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
    const tr = el("tr", { "data-id": t.ticket_id });
    tr.append(
      el("td", { text: t.ticket_id ?? "—" }),
      el("td", { text: fmtDate(t.created_at) }),
      el("td", { text: t.category ?? "—" }),
      el("td", { text: t.confidence != null ? pct(t.confidence) : "—" }),
      el("td", { text: statusLabel(t.status) }),
      el("td", { text: String(t.user_actions_count ?? "—") }),
      el("td", { text: t.rating != null ? String(t.rating) : "—" }),
    );
    tr.addEventListener("click", () => openDrawer(t.ticket_id));
    tbody.append(tr);
  }
}

function statusLabel(s) {
  return (
    {
      resolved_by_bot: "Решено ботом",
      escalated: "Передано специалисту",
      in_progress: "В работе",
    }[s] ??
    s ??
    "—"
  );
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

  try {
    const card = await apiGet(API.ticket(id));
    renderDrawer(card);
  } catch (e) {
    body.innerHTML = '<p class="muted">Не удалось загрузить карточку</p>';
  }
}

function renderDrawer(card) {
  const body = document.getElementById("drawer-body");
  body.innerHTML = "";

  body.append(el("h3", { text: "Карточка" }));
  const dl = el("dl", { class: "card-dl" });
  for (const [label, value] of [
    ["Категория", card.category],
    ["Статья", card.article_id],
    ["Уверенность", card.confidence != null ? pct(card.confidence) : "—"],
    ["Статус", statusLabel(card.status)],
    ["Действий пользователя", card.user_actions_count],
    ["Оценка", card.rating],
  ]) {
    dl.append(
      el("dt", { text: label }),
      el("dd", { text: String(value ?? "—") }),
    );
  }
  body.append(dl);

  body.append(el("h3", { text: "Переписка" }));
  const log = el("div", { class: "dialog-log" });
  for (const m of card.messages ?? []) {
    const cls = m.role === "user" ? "msg msg-user" : "msg msg-bot";
    log.append(el("div", { class: cls, text: m.text ?? "" }));
  }
  body.append(log);
}

/* ---------- Кнопки в панели ---------- */

document.getElementById("btn-download-json").addEventListener("click", () => {
  if (!state.activeTicketId) return;
  window.location.href = `${API.ticket(state.activeTicketId)}/export.json`;
});

document
  .getElementById("toggle-share")
  .addEventListener("change", async (e) => {
    if (!state.activeTicketId) return;
    try {
      const res = await apiPost(API.share(state.activeTicketId), {
        enabled: e.target.checked,
      });
      if (e.target.checked && res.url) {
        sessionStorage.setItem(`share:${state.activeTicketId}`, res.url);
      } else {
        sessionStorage.removeItem(`share:${state.activeTicketId}`);
      }
    } catch (err) {
      alert("Не удалось изменить доступ");
      e.target.checked = !e.target.checked;
    }
  });

document.getElementById("btn-copy-link").addEventListener("click", async () => {
  const url = sessionStorage.getItem(`share:${state.activeTicketId}`);
  if (!url) return alert("Ссылка не активна");
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
    const s = await apiGet(API.stats);
    renderStats(s);
  } catch (e) {
    console.warn("stats недоступны", e);
  }
}

function renderStats(s) {
  // Главная цифра
  document.getElementById("metric-actions").textContent =
    s.avg_user_actions != null ? s.avg_user_actions.toFixed(2) : "—";

  // Кольцевая диаграмма: доля решённых ботом
  const resolvedShare = s.resolved_by_bot_share ?? 0;
  const donut = document.getElementById("donut-resolved");
  donut.style.setProperty("--p", String(Math.round(resolvedShare * 100)));
  document.getElementById("donut-resolved-value").textContent =
    pct(resolvedShare);
  document.getElementById("donut-resolved-caption").textContent =
    `${s.resolved_by_bot_count ?? 0} из ${s.total_count ?? 0} обращений`;

  // Полосы по категориям
  const bars = document.getElementById("category-bars");
  bars.innerHTML = "";
  const dist = s.by_category ?? {};
  const max = Math.max(1, ...Object.values(dist));
  for (const [cat, n] of Object.entries(dist).sort((a, b) => b[1] - a[1])) {
    const row = el("div", { class: "bar-row" });
    row.append(
      el("span", { class: "bar-label", text: cat }),
      el("span", { class: "bar-track" }, [
        el("span", { class: "bar-fill", style: `width:${(n / max) * 100}%` }),
      ]),
      el("span", { class: "bar-value", text: String(n) }),
    );
    bars.append(row);
  }

  document.getElementById("metric-rating").textContent =
    s.avg_rating != null ? s.avg_rating.toFixed(2) : "—";
  document.getElementById("metric-rating-count").textContent =
    s.rating_count ?? "—";
  document.getElementById("metric-first-step").textContent =
    s.avg_time_to_first_step_ms != null
      ? `${(s.avg_time_to_first_step_ms / 1000).toFixed(1)} с`
      : "—";
  document.getElementById("metric-accuracy").textContent =
    s.classification_accuracy != null ? pct(s.classification_accuracy) : "—";
}

/* ---------- Вкладка «База знаний» ---------- */

async function searchKB() {
  const q = document.getElementById("kb-search").value.trim();
  const box = document.getElementById("kb-results");
  if (!q) {
    box.innerHTML = "";
    return;
  }
  box.innerHTML = '<p class="muted">Поиск…</p>';
  try {
    const data = await apiGet(API.kbSearch(q));
    const items = Array.isArray(data) ? data : (data.items ?? []);
    box.innerHTML = "";
    if (!items.length) {
      box.append(el("p", { class: "muted", text: "Ничего не найдено" }));
      return;
    }
    for (const a of items) {
      const card = el("article", { class: "kb-card" });
      card.append(
        el("h3", { text: a.title ?? a.article_id ?? "Статья" }),
        el("p", { class: "muted", text: a.category ?? "" }),
        el(
          "ol",
          {},
          (a.steps ?? []).map((s) => el("li", { text: s })),
        ),
      );
      box.append(card);
    }
  } catch (e) {
    box.innerHTML = '<p class="muted">Ошибка поиска</p>';
  }
}

/* ---------- Инициализация ---------- */

document.querySelectorAll(".tab").forEach((t) => {
  t.addEventListener("click", () => switchTab(t.dataset.tab));
});
document.getElementById("btn-refresh").addEventListener("click", loadTickets);
document
  .getElementById("filter-category")
  .addEventListener("change", applyFilters);
document
  .getElementById("filter-status")
  .addEventListener("change", applyFilters);
document
  .getElementById("filter-search")
  .addEventListener("input", applyFilters);
document.getElementById("kb-btn").addEventListener("click", searchKB);
document.getElementById("kb-search").addEventListener("keydown", (e) => {
  if (e.key === "Enter") searchKB();
});

// старт
switchTab("tickets");
