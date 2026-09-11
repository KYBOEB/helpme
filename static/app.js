"use strict";

/* ==========================================================================
   «Помоги мне» — страница чата
   Этап 1: моки + каркас + отрисовка question, choice и steps.
   summary/escalation — временная заглушка, полноценная карточка
   будет на этапе 2.
   ========================================================================== */

// ---------- Настройки ----------

const MOCK = false;  // переключается на false, когда бэкенд готов

const API_CHAT = "/api/chat";
const MAX_LEN = 2000;             // лимит длины сообщения
const COUNTER_FROM = 1800;        // счётчик появляется после этого числа символов
const REQUEST_TIMEOUT_MS = 20000; // дольше ждать нет смысла — показываем ошибку

// Итог пошагового гида (решение тимлида): сервер присылает ВСЕ шаги сразу,
// клиент показывает их по одному и отправляет на сервер ОДНО сообщение за весь гид.
//   «Всё получилось» на любом шаге      → quick_reply "Получилось"
//   «Проблема ещё не решена» на последнем → quick_reply "Не получилось"
// Кем решена проблема (ботом или специалистом) — определяет бэкенд в ticket_card.
const QR = {
  SOLVED: "Получилось",
  NOT_SOLVED: "Не получилось",
};

// Сообщение, когда шаги из базы знаний не помогли и дальше отвечает ИИ (source: "general")
const AI_HANDOFF_TEXT = "Рекомендации из базы знаний не помогли. Сейчас вам ответит ИИ-ассистент, ожидайте.";
const GENERAL_BADGE_TEXT = "Совет ИИ-ассистента";

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

// Аватары — одна буква вместо иконки: на любом экране читается лучше мелкой графики.
// Буква нарисована в SVG, а не текстом в блоке: text-anchor и dominant-baseline
// центрируют её по геометрии глифа, а не по строке, поэтому она стоит ровно
// в середине кружка при любом системном шрифте.
function avatarLetter(letter) {
  return `<svg viewBox="0 0 32 32" width="32" height="32" aria-hidden="true">`
       + `<text x="16" y="16" text-anchor="middle" dominant-baseline="middle"`
       + ` font-size="16" font-weight="600" fill="currentColor">${letter}</text></svg>`;
}

const ICON_BOT = avatarLetter("а");        // «а» — Актион
const ICON_OPERATOR = avatarLetter("с");   // «с» — специалист

// Опрос новых реплик специалиста, пока обращение у человека
const POLL_INTERVAL_MS = 6000;

// Незавершённое обращение переживает перезагрузку страницы.
// Храним только номер и токен — переписка остаётся на сервере.
const STORE_KEY = "helpme:ticket";
const STORE_TTL_MS = 24 * 60 * 60 * 1000;

// ---------- Состояние ----------

const state = {
  ticketId: null,
  token: null,
  busy: false,
  conv: 0, // номер диалога: ответы на запросы из прошлого диалога не отрисовываются
  pollTimer: null,  // опрос реплик специалиста
  lastMsgId: 0,     // последнее показанное сообщение переписки
};

// ---------- DOM ----------

let chatEl, feedEl, emptyEl, inputEl, sendBtn, counterEl;
let composerEl, heroSlotEl, dockEl, backBtn, resumeEl;
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

document.addEventListener("DOMContentLoaded", init);

function init() {
  chatEl = document.getElementById("chat");
  feedEl = document.getElementById("feed");
  emptyEl = document.getElementById("empty");
  inputEl = document.getElementById("input");
  sendBtn = document.getElementById("send-btn");
  counterEl = document.getElementById("counter");
  composerEl = document.getElementById("composer");
  heroSlotEl = document.getElementById("hero-slot");
  dockEl = document.getElementById("dock");
  backBtn = document.getElementById("back-btn");
  resumeEl = document.getElementById("resume");

  // Режим моков виден только разработчику: во вкладке браузера и в консоли
  if (MOCK) {
    document.title = "[MOCK] " + document.title;
    console.warn("Режим моков: ответы берутся из app.js. Перед защитой поставьте MOCK = false.");
  }

  inputEl.maxLength = MAX_LEN;
  inputEl.addEventListener("input", onInput);
  inputEl.addEventListener("keydown", onKeydown);
  sendBtn.addEventListener("click", submitInput);

  // Частые проблемы: клик сразу отправляет, «Другая проблема» — просит описать
  document.querySelectorAll(".example").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (btn.dataset.action === "other") return startOtherProblem();
      const text = btn.dataset.example;
      send({ message: text }, { echo: text });
    });
  });

  // «На главную»: просто уходим из диалога, ничего не отправляя
  backBtn.addEventListener("click", resetConversation);

  document.getElementById("resume-continue").addEventListener("click", resumeTicket);
  document.getElementById("resume-new").addEventListener("click", () => {
    // Отказались продолжать — старое обращение закрываем, чтобы оно
    // не висело в панели оператора как активное
    const saved = loadSaved();
    if (saved) closeTicket(saved.ticketId, saved.token);
    clearSaved();
    hideResume();
  });
  offerResume();

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
 * @param {{echo?: string, requestId?: string, afterReply?: Function}} opts
 *   echo — текст, который показать в ленте как сообщение пользователя;
 *   requestId — передаётся при повторе, чтобы бэкенд мог отбросить дубль;
 *   afterReply — вызывается с ответом сервера перед отрисовкой.
 */
async function send({ message = null, quickReply = null }, { echo = null, requestId = null, afterReply = null } = {}) {
  if (state.busy) return;                         // второе нажатие игнорируем
  const text = typeof message === "string" ? message.trim() : "";
  if (!quickReply && !text) return;               // пустое не отправляем

  setMode("chat");                                // поле ввода уезжает вниз
  hideResume();                                   // начался новый диалог
  retireActiveControls();                         // старые кнопки больше не актуальны
  if (echo) addUserMessage(echo);

  const payload = {
    ticket_id: state.ticketId,
    token: state.token,
    request_id: requestId || uuid(),
    message: quickReply ? null : text,
    quick_reply: quickReply || null,
  };

  const conv = state.conv;
  setBusy(true);
  showTyping();

  let data = null;
  let error = null;
  try {
    data = await apiPost(API_CHAT, payload);
  } catch (e) {
    error = e;
  }

  // Пользователь уже ушёл на главную — ответ старого диалога не показываем
  if (conv !== state.conv) return;

  hideTyping();
  setBusy(false);

  if (error) {
    renderRequestError(error, payload);
    return;
  }

  if (data.ticket_id) state.ticketId = data.ticket_id;
  if (data.token) state.token = data.token;
  saveTicket(data);

  try {
    if (afterReply) afterReply(data);
    render(data, payload);
    // Обращение у человека — начинаем следить за его ответами
    if (data.state === "ESCALATED") startPolling();
    else if (data.state === "RESOLVED") stopPolling();
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
    case "escalation":
    case "closed":     return renderCard(data);
    case "operator":   return renderOperatorAck(data);
    case "error":      return renderErrorReply(data, payload);
    default:
      console.warn("Неизвестный reply.type:", reply.type);
      return renderQuestion(data); // покажем хотя бы текст
  }
}

// source: "general" — ответ не из базы знаний: плашка и приглушённое оформление.
// Поле необязательное: если его нет, считаем ответ обычным (из базы знаний).
function isGeneral(data) {
  return data.reply?.source === "general";
}

function markGeneral(msg) {
  msg.row.classList.add("msg--general");
  const badge = el("div", "source-badge");
  const icon = el("span", "source-badge-icon", "i");
  icon.setAttribute("aria-hidden", "true");
  badge.append(icon, GENERAL_BADGE_TEXT);
  msg.body.prepend(badge);
}

// question — вопрос + кнопки быстрых ответов
function renderQuestion(data) {
  const { reply } = data;
  const msg = addBotMessage(reply.text);
  if (isGeneral(data)) markGeneral(msg);
  // Этап 2: renderBadge(msg.body, data) — категория, уверенность, источник (4.4)
  renderQuickReplies(msg.body, reply.quick_replies);
  scrollToMessage(msg.row);
}

// choice — «уточните, о чём речь» + варианты категорий
function renderChoice(data) {
  const { reply } = data;
  const msg = addBotMessage(reply.text || "Уточните, пожалуйста, о чём речь:");
  if (isGeneral(data)) markGeneral(msg);
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

// steps — сервер присылает все шаги сразу, клиент показывает их по одному.
// Переход к следующему шагу — без запроса к серверу.
function renderSteps(data) {
  const { reply } = data;
  const steps = (Array.isArray(reply.steps) ? reply.steps : []).map(stepText).filter(Boolean);
  if (steps.length === 0) return renderQuestion(data);

  const source = isGeneral(data) ? "general" : "kb";
  const msg = addBotMessage(reply.text, { wide: true });
  if (source === "general") markGeneral(msg);
  showStep(msg.body, steps, 0, source);
  // Кнопки из quick_replies (например «Позвать специалиста») — наравне с шагами
  renderQuickReplies(msg.body, reply.quick_replies);
  scrollToMessage(msg.row);
}

function showStep(container, steps, index, source) {
  const total = steps.length;
  const text = steps[index];

  const card = el("div", "step-card");
  card.dataset.active = "step";
  card.dataset.label = `Шаг ${index + 1} из ${total}: ${text}`;

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
  const okBtn = el("button", "btn btn--primary", "Всё получилось");
  const failBtn = el("button", "btn btn--ghost", "Проблема ещё не решена");
  okBtn.type = failBtn.type = "button";
  okBtn.addEventListener("click", () => answerStep(card, true, steps, index, source));
  failBtn.addEventListener("click", () => answerStep(card, false, steps, index, source));
  actions.append(okBtn, failBtn);

  card.append(head, progress, body, actions);
  // Новый шаг встаёт над кнопками быстрых ответов, если они есть
  const chips = container.querySelector(":scope > .quick-replies");
  if (chips) container.insertBefore(card, chips);
  else container.append(card);
  focusIfLost(okBtn);
  return card;
}

// «Всё получилось» — сразу одно сообщение на сервер, дальше шаги не показываем.
// «Проблема ещё не решена» — следующий шаг локально; на последнем шаге — одно сообщение.
function answerStep(card, solved, steps, index, source) {
  if (state.busy || card.dataset.active !== "step") return; // двойное нажатие
  collapseStep(card, solved ? "ok" : "failed");

  if (solved) return send({ quickReply: QR.SOLVED });

  if (index < steps.length - 1) {
    const next = showStep(card.parentElement, steps, index + 1, source);
    scrollToMessage(next);
    return;
  }

  // Шаги кончились. Если это были шаги из базы знаний — предупреждаем, что дальше ответит ИИ.
  // Если сервер вместо ИИ-ответа пришлёт что-то другое, предупреждение убираем.
  if (source === "kb") {
    const note = addBotMessage(AI_HANDOFF_TEXT);
    scrollToMessage(note.row);
    return send({ quickReply: QR.NOT_SOLVED }, {
      afterReply: (data) => { if (!isGeneral(data)) note.row.remove(); },
    });
  }
  send({ quickReply: QR.NOT_SOLVED });
}

// Сворачиваем шаг: остаётся в ленте приглушённым, с отметкой результата
function collapseStep(card, result) {
  const label = card.dataset.label || "Шаг";
  delete card.dataset.active;
  card.className = `step-card is-collapsed is-${result}`;
  card.replaceChildren();

  const marks = { ok: "✓", failed: "✕", skipped: "–" };
  const srText = { ok: "Проблема решена на этом шаге. ", failed: "Не помогло. ", skipped: "Пропущено. " };

  const mark = el("span", "step-mark", marks[result]);
  mark.setAttribute("aria-hidden", "true");
  const textEl = el("span", "step-collapsed-text");
  textEl.append(el("span", "visually-hidden", srText[result]), label);
  textEl.title = label;

  card.append(mark, textEl);
}

// Шаг может прийти строкой или объектом — формат в контракте не зафиксирован
function stepText(step) {
  if (typeof step === "string") return step;
  return step?.text || step?.title || "";
}

// summary / escalation — ВРЕМЕННАЯ версия. Этап 2: карточка из 4 блоков (4.6).
// Исход определяется только данными бэкенда:
//   resolved    — state RESOLVED                      → [Новое обращение]
//   transferred — state ESCALATED (уже у специалиста) → [Завершить обращение]
//   unsolved    — рекомендации не помогли, специалист ещё не подключён
//                 → [Завершить обращение] [Обратиться к специалисту]
function renderCard(data) {
  const { reply } = data;
  const card = data.ticket_card || {};
  const ticketId = card.ticket_id || data.ticket_id;
  const outcome = cardOutcome(data);

  const fallbackText = {
    resolved: "Проблема решена.",
    transferred: "Обращение передано специалисту.",
    unsolved: "К сожалению, рекомендации не помогли.",
    closed: "Обращение закрыто.",
  };
  const msg = addBotMessage(reply.text || fallbackText[outcome]);

  // Нецелевое обращение: карточку задачи не показываем — задачи не было.
  // Оценку тоже не просим: оценивать здесь нечего.
  if (outcome === "closed") {
    const actions = el("div", "msg-actions card-actions");
    actions.append(button("btn btn--primary", "Новое обращение", resetConversation));
    msg.body.append(actions);
    scrollToMessage(msg.row);
    return;
  }

  // Карточки-плашки в чате нет: она дословно повторяла текст сообщения выше.
  // Всё, что должен понять пользователь — какая проблема, решена ли она,
  // что дальше и нужен ли специалист — есть в самом тексте ответа.
  // Полная карточка обращения формируется и живёт в панели оператора,
  // выгружается в JSON и уходит вебхуком во внешнюю систему.
  msg.body.append(renderRating(ticketId));

  const actions = el("div", "msg-actions card-actions");
  const status = el("p", "card-status");
  status.setAttribute("role", "status");

  if (outcome === "resolved") {
    actions.append(button("btn btn--ghost", "Новое обращение", resetConversation));
  } else {
    actions.append(button("btn btn--ghost", "Завершить обращение", resetConversation));
  }

  // Обращение у человека — диалог продолжается здесь же, окно можно не закрывать
  if (outcome === "transferred") {
    status.textContent = "Можно продолжать писать здесь: ответ специалиста придёт в этот же чат.";
    startPolling();
  }

  if (outcome === "unsolved") {
    const escBtn = button("btn btn--primary", "Обратиться к специалисту", async () => {
      if (escBtn.disabled) return; // двойное нажатие
      escBtn.disabled = true;
      status.textContent = "";
      try {
        await escalateTicket(ticketId);
        escBtn.remove();
        status.textContent = "Обращение передано специалисту — он увидит всё, что вы уже попробовали. Ответ придёт в этот же чат.";
        startPolling();
      } catch (e) {
        escBtn.disabled = false;
        status.textContent = e.status === 429 ? ERROR_TEXT[429] : "Не удалось передать обращение. Попробуйте ещё раз.";
      }
    });
    actions.append(escBtn);
  }

  msg.body.append(actions, status);
  scrollToMessage(msg.row);
}

function cardOutcome(data) {
  const card = data.ticket_card || {};
  // Нецелевое обращение: закрыто системой, специалиста не звали.
  if (data.reply?.type === "closed" || card.out_of_scope === true) return "closed";
  if (data.state === "RESOLVED") return "resolved";
  if (data.state === "ESCALATED") return "transferred";
  if (data.reply?.type === "summary" && card.needs_specialist !== true) return "resolved";
  return "unsolved";
}

// Оценка ответа: 👍 = 5, 👎 = 1 (API принимает 1..5)
function renderRating(ticketId) {
  const wrap = el("div", "rating");
  const label = el("p", "rating-label", "Помог ли ответ?");
  const buttons = el("div", "msg-actions");
  const status = el("p", "rating-status");
  status.setAttribute("role", "status");

  // Оценка бинарная: 1 — помогло, 0 — нет. Из неё считается доля полезных
  // ответов в процентах; средний балл по пятибалльной шкале на таком
  // количестве оценок не значил бы ничего.
  const options = [
    { text: "👍 Помогло", rating: 1 },
    { text: "👎 Не помогло", rating: 0 },
  ];
  const btns = options.map(({ text, rating }) => {
    const btn = el("button", "btn btn--ghost", text);
    btn.type = "button";
    btn.addEventListener("click", async () => {
      if (wrap.dataset.sending) return; // двойное нажатие
      wrap.dataset.sending = "1";
      btns.forEach((b) => (b.disabled = true));
      status.textContent = "";
      try {
        await rateTicket(ticketId, rating);
        label.remove();
        buttons.remove();
        status.textContent = "Спасибо за оценку!";
      } catch (e) {
        delete wrap.dataset.sending;
        btns.forEach((b) => (b.disabled = false));
        status.textContent = e.status === 429 ? ERROR_TEXT[429] : "Оценка не отправилась. Попробуйте ещё раз.";
      }
    });
    return btn;
  });

  buttons.append(...btns);
  wrap.append(label, buttons, status);
  return wrap;
}

// POST /api/tickets/{id}/escalate — передать специалисту, тоже с token
function escalateTicket(ticketId) {
  return apiPost(`/api/tickets/${encodeURIComponent(ticketId)}/escalate`, { token: state.token });
}

// POST /api/tickets/{id}/close — пользователь ушёл из диалога.
// Без этого брошенное обращение продолжало висеть в панели как активное.
// Ошибку глушим: уход пользователя не должен упираться в сеть.
function closeTicket(ticketId, token) {
  if (!ticketId || !token || MOCK) return;
  apiPost(`/api/tickets/${encodeURIComponent(ticketId)}/close`, { token })
    .catch(() => {});
}

// POST /api/tickets/{id}/rate — теперь обязательно с token из ответа /api/chat
function rateTicket(ticketId, rating) {
  return apiPost(`/api/tickets/${encodeURIComponent(ticketId)}/rate`, {
    rating,
    token: state.token,
  });
}

/* ==========================================================================
   НЕЗАВЕРШЁННОЕ ОБРАЩЕНИЕ
   Перезагрузка страницы больше не теряет диалог: номер и токен лежат
   в браузере, переписка — на сервере. Любое обращение к localStorage
   обёрнуто в try/catch: в приватном окне он может быть недоступен.
   ========================================================================== */

function saveTicket(data) {
  if (!state.ticketId || !state.token) return;
  // Закрытое обращение восстанавливать нечего и незачем
  if (data && (data.state === "RESOLVED" || data.reply?.type === "closed")) {
    return clearSaved();
  }
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify({
      ticketId: state.ticketId, token: state.token, savedAt: Date.now(),
    }));
  } catch { /* приватное окно или переполнение — работаем без сохранения */ }
}

function loadSaved() {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    if (!raw) return null;
    const saved = JSON.parse(raw);
    if (!saved?.ticketId || !saved?.token) return null;
    if (Date.now() - (saved.savedAt || 0) > STORE_TTL_MS) return null;
    return saved;
  } catch {
    return null;
  }
}

function clearSaved() {
  try { localStorage.removeItem(STORE_KEY); } catch { /* не критично */ }
}

function offerResume() {
  if (MOCK || !loadSaved()) return;
  resumeEl.hidden = false;
}

function hideResume() {
  if (resumeEl) resumeEl.hidden = true;
}

async function resumeTicket() {
  const saved = loadSaved();
  if (!saved) return hideResume();
  hideResume();

  let data;
  try {
    data = await apiPost("/api/chat/updates", {
      ticket_id: saved.ticketId, token: saved.token, after: 0, full: true,
    });
  } catch (e) {
    // 404 — обращение удалено или токен больше не подходит: забываем о нём
    clearSaved();
    showError(e.status === 404
      ? "Прошлое обращение больше недоступно. Начните новое."
      : ERROR_TEXT.network);
    return;
  }

  state.ticketId = saved.ticketId;
  state.token = saved.token;
  state.lastMsgId = data.last_id || 0;

  setMode("chat");
  feedEl.replaceChildren();
  for (const m of data.messages || []) {
    if (m.role === "user") addUserMessage(m.text);
    else if (m.role === "operator") addOperatorMessage(m.text);
    else addBotMessage(m.text);
  }

  const note = addBotMessage(`Обращение №${saved.ticketId} восстановлено. `
    + `Продолжайте — контекст я помню.`);

  scrollToMessage(note.row);

  // Шаги в переписке не хранятся — сервер отдаёт их отдельным полем.
  // Возвращаем полноценный пошаговый гид, а не список текстом: пользователь
  // продолжит ровно с теми же кнопками, что были до перезагрузки.
  const steps = Array.isArray(data.steps) ? data.steps : [];
  if (data.state === "VERIFYING" && steps.length) {
    renderSteps({ reply: { type: "steps", text: "", steps,
                           source: data.assist_used ? "general" : "kb" } });
  } else if (data.state === "VERIFYING") {
    renderQuickReplies(note.body, [QR.SOLVED, QR.NOT_SOLVED]);
  }

  if (data.state === "ESCALATED") startPolling();
}

/* ==========================================================================
   ЖИВОЙ СПЕЦИАЛИСТ
   Обращение передано человеку. Бот в переписку не вмешивается: страница
   раз в несколько секунд спрашивает сервер, не написал ли специалист.
   ========================================================================== */

// Подтверждение, что реплика пользователя ушла человеку
function renderOperatorAck(data) {
  const msg = addBotMessage(data.reply?.text || "Сообщение передано специалисту.");
  scrollToMessage(msg.row);
  startPolling();
}

function startPolling() {
  if (state.pollTimer || !state.ticketId) return;
  state.pollTimer = setInterval(pollOperator, POLL_INTERVAL_MS);
  pollOperator();
}

function stopPolling() {
  if (!state.pollTimer) return;
  clearInterval(state.pollTimer);
  state.pollTimer = null;
}

async function pollOperator() {
  if (!state.ticketId || !state.token || MOCK) return stopPolling();
  const conv = state.conv;

  let data;
  try {
    data = await apiPost("/api/chat/updates", {
      ticket_id: state.ticketId,
      token: state.token,
      after: state.lastMsgId,
    });
  } catch (e) {
    // Сеть моргнула — молчим и пробуем на следующем тике. Обращение потеряно
    // (404) или сервер закрыл доступ — прекращаем опрос, чтобы не долбиться.
    if (e instanceof ApiError && (e.status === 404 || e.status === 409)) stopPolling();
    return;
  }

  if (conv !== state.conv) return;         // пользователь уже ушёл на главную

  if (typeof data.last_id === "number") state.lastMsgId = data.last_id;
  for (const m of data.messages || []) {
    addOperatorMessage(m.text);
    if (typeof m.id === "number" && m.id > state.lastMsgId) state.lastMsgId = m.id;
  }
  if (data.state === "RESOLVED") {
    stopPolling();
    clearSaved();
    // Специалист закрыл обращение — молча обрывать диалог нельзя
    const msg = addBotMessage("Специалист завершил обращение. "
      + "Если проблема вернётся — начните новое, я помогу.");
    const actions = el("div", "msg-actions");
    actions.append(button("btn btn--ghost", "Новое обращение", resetConversation));
    msg.body.append(actions);
    scrollToMessage(msg.row);
  }
}

function addOperatorMessage(text) {
  const row = el("div", "msg msg--bot msg--operator");
  const avatar = el("div", "avatar");
  avatar.setAttribute("aria-hidden", "true");
  avatar.innerHTML = ICON_OPERATOR;       // статичная строка, не данные

  const body = el("div", "msg-body");
  const bubble = el("div", "bubble");
  bubble.append(el("p", "operator-label", "Специалист поддержки"));
  bubble.append(el("p", "bubble-text", text));   // textContent — безопасно
  body.append(bubble);

  row.append(avatar, body);
  feedEl.append(row);
  scrollToMessage(row);
}

// Строка «Результат» в карточке. «Без специалиста» — только если бэкенд
// явно поставил resolved_by_bot = true.
function resultText(data, outcome) {
  if (outcome === "transferred") return "передано специалисту";
  if (outcome === "unsolved") return "не решено — рекомендации не помогли";
  return data.ticket_card?.resolved_by_bot === true ? "✓ решено без специалиста" : "✓ решено";
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
    clearSaved();   // обращения больше нет — восстанавливать будет нечего
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

// «На главную», «Завершить обращение», «Новое обращение».
// Можно нажать и во время запроса: ответ старого диалога просто отбросится.
function resetConversation() {
  // Уходим из диалога — закрываем обращение на сервере. Продолжить его
  // уже нельзя: локальные номер и токен сейчас будут стёрты.
  closeTicket(state.ticketId, state.token);
  state.conv++;
  stopPolling();
  if (state.busy) {
    hideTyping();
    setBusy(false);
  }
  state.ticketId = null;
  state.token = null;
  state.lastMsgId = 0;
  clearSaved();
  hideResume();

  feedEl.replaceChildren();
  setMode("home");
  chatEl.scrollTo({ top: 0 });
  if (window.matchMedia("(hover: hover)").matches) inputEl.focus();
}

// Главный экран: поле ввода по центру над частыми проблемами.
// Диалог: то же самое поле переезжает в нижнюю панель.
function setMode(mode) {
  const home = mode === "home";
  if (emptyEl.hidden === !home) return; // уже в нужном режиме
  const hadFocus = composerEl.contains(document.activeElement);
  emptyEl.hidden = !home;
  dockEl.hidden = home;
  backBtn.hidden = home;
  (home ? heroSlotEl : dockEl).append(composerEl);
  if (hadFocus) inputEl.focus({ preventScroll: true }); // перенос в DOM сбрасывает фокус
}

// «Другая проблема»: темы заново не предлагаем и ничего не отправляем.
// Просим описать своими словами — описание уйдёт на сервер первым сообщением.
function startOtherProblem() {
  if (state.busy) return;
  setMode("chat");
  const msg = addBotMessage("Опишите свою проблему, и мы попытаемся её решить.");
  scrollToMessage(msg.row);
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

function button(className, text, onClick) {
  const btn = el("button", className, text);
  btn.type = "button";
  btn.addEventListener("click", onClick);
  return btn;
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
     «Wi-Fi», «VPN»     — вопрос → шаги из базы (source: "kb") одним ответом
                          «Всё получилось» на любом шаге → "Получилось" → итог «решено»
                          все шаги не помогли → "Не получилось" → общие рекомендации ИИ
                          (source: "general", плашка, кнопка «Позвать специалиста»)
                          общие тоже не помогли → «не решено»: [Завершить] [Обратиться к специалисту]
                          Переходы между шагами на сервер не ходят.
     «принтер»          — вопрос → 3 шага
     «интернет», «сеть» — choice: Wi-Fi или VPN
     «пароль»           — сразу передано специалисту (state ESCALATED)
     любой другой текст — сразу общие рекомендации (source: "general")
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
      "Закройте VPN-клиент в области уведомлений (возле часов) и запустите его снова",
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

// Общие рекомендации «от ИИ» — не из базы знаний
const MOCK_GENERAL_STEPS = [
  "Перезагрузите компьютер — это устраняет большую часть временных сбоев",
  "Проверьте, что установлены последние обновления системы и нужной программы",
  "Попробуйте то же действие на другом компьютере или под другой учётной записью",
];
const MOCK_CALL_SPECIALIST = "Позвать специалиста";

const LONG_TEXT = Array.from(
  { length: 30 },
  (_, i) => `${i + 1}. Длинный абзац для проверки прокрутки: лента должна остановиться на начале ответа, а не на его конце.`
).join("\n");

const mock = {
  ticketId: null, token: null, scenario: null, phase: null,
  slots: {}, stepsDone: [], problem: "", actions: 0,
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
  if (path.includes("/rate") || path.includes("/escalate")) {
    // Как на реальном API: без правильного token не принимаем
    if (!body.token || body.token !== mock.token) throw new ApiError(422);
    if (path.includes("/escalate")) mock.phase = "closed";
    return { ok: true };
  }

  if (!body.ticket_id) {
    // Новое обращение
    Object.assign(mock, {
      ticketId: "t_" + randomHex(2), token: "s_" + randomHex(8),
      scenario: null, phase: "start", slots: {}, stepsDone: [],
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
  if (body.quick_reply === MOCK_CALL_SPECIALIST && mock.phase !== "closed") {
    return mockTransfer("Обращение передано специалисту. Он увидит всё, что вы уже попробовали, и свяжется с вами.");
  }
  switch (mock.phase) {
    case "start":
    case "choice":
      return mockClassify(answer);
    case "question": {
      const sc = MOCK_SCENARIOS[mock.scenario];
      mock.slots[sc.slot] = answer;
      mock.phase = "steps";
      return mockSteps("Попробуем решить по шагам. Если на каком-то шаге всё заработает — сразу отметьте это.");
    }
    case "steps":
      return mockStepsResult(body.quick_reply);
    case "general":
      return mockGeneralResult(body.quick_reply);
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

  // Неоднозначно: «интернет» бывает и Wi-Fi, и VPN — уточняем только между ними
  if (/интернет|сеть|сети/.test(t)) {
    mock.phase = "choice";
    return mockReply(
      "CLASSIFYING",
      { type: "choice", text: "Уточните, пожалуйста: с каким подключением проблема?", quick_replies: ["Корпоративный Wi-Fi", "VPN из дома"] },
      { category: null, confidence: 0.48 }
    );
  }

  if (/парол/.test(t)) {
    return mockTransfer(
      "Сброс пароля делает специалист — это требование безопасности. Обращение передано, с вами свяжутся в течение 15 минут.",
      { category: "Учётная запись", confidence: 0.55, article: { id: "KB-ACC-010", title: "Сброс пароля" } }
    );
  }

  // В базе знаний ничего нет — сразу общие рекомендации
  return mockGeneral("Готового решения в базе знаний нет. Вот общие рекомендации — если не помогут, позовите специалиста.");
}

function mockSteps(intro = "") {
  const sc = MOCK_SCENARIOS[mock.scenario];
  return mockReply("SOLVING", { type: "steps", text: intro, steps: sc.steps });
}

// Сервер узнаёт только итог гида — одно сообщение вместо запроса на каждый шаг
function mockStepsResult(quickReply) {
  const sc = MOCK_SCENARIOS[mock.scenario];
  if (quickReply === QR.SOLVED) return mockSummary();
  if (quickReply === QR.NOT_SOLVED) {
    mock.stepsDone = [...sc.steps];
    return mockGeneral("Вот общие рекомендации. Если не помогут — позовите специалиста.");
  }
  return mockSteps("Давайте пройдём шаги по порядку — отмечайте результат кнопками.");
}

function mockGeneral(intro) {
  const sc = MOCK_SCENARIOS[mock.scenario];
  mock.phase = "general";
  return mockReply(
    "SOLVING",
    { type: "steps", source: "general", text: intro, steps: MOCK_GENERAL_STEPS, quick_replies: [MOCK_CALL_SPECIALIST] },
    { category: sc?.category ?? "Другое", confidence: sc ? sc.confidence : 0.3, article: null }
  );
}

function mockGeneralResult(quickReply) {
  if (quickReply === QR.SOLVED) return mockSummary();
  if (quickReply === QR.NOT_SOLVED) {
    mock.stepsDone.push(...MOCK_GENERAL_STEPS);
    mock.phase = "unsolved";
    // Не решено, но специалист ещё не подключён: state не ESCALATED
    return mockReply(
      "VERIFYING",
      { type: "escalation", text: "К сожалению, рекомендации не помогли. Вы можете обратиться к специалисту — он увидит всё, что вы уже попробовали." },
      { ticket_card: mockCard(false), article: null }
    );
  }
  return mockGeneral("Давайте пройдём рекомендации по порядку — отмечайте результат кнопками.");
}

function mockSummary() {
  mock.phase = "closed";
  return mockReply(
    "RESOLVED",
    { type: "summary", text: "Проблема решена. Если она повторится — начните новое обращение." },
    { ticket_card: mockCard(true) }
  );
}

// Обращение уже у специалиста
function mockTransfer(text, override = {}) {
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
    reply: { text: "", quick_replies: [], steps: [], source: "kb", ...reply },
    ticket_card: null,
    user_actions_count: mock.actions,
    ...extra,
  };
}
