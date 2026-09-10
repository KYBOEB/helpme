"use strict";

/* ==========================================================================
   «Помоги мне» — страница чата
   Этап 1: моки + каркас + отрисовка question и steps (choice — заодно,
   он рисуется так же, как question). summary/escalation — временная
   заглушка, полноценная карточка будет на этапе 2.
   ========================================================================== */

// ---------- Настройки ----------

const MOCK = true;  // переключается на false, когда бэкенд готов

const API_CHAT = "/api/chat";
const MAX_LEN = 2000;             // лимит длины сообщения
const COUNTER_FROM = 1800;        // счётчик появляется после этого числа символов
const REQUEST_TIMEOUT_MS = 20000; // дольше ждать нет смысла — показываем ошибку

// Служебные ответы кнопок пошагового гида.
// ВНИМАНИЕ: значение для «Получилось» на промежуточном шаге в ТЗ не задано —
// согласовать с бэкендом и поправить здесь.
const QR = {
  RESOLVED: "решено",
  STEP_OK: "получилось",
  NOT_HELPED: "не помогло",
};

// Человеческие тексты ошибок вместо «Error 429»
const ERROR_TEXT = {
  network: "Не удалось связаться с помощником. Проверьте подключение и повторите.",
  timeout: "Помощник не ответил вовремя. Повторите запрос.",
  404: "Обращение не найдено. Начните новое — это займёт пару секунд.",
  409: "Это обращение уже закрыто. Начните новое.",
  422: "Сообщение не удалось разобрать. Переформулируйте, пожалуйста.",
  429: "Слишком много сообщений подряд, подождите пару секунд.",
  default: "Что-то пошло не так. Повторите запрос.",
};

// Иконка помощника (инлайновый SVG, без внешних файлов)
const ICON_BOT = `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/><path d="M9.6 9.6a2.4 2.4 0 0 1 4.7.6c0 1.6-2.3 2.1-2.3 3.3"/><path d="M12 16.4h.01"/></svg>`;

// ---------- Состояние ----------

const state = {
  ticketId: null,
  token: null,
  busy: false,
  // Запасной счётчик шагов — если бэкенд не присылает reply.step_index
  stepTrack: { key: null, index: -1 },
};

// ---------- DOM ----------

let chatEl, feedEl, emptyEl, inputEl, sendBtn, counterEl;
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

document.addEventListener("DOMContentLoaded", init);

function init() {
  chatEl = document.getElementById("chat");
  feedEl = document.getElementById("feed");
  emptyEl = document.getElementById("empty");
  inputEl = document.getElementById("input");
  sendBtn = document.getElementById("send-btn");
  counterEl = document.getElementById("counter");

  document.getElementById("mock-badge").hidden = !MOCK;

  inputEl.maxLength = MAX_LEN;
  inputEl.addEventListener("input", onInput);
  inputEl.addEventListener("keydown", onKeydown);
  sendBtn.addEventListener("click", submitInput);

  // Примеры обращений: клик сразу отправляет
  document.querySelectorAll("[data-example]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const text = btn.dataset.example;
      send({ message: text }, { echo: text });
    });
  });

  // Этап 4: проверить localStorage и показать полосу незавершённого обращения

  updateComposer();
  // На телефоне не открываем клавиатуру сами
  if (window.matchMedia("(hover: hover)").matches) inputEl.focus();
}

/* ==========================================================================
   ОТПРАВКА
   ========================================================================== */

/**
 * Единая точка отправки в /api/chat.
 * @param {{message?: string, quickReply?: string}} input
 * @param {{echo?: string, requestId?: string}} opts
 *   echo — текст, который показать в ленте как сообщение пользователя;
 *   requestId — передаётся при повторе, чтобы бэкенд мог отбросить дубль.
 */
async function send({ message = null, quickReply = null }, { echo = null, requestId = null } = {}) {
  if (state.busy) return;                         // второе нажатие игнорируем
  const text = typeof message === "string" ? message.trim() : "";
  if (!quickReply && !text) return;               // пустое не отправляем

  emptyEl.hidden = true;
  retireActiveControls();                         // старые кнопки больше не актуальны
  if (echo) addUserMessage(echo);

  const payload = {
    ticket_id: state.ticketId,
    token: state.token,
    request_id: requestId || uuid(),
    message: quickReply ? null : text,
    quick_reply: quickReply || null,
  };

  setBusy(true);
  showTyping();

  let data = null;
  let error = null;
  try {
    data = await apiPost(API_CHAT, payload);
  } catch (e) {
    error = e;
  }

  hideTyping();
  setBusy(false);

  if (error) {
    renderRequestError(error, payload);
    return;
  }

  if (data.ticket_id) state.ticketId = data.ticket_id;
  if (data.token) state.token = data.token;
  // Этап 4: сохранить ticket_id и token в localStorage (в try/catch)

  try {
    render(data, payload);
  } catch (e) {
    console.error("Не удалось отрисовать ответ", e, data);
    renderRequestError(new ApiError(-1, "render"), payload);
  }
}

// Повтор запроса. keepId = true — тот же request_id (запрос мог дойти до сервера).
function resend(payload, keepId) {
  send(
    { message: payload.message, quickReply: payload.quick_reply },
    { requestId: keepId ? payload.request_id : null }
  );
}

class ApiError extends Error {
  constructor(status, code = null) {
    super(code || `HTTP ${status}`);
    this.name = "ApiError";
    this.status = status; // 0 — сеть, -1 — сломанный ответ, иначе HTTP-код
    this.code = code;
  }
}

async function apiPost(path, body) {
  if (MOCK) return mockResponse(path, body);

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  let res;
  try {
    res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
  } catch (e) {
    throw new ApiError(0, e.name === "AbortError" ? "timeout" : "network");
  } finally {
    clearTimeout(timer);
  }

  if (!res.ok) throw new ApiError(res.status);
  try {
    return await res.json();
  } catch {
    throw new ApiError(-1, "bad_json");
  }
}

/* ==========================================================================
   ОТРИСОВКА ОТВЕТОВ
   ========================================================================== */

function render(data, payload) {
  const reply = data.reply || { type: "error" };
  switch (reply.type) {
    case "question":   return renderQuestion(data);
    case "choice":     return renderChoice(data);
    case "steps":      return renderSteps(data);
    case "summary":
    case "escalation": return renderCard(data);
    case "error":      return renderErrorReply(data, payload);
    default:
      console.warn("Неизвестный reply.type:", reply.type);
      return renderQuestion(data); // покажем хотя бы текст
  }
}

// question — вопрос + кнопки быстрых ответов
function renderQuestion(data) {
  const { reply } = data;
  const msg = addBotMessage(reply.text);
  // Этап 2: renderBadge(msg.body, data) — категория, уверенность, источник (4.4)
  renderQuickReplies(msg.body, reply.quick_replies);
  scrollToMessage(msg.row);
}

// choice — «уточните, о чём речь» + варианты категорий
function renderChoice(data) {
  const { reply } = data;
  const msg = addBotMessage(reply.text || "Уточните, пожалуйста, о чём речь:");
  renderQuickReplies(msg.body, reply.quick_replies);
  scrollToMessage(msg.row);
}

function renderQuickReplies(container, options) {
  if (!Array.isArray(options) || options.length === 0) return;

  const group = el("div", "quick-replies");
  group.setAttribute("role", "group");
  group.setAttribute("aria-label", "Варианты ответа");
  group.dataset.active = "quick";

  options.forEach((label) => {
    const btn = el("button", "chip", label);
    btn.type = "button";
    btn.addEventListener("click", () => {
      if (state.busy || !group.isConnected) return; // двойное нажатие
      group.remove();
      send({ quickReply: label }, { echo: label });
    });
    group.append(btn);
  });

  container.append(group);
  focusIfLost(group.firstElementChild);
}

// steps — пошаговый гид: показываем ровно один шаг
function renderSteps(data) {
  const { reply } = data;
  const steps = Array.isArray(reply.steps) ? reply.steps : [];
  if (steps.length === 0) return renderQuestion(data);

  const index = resolveStepIndex(data, steps);
  const total = steps.length;
  const text = stepText(steps[index]);
  const label = `Шаг ${index + 1} из ${total}: ${text}`;

  const msg = addBotMessage(reply.text, { wide: true });

  const card = el("div", "step-card");
  card.dataset.active = "step";
  card.dataset.label = label;

  const head = el("div", "step-head");
  head.append(el("span", "step-counter", `Шаг ${index + 1} из ${total}`));

  const progress = el("div", "progress");
  progress.setAttribute("role", "progressbar");
  progress.setAttribute("aria-valuemin", "0");
  progress.setAttribute("aria-valuemax", String(total));
  progress.setAttribute("aria-valuenow", String(index + 1));
  progress.setAttribute("aria-label", "Прогресс по шагам");
  const fill = el("div", "progress-fill");
  // Анимируем от предыдущего шага к текущему
  fill.style.width = `${(index / total) * 100}%`;
  progress.append(fill);
  requestAnimationFrame(() => requestAnimationFrame(() => {
    fill.style.width = `${((index + 1) / total) * 100}%`;
  }));

  const body = el("p", "step-text", text);

  const actions = el("div", "step-actions");
  const okBtn = el("button", "btn btn--primary", "Получилось");
  const failBtn = el("button", "btn btn--ghost", "Не получилось");
  okBtn.type = failBtn.type = "button";
  okBtn.addEventListener("click", () => answerStep(card, true, index, total));
  failBtn.addEventListener("click", () => answerStep(card, false, index, total));
  actions.append(okBtn, failBtn);

  card.append(head, progress, body, actions);
  msg.body.append(card);

  scrollToMessage(msg.row);
  focusIfLost(okBtn);
}

function answerStep(card, ok, index, total) {
  if (state.busy || card.dataset.active !== "step") return; // двойное нажатие
  collapseStep(card, ok ? "ok" : "failed");

  const isLast = index >= total - 1;
  const quickReply = ok ? (isLast ? QR.RESOLVED : QR.STEP_OK) : QR.NOT_HELPED;
  send({ quickReply });
}

// Сворачиваем шаг: остаётся в ленте приглушённым, с отметкой результата
function collapseStep(card, result) {
  const label = card.dataset.label || "Шаг";
  delete card.dataset.active;
  card.className = `step-card is-collapsed is-${result}`;
  card.replaceChildren();

  const marks = { ok: "✓", failed: "✕", skipped: "–" };
  const srText = { ok: "Выполнено. ", failed: "Не получилось. ", skipped: "Пропущено. " };

  const mark = el("span", "step-mark", marks[result]);
  mark.setAttribute("aria-hidden", "true");
  const textEl = el("span", "step-collapsed-text");
  textEl.append(el("span", "visually-hidden", srText[result]), label);
  textEl.title = label;

  card.append(mark, textEl);
}

// Какой шаг показывать. Предпочитаем reply.step_index от бэкенда.
function resolveStepIndex(data, steps) {
  let index;
  if (Number.isInteger(data.reply.step_index)) {
    index = data.reply.step_index;
  } else {
    // Запасной вариант: тот же набор шагов пришёл повторно — значит, следующий
    const key = (data.article?.id || "") + "|" + steps.map(stepText).join("|");
    index = state.stepTrack.key === key ? state.stepTrack.index + 1 : 0;
    state.stepTrack.key = key;
  }
  index = Math.min(Math.max(index, 0), steps.length - 1);
  state.stepTrack.index = index;
  return index;
}

// Шаг может прийти строкой или объектом — формат в контракте не зафиксирован
function stepText(step) {
  if (typeof step === "string") return step;
  return step?.text || step?.title || "";
}

// summary / escalation — ВРЕМЕННАЯ заглушка. Этап 2: карточка из 4 блоков + оценка (4.6)
function renderCard(data) {
  const { reply } = data;
  const card = data.ticket_card || {};
  const msg = addBotMessage(reply.text);

  const draft = el("div", "card-draft");
  draft.append(
    el("p", null, `Обращение №${card.ticket_id || data.ticket_id || "—"}, ${card.category || data.category || "без категории"}`),
    el("p", null, `Проблема: ${card.problem_summary || "—"}`),
    el("p", null, card.resolved_by_bot ? "Результат: ✓ решено без специалиста" : "Результат: передано специалисту")
  );
  msg.body.append(draft);

  const actions = el("div", "msg-actions");
  const newBtn = el("button", "btn btn--ghost", "Новое обращение");
  newBtn.type = "button";
  newBtn.addEventListener("click", resetConversation);
  actions.append(newBtn);
  msg.body.append(actions);

  scrollToMessage(msg.row);
}

// reply.type === "error" — бэкенд ответил, но обработать не смог
function renderErrorReply(data, payload) {
  showError(data.reply?.text || ERROR_TEXT.default, {
    label: "Попробовать снова",
    onClick: () => resend(payload, false),
  });
}

// Ошибка транспорта или HTTP-код
function renderRequestError(err, payload) {
  const status = err instanceof ApiError ? err.status : 0;
  const code = err instanceof ApiError ? err.code : "network";
  const text = ERROR_TEXT[status] || ERROR_TEXT[code] || ERROR_TEXT.default;

  if (status === 404 || status === 409) {
    showError(text, { label: "Новое обращение", onClick: resetConversation });
  } else if (status === 422) {
    showError(text, null); // повтор того же текста не поможет
  } else {
    showError(text, { label: "Повторить", onClick: () => resend(payload, true) });
  }
}

function showError(text, action) {
  const msg = addBotMessage(text, { variant: "error" });
  if (action) {
    const wrap = el("div", "msg-actions");
    wrap.dataset.active = "retry";
    const btn = el("button", "btn btn--ghost", action.label);
    btn.type = "button";
    btn.addEventListener("click", () => {
      if (state.busy) return;
      wrap.remove();
      action.onClick();
    });
    wrap.append(btn);
    msg.body.append(wrap);
  }
  scrollToMessage(msg.row);
}

/* ==========================================================================
   ЭЛЕМЕНТЫ ЛЕНТЫ
   ========================================================================== */

function addUserMessage(text) {
  const row = el("div", "msg msg--user");
  const bubble = el("div", "bubble");
  bubble.append(el("p", "bubble-text", text));
  row.append(bubble);
  feedEl.append(row);
  scrollToBottom();
}

function addBotMessage(text, { variant = null, wide = false } = {}) {
  const row = el("div", "msg msg--bot");
  const avatar = el("div", "avatar");
  avatar.setAttribute("aria-hidden", "true");
  avatar.innerHTML = ICON_BOT; // статичная строка, не данные

  const body = el("div", wide ? "msg-body msg-body--wide" : "msg-body");
  let bubble = null;
  if (text) {
    bubble = el("div", variant === "error" ? "bubble bubble--error" : "bubble");
    if (variant === "error") {
      const icon = el("span", "bubble-icon", "⚠️");
      icon.setAttribute("aria-hidden", "true");
      bubble.append(icon);
    }
    bubble.append(el("p", "bubble-text", text)); // textContent — безопасно
    body.append(bubble);
  }

  row.append(avatar, body);
  feedEl.append(row);
  return { row, body, bubble };
}

function showTyping() {
  hideTyping();
  const row = el("div", "msg msg--bot");
  row.id = "typing";
  const avatar = el("div", "avatar");
  avatar.setAttribute("aria-hidden", "true");
  avatar.innerHTML = ICON_BOT;

  const bubble = el("div", "bubble typing");
  bubble.setAttribute("role", "status");
  bubble.append(el("span", "visually-hidden", "Помощник печатает…"));
  for (let i = 0; i < 3; i++) {
    const dot = el("span", "typing-dot");
    dot.setAttribute("aria-hidden", "true");
    bubble.append(dot);
  }

  const body = el("div", "msg-body");
  body.append(bubble);
  row.append(avatar, body);
  feedEl.append(row);
  scrollToBottom();
}

function hideTyping() {
  document.getElementById("typing")?.remove();
}

// Когда пользователь пишет текст, висящие кнопки прошлого ответа убираем
function retireActiveControls() {
  feedEl.querySelectorAll("[data-active]").forEach((node) => {
    if (node.dataset.active === "step") collapseStep(node, "skipped");
    else node.remove();
  });
}

function resetConversation() {
  if (state.busy) return;
  state.ticketId = null;
  state.token = null;
  state.stepTrack = { key: null, index: -1 };
  // Этап 4: очистить сохранённое обращение в localStorage

  feedEl.replaceChildren();
  emptyEl.hidden = false;
  chatEl.scrollTo({ top: 0 });
  inputEl.focus();
}

/* ==========================================================================
   ПОЛЕ ВВОДА
   ========================================================================== */

function onInput() {
  autoResize();
  updateComposer();
}

function onKeydown(e) {
  // isComposing — не отправляем, пока идёт ввод через IME
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    submitInput();
  }
}

function submitInput() {
  const text = inputEl.value.trim();
  if (!text || state.busy) return;
  inputEl.value = "";
  onInput();
  send({ message: text }, { echo: text });
}

function updateComposer() {
  const len = inputEl.value.length;
  counterEl.hidden = len <= COUNTER_FROM;
  counterEl.textContent = `${len} / ${MAX_LEN}`;
  counterEl.classList.toggle("is-limit", len >= MAX_LEN);
  sendBtn.disabled = state.busy || inputEl.value.trim() === "";
}

function autoResize() {
  inputEl.style.height = "auto";
  inputEl.style.height = `${inputEl.scrollHeight}px`; // потолок задан max-height в CSS
}

function setBusy(value) {
  state.busy = value;
  chatEl.setAttribute("aria-busy", String(value));
  updateComposer();
}

/* ==========================================================================
   ПРОКРУТКА И УТИЛИТЫ
   ========================================================================== */

function scrollBehavior() {
  return reducedMotion.matches ? "auto" : "smooth";
}

function scrollToBottom() {
  requestAnimationFrame(() => {
    chatEl.scrollTo({ top: chatEl.scrollHeight, behavior: scrollBehavior() });
  });
}

// Длинный ответ — показываем его начало, короткий — докручиваем до конца
function scrollToMessage(node) {
  requestAnimationFrame(() => {
    const chatRect = chatEl.getBoundingClientRect();
    const nodeRect = node.getBoundingClientRect();
    const nodeTop = nodeRect.top - chatRect.top + chatEl.scrollTop - 16;
    const tooTall = nodeRect.height > chatEl.clientHeight - 32;
    chatEl.scrollTo({
      top: tooTall ? nodeTop : chatEl.scrollHeight,
      behavior: scrollBehavior(),
    });
  });
}

// После клика по исчезнувшей кнопке фокус теряется — возвращаем его в ленту
function focusIfLost(node) {
  if (!node) return;
  if (!document.activeElement || document.activeElement === document.body) {
    node.focus({ preventScroll: true });
  }
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function uuid() {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  // file:// не считается безопасным контекстом — собираем UUID v4 вручную
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

function randomHex(bytes) {
  return Array.from(crypto.getRandomValues(new Uint8Array(bytes)), (x) =>
    x.toString(16).padStart(2, "0")
  ).join("");
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/* ==========================================================================
   МОКИ
   --------------------------------------------------------------------------
   Сценарии по первому сообщению:
     «Wi-Fi», «VPN»  — вопрос с кнопками → шаги → итоговая карточка
                       («Не получилось» на последнем шаге → эскалация)
     «принтер»       — вопрос → шаги (короткий сценарий, 3 шага)
     «пароль»        — сразу эскалация
     любой другой    — choice с вариантами категорий
   Отладочные команды (ввести в поле):
     /error   — reply.type = "error"
     /429     — HTTP 429
     /404     — HTTP 404
     /offline — сеть недоступна
     /slow    — ответ через 4 секунды
     /long    — очень длинный ответ
   Сообщение в закрытое обращение → HTTP 409.
   ========================================================================== */

const MOCK_SCENARIOS = {
  wifi: {
    category: "Wi-Fi",
    confidence: 0.91,
    article: { id: "KB-WIFI-002", title: "Подключение к corp-wifi" },
    question: "На каком устройстве не подключается?",
    options: ["Ноутбук Windows", "MacBook", "Телефон"],
    slot: "device",
    steps: [
      "Проверьте, что Wi-Fi включён, а режим «В самолёте» выключен",
      "Откройте список сетей, выберите corp-wifi и нажмите «Забыть сеть»",
      "Подключитесь к corp-wifi заново и введите корпоративный логин и пароль",
      "Если появится запрос сертификата — нажмите «Доверять» и подождите 10 секунд",
    ],
  },
  vpn: {
    category: "VPN",
    confidence: 0.78,
    article: { id: "KB-VPN-004", title: "Не подключается корпоративный VPN" },
    question: "Что показывает VPN-клиент?",
    options: ["Неверный логин или пароль", "Сервер недоступен", "Ошибки нет, но не подключается"],
    slot: "vpn_error",
    steps: [
      "Проверьте интернет без VPN — откройте любой сайт",
      "Закройте VPN-клиент в трее и запустите его снова",
      "Проверьте, что дата и время на компьютере выставляются автоматически",
      "Удалите профиль подключения и загрузите его заново с корпоративного портала",
    ],
  },
  printer: {
    category: "Принтер",
    confidence: 0.64,
    article: { id: "KB-PRN-001", title: "Принтер не печатает" },
    question: "Что происходит с принтером?",
    options: ["Мигает индикатор", "Документ завис в очереди", "Ничего не происходит"],
    slot: "printer_state",
    steps: [
      "Проверьте, что принтер включён и в лотке есть бумага",
      "Откройте очередь печати и удалите зависшие документы",
      "Выключите принтер на 30 секунд и включите снова",
    ],
  },
};

const LONG_TEXT = Array.from(
  { length: 30 },
  (_, i) => `${i + 1}. Длинный абзац для проверки прокрутки: лента должна остановиться на начале ответа, а не на его конце.`
).join("\n");

const mock = {
  ticketId: null, token: null, scenario: null, phase: null,
  step: 0, slots: {}, stepsDone: [], problem: "", actions: 0,
  failed: new Set(), // request_id, на которых уже «упали»
};

async function mockResponse(path, body) {
  const text = (body.message || "").trim();
  const cmd = text.toLowerCase();

  await sleep(cmd === "/slow" ? 4000 : 600 + Math.random() * 500);

  // Сбои срабатывают один раз: «Повторить» с тем же request_id уже проходит
  const firstTry = !mock.failed.has(body.request_id);
  if (firstTry && ["/offline", "/429"].includes(cmd)) {
    mock.failed.add(body.request_id);
    throw cmd === "/429" ? new ApiError(429) : new ApiError(0, "network");
  }
  if (cmd === "/404") throw new ApiError(404);
  if (path.includes("/rate")) return { ok: true };

  if (!body.ticket_id) {
    // Новое обращение
    Object.assign(mock, {
      ticketId: "t_" + randomHex(2), token: "s_" + randomHex(8),
      scenario: null, phase: "start", step: 0, slots: {}, stepsDone: [],
      problem: text, actions: 0,
    });
  } else if (body.ticket_id !== mock.ticketId) {
    throw new ApiError(404); // например, после перезагрузки страницы
  }
  mock.actions++;

  if (cmd === "/error") {
    return mockReply("CLASSIFYING", { type: "error", text: "Не получилось обработать сообщение. Попробуйте ещё раз." });
  }
  if (cmd === "/long") {
    return mockReply("CLARIFYING", { type: "question", text: LONG_TEXT, quick_replies: ["Понятно"] });
  }

  const answer = body.quick_reply || text;
  switch (mock.phase) {
    case "start":
    case "choice":
      return mockClassify(answer);
    case "question": {
      const sc = MOCK_SCENARIOS[mock.scenario];
      mock.slots[sc.slot] = answer;
      mock.phase = "steps";
      mock.step = 0;
      return mockSteps("Попробуем решить по шагам. Отмечайте, что получилось.");
    }
    case "steps":
      return mockStepAnswer(body.quick_reply);
    default:
      throw new ApiError(409); // обращение уже закрыто
  }
}

function mockClassify(text) {
  const t = text.toLowerCase();
  let key = null;
  if (/wi-?fi|вай-?фай/.test(t)) key = "wifi";
  else if (/vpn|впн/.test(t)) key = "vpn";
  else if (/принтер|печат/.test(t)) key = "printer";

  if (key) {
    mock.scenario = key;
    mock.phase = "question";
    const sc = MOCK_SCENARIOS[key];
    return mockReply("CLARIFYING", { type: "question", text: sc.question, quick_replies: sc.options });
  }

  if (/парол|другое/.test(t)) {
    return mockEscalation(
      "Сброс пароля делает специалист — это требование безопасности. Обращение передано, с вами свяжутся в течение 15 минут.",
      { category: "Учётная запись", confidence: 0.55, article: { id: "KB-ACC-010", title: "Сброс пароля" } }
    );
  }

  mock.phase = "choice";
  return mockReply(
    "CLASSIFYING",
    { type: "choice", text: "Уточните, пожалуйста, о чём речь:", quick_replies: ["Wi-Fi", "VPN", "Принтер", "Другое"] },
    { category: null, confidence: 0.42 }
  );
}

function mockSteps(intro = "") {
  const sc = MOCK_SCENARIOS[mock.scenario];
  return mockReply("SOLVING", { type: "steps", text: intro, steps: sc.steps, step_index: mock.step });
}

function mockStepAnswer(quickReply) {
  const sc = MOCK_SCENARIOS[mock.scenario];
  const current = sc.steps[mock.step];
  const isLast = mock.step >= sc.steps.length - 1;

  if (quickReply === QR.RESOLVED || (quickReply === QR.STEP_OK && isLast)) {
    mock.stepsDone.push(current);
    return mockSummary();
  }
  if (quickReply === QR.STEP_OK) {
    mock.stepsDone.push(current);
    mock.step++;
    return mockSteps();
  }
  if (quickReply === QR.NOT_HELPED) {
    if (isLast) {
      return mockEscalation("Шаги не помогли — передаю обращение специалисту. Он увидит, на каком шаге возникла проблема.");
    }
    mock.step++;
    return mockSteps("Понял. Тогда попробуем так:");
  }
  // Пользователь написал текст вместо кнопки
  return mockSteps("Давайте сначала закончим текущий шаг — отметьте результат кнопкой.");
}

function mockSummary() {
  mock.phase = "closed";
  return mockReply(
    "RESOLVED",
    { type: "summary", text: "Отлично, проблема решена! Вот итог обращения." },
    { ticket_card: mockCard(true) }
  );
}

function mockEscalation(text, override = {}) {
  mock.phase = "closed";
  return mockReply("ESCALATED", { type: "escalation", text }, { ...override, ticket_card: mockCard(false, override) });
}

function mockCard(resolved, override = {}) {
  const sc = MOCK_SCENARIOS[mock.scenario];
  return {
    ticket_id: mock.ticketId,
    category: override.category ?? sc?.category ?? "Другое",
    problem_summary: mock.problem || "—",
    slots: { ...mock.slots },
    steps_done: [...mock.stepsDone],
    resolved_by_bot: resolved,
    needs_specialist: !resolved,
    article_id: override.article?.id ?? sc?.article.id ?? null,
    created_at: new Date().toISOString().slice(0, 19),
  };
}

function mockReply(stateName, reply, extra = {}) {
  const sc = MOCK_SCENARIOS[mock.scenario];
  return {
    ticket_id: mock.ticketId,
    token: mock.token,
    state: stateName,
    category: sc?.category ?? null,
    confidence: sc?.confidence ?? null,
    article: sc?.article ?? null,
    reply: { text: "", quick_replies: [], steps: [], ...reply },
    ticket_card: null,
    user_actions_count: mock.actions,
    ...extra,
  };
}
