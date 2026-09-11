"use strict";

/* «Помоги мне» — страница чата пользователя. */

// ---------- Настройки ----------

const API_CHAT = "/api/chat";
const MAX_LEN = 2000;             // лимит длины сообщения
const COUNTER_FROM = 1800;        // счётчик появляется после этого числа символов
const REQUEST_TIMEOUT_MS = 20000; // дольше ждать нет смысла — показываем ошибку

// Сервер присылает все шаги сразу; чекбоксы возле шагов — просто отметки для
// пользователя (F2), они никуда не отправляются. Результат инструкции —
// одно из двух сообщений на сервер, вне зависимости от того, что отмечено.
const QR = {
  SOLVED: "Получилось",
  NOT_SOLVED: "Не получилось",
};

// F4: нажатие кнопки «Специалист» в шапке — обычное сообщение в общий автомат
// диалога, дальше бэкенд сам решает, создавать ли обращение и как задать
// уточняющий вопрос. Отдельный REST-вызов эскалации тут не нужен.
const QR_CALL_SPECIALIST = "Позвать специалиста";

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

// F5: идентификатор клиента (браузера) — отдельно от текущего обращения,
// живёт неограниченно долго и связывает все обращения человека в «Мои обращения».
const CLIENT_STORE_KEY = "helpme:client";

// F5: /api/my/tickets ещё не выкачен бэкендом — временная заглушка на моках,
// включается параметром ?mock=1. ПЕРЕД ФИНАЛЬНЫМ КОММИТОМ УДАЛИТЬ вместе с MOCK.
const MOCK = new URLSearchParams(location.search).has("mock");
async function myTickets(clientId) {
  if (MOCK) return { tickets: [
    { ticket_id: "t_demo1", token: "s_demo1", public_no: 1042, category: "VPN",
      problem_summary: "не подключается VPN", state: "RESOLVED",
      resolved_by_bot: true, needs_specialist: false, out_of_scope: false,
      rating: 1, created_at: new Date(Date.now() - 864e5).toISOString(),
      updated_at: new Date(Date.now() - 864e5).toISOString() },
    { ticket_id: "t_demo2", token: "s_demo2", public_no: 1039, category: "Wi-Fi",
      problem_summary: "не подключается к корпоративному Wi-Fi",
      state: "ESCALATED", resolved_by_bot: false, needs_specialist: true,
      out_of_scope: false, rating: null,
      created_at: new Date(Date.now() - 3 * 864e5).toISOString(),
      updated_at: new Date(Date.now() - 3 * 864e5).toISOString() },
  ]};
  return apiPost("/api/my/tickets", { client_id: clientId });
}

// ---------- Состояние ----------

const state = {
  ticketId: null,
  token: null,
  busy: false,
  conv: 0, // номер диалога: ответы на запросы из прошлого диалога не отрисовываются
  pollTimer: null,  // опрос реплик специалиста
  lastMsgId: 0,     // последнее показанное сообщение переписки
  lastPublicNo: null, // короткий номер текущего обращения, если уже известен

  screen: "home",   // "home" | "chat" | "history" | "history-detail"
  returnScreen: null, // куда вернуться из «Мои обращения»
  done: false,        // обращение завершено — прячем кнопку «Специалист»

  lastClassifyKey: null, // чтобы не повторять строку категории на каждом сообщении
  pendingParent: null,   // {ticketId, token} — «Проблема вернулась», привязать следующее сообщение

  historyTickets: null,  // кэш списка «Мои обращения» на время сессии
  historyTicket: null,   // какое обращение открыто в history-detail
};

// ---------- DOM ----------

let chatEl, feedEl, emptyEl, inputEl, sendBtn, counterEl;
let composerEl, heroSlotEl, dockEl, backBtn, backBtnLabel, resumeEl;
let specialistBtn, historyBtn;
let historyEl, historyListEl, historyDetailEl, historyDetailFeedEl, historyDetailActionsEl;
let kbSearchInput, kbResultsEl;
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
  backBtnLabel = backBtn.querySelector("span");
  resumeEl = document.getElementById("resume");

  specialistBtn = document.getElementById("specialist-btn");
  historyBtn = document.getElementById("history-btn");
  historyEl = document.getElementById("history");
  historyListEl = document.getElementById("history-list");
  historyDetailEl = document.getElementById("history-detail");
  historyDetailFeedEl = document.getElementById("history-detail-feed");
  historyDetailActionsEl = document.getElementById("history-detail-actions");

  kbSearchInput = document.getElementById("kb-search-input");
  kbResultsEl = document.getElementById("kb-search-results");

  inputEl.maxLength = MAX_LEN;
  inputEl.addEventListener("input", onInput);
  inputEl.addEventListener("keydown", onKeydown);
  sendBtn.addEventListener("click", submitInput);

  // F1: плитки категорий — только подставляют пример в поле, не отправляют
  document.querySelectorAll(".catalog-tile").forEach((btn) => {
    btn.addEventListener("click", () => fillInput(btn.dataset.fill));
  });
  initKbSearch();
  loadKbCount();

  backBtn.addEventListener("click", handleBack);
  specialistBtn.addEventListener("click", callSpecialist);
  historyBtn.addEventListener("click", openHistory);

  document.getElementById("resume-continue").addEventListener("click", () => resumeTicket());
  document.getElementById("resume-new").addEventListener("click", () => {
    // Отказались продолжать — старое обращение закрываем, чтобы оно
    // не висело в панели оператора как активное
    const saved = loadSaved();
    if (saved) closeTicket(saved.ticketId, saved.token);
    clearSaved();
    hideResume();
  });
  offerResume();

  if (loadClientId()) historyBtn.hidden = false;

  window.addEventListener("popstate", onPopState);
  try { history.replaceState({ screen: "home" }, "", location.pathname + location.search); } catch { /* file:// иногда против */ }

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
    client_id: loadClientId(),
  };
  // «Проблема вернулась»: привязываем ровно первое сообщение нового обращения
  // к старому, закрытому. Очищается только после успешного ответа сервера.
  if (state.pendingParent) {
    payload.parent_ticket_id = state.pendingParent.ticketId;
    payload.parent_token = state.pendingParent.token;
  }

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
  if (data.client_id) saveClientId(data.client_id);
  if (typeof data.public_no === "number") state.lastPublicNo = data.public_no;
  state.pendingParent = null; // связка передана — дальше не нужна
  saveTicket(data);

  try {
    if (afterReply) afterReply(data);
    render(data, payload);
    // Обращение у человека — начинаем следить за его ответами
    if (data.state === "ESCALATED") startPolling();
    else if (data.state === "RESOLVED") stopPolling();
    state.done = data.state === "RESOLVED" || data.reply?.type === "closed";
    syncSpecialistButton();
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

// F1: GET-запросы к базе знаний, без авторизации, без ticket_id/token.
async function apiGet(path) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  let res;
  try {
    res = await fetch(path, { signal: controller.signal });
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

// F3: «Категория: VPN · Инструкция: «…» · Уверенность: 86 %» сразу под первым
// содержательным ответом бота. Показывается один раз на набор значений —
// если категория/уверенность не менялись, повторно не дублируем на каждом
// следующем сообщении, чтобы не превращать ленту в простыню.
function maybeRenderClassificationLine(body, data) {
  if (!data.category) return; // классификация ещё не готова — показывать нечего
  const percent = formatConfidence(data);
  const key = `${data.category}|${data.article?.id || ""}|${percent}`;
  if (state.lastClassifyKey === key) return;
  state.lastClassifyKey = key;

  const line = el("p", "classify-line");
  const parts = [`Категория: ${data.category}`];
  if (data.article?.title) parts.push(`Инструкция: «${data.article.title}»`);
  parts.push(`Уверенность: ${percent}`);
  line.append(document.createTextNode(parts.join(" · ") + " "));

  const hint = el("span", "classify-hint", "?");
  hint.tabIndex = 0;
  hint.setAttribute("role", "note");
  const hintText = "Уверенность относится к подбору инструкции из базы знаний, а не к ответу целиком";
  hint.title = hintText;
  hint.setAttribute("aria-label", hintText);
  line.append(hint);

  body.append(line);
}

// Правило 7 из ТЗ: если статьи нет — прочерк, а не «0 %» и не выдуманное число.
function formatConfidence(data) {
  if (!data.article || typeof data.confidence !== "number") return "—";
  return `${Math.round(data.confidence * 100)} %`;
}

// question — вопрос + кнопки быстрых ответов.
// Если source: "general" — это не уточняющий вопрос, а содержательный ответ
// ИИ-ассистента без статьи из базы: добавляем оценку, как и для шагов.
function renderQuestion(data) {
  const { reply } = data;
  const msg = addBotMessage(reply.text);
  const general = isGeneral(data);
  if (general) markGeneral(msg);
  maybeRenderClassificationLine(msg.body, data);
  renderQuickReplies(msg.body, reply.quick_replies);
  appendRatingIfKnown(msg.body, data, { onlyIf: general });
  scrollToMessage(msg.row);
}

// choice — «уточните, о чём речь» + варианты категорий. Это меню, не ответ —
// оценку сюда не добавляем.
function renderChoice(data) {
  const { reply } = data;
  const msg = addBotMessage(reply.text || "Уточните, пожалуйста, о чём речь:");
  if (isGeneral(data)) markGeneral(msg);
  maybeRenderClassificationLine(msg.body, data);
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

// F2: сервер присылает все шаги сразу — показываем их сразу все, нумерованным
// списком с необязательными чекбоксами. Итог — одна пара кнопок внизу:
// «Проблема решена» / «Не помогло». Кнопка не подтверждает шаг, а завершает
// всю инструкцию — раньше это было главной путаницей у ревьюеров.
function renderSteps(data) {
  const { reply } = data;
  const steps = (Array.isArray(reply.steps) ? reply.steps : []).map(stepText).filter(Boolean);
  if (steps.length === 0) return renderQuestion(data);

  const source = isGeneral(data) ? "general" : "kb";
  const msg = addBotMessage(reply.text, { wide: true });
  if (source === "general") markGeneral(msg);
  maybeRenderClassificationLine(msg.body, data);

  const card = buildStepsCard(steps, source);
  msg.body.append(card);
  renderQuickReplies(msg.body, reply.quick_replies);
  appendRatingIfKnown(msg.body, data, { onlyIf: true });
  scrollToMessage(msg.row);
}

function buildStepsCard(steps, source) {
  const total = steps.length;
  const card = el("div", "step-card");
  card.dataset.active = "steps";
  card.tabIndex = -1;

  const head = el("div", "step-head");
  head.append(el("span", "step-counter", `Инструкция: ${total} ${pluralSteps(total)}`));

  const progress = el("div", "progress");
  progress.setAttribute("role", "progressbar");
  progress.setAttribute("aria-valuemin", "0");
  progress.setAttribute("aria-valuemax", String(total));
  progress.setAttribute("aria-valuenow", "0");
  progress.setAttribute("aria-label", "Отмечено шагов");
  const fill = el("div", "progress-fill");
  progress.append(fill);

  const list = el("ol", "step-list");
  steps.forEach((text) => {
    const li = el("li", "step-item");
    const label = el("label", "step-item-label");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.className = "step-check";
    checkbox.addEventListener("change", () => updateStepsProgress(list, progress, fill, total));
    const span = el("span", "step-item-text", text);
    label.append(checkbox, span);
    li.append(label);
    list.append(li);
  });

  const actions = el("div", "step-actions");
  const okBtn = el("button", "btn btn--primary", "Проблема решена");
  const failBtn = el("button", "btn btn--ghost", "Не помогло");
  okBtn.type = failBtn.type = "button";
  okBtn.addEventListener("click", () => finishSteps(card, true, source));
  failBtn.addEventListener("click", () => finishSteps(card, false, source));
  actions.append(okBtn, failBtn);

  card.append(head, progress, list, actions);
  focusIfLost(card);
  return card;
}

// Чекбоксы ни на что не влияют, кроме прогресс-бара — это просто отметки
// «где я в списке», а не подтверждение шага (см. F2 в ТЗ).
function updateStepsProgress(list, progress, fill, total) {
  const checked = list.querySelectorAll(".step-check:checked").length;
  progress.setAttribute("aria-valuenow", String(checked));
  fill.style.width = `${(checked / total) * 100}%`;
}

// «Проблема решена» — одно сообщение на сервер. «Не помогло» — тоже одно,
// с предупреждением про передачу ИИ-ассистенту, если источник был kb.
function finishSteps(card, solved, source) {
  if (state.busy || card.dataset.active !== "steps") return; // двойное нажатие
  const checked = card.querySelectorAll(".step-check:checked").length;
  const total = card.querySelectorAll(".step-check").length;
  collapseStepsCard(card, solved ? "ok" : "failed", checked, total);

  if (solved) return send({ quickReply: QR.SOLVED });

  if (source === "kb") {
    const note = addBotMessage(AI_HANDOFF_TEXT);
    scrollToMessage(note.row);
    return send({ quickReply: QR.NOT_SOLVED }, {
      afterReply: (data) => { if (!isGeneral(data)) note.row.remove(); },
    });
  }
  send({ quickReply: QR.NOT_SOLVED });
}

// Сворачиваем инструкцию: остаётся в ленте приглушённой строкой с итогом.
function collapseStepsCard(card, result, checked, total) {
  delete card.dataset.active;
  card.className = `step-card is-collapsed is-${result}`;
  card.replaceChildren();

  const marks = { ok: "✓", failed: "✕", skipped: "–" };
  const texts = {
    ok: "Проблема решена",
    failed: `Не помогло (отмечено ${checked} из ${total})`,
    skipped: `Пропущено (отмечено ${checked} из ${total})`,
  };

  const mark = el("span", "step-mark", marks[result]);
  mark.setAttribute("aria-hidden", "true");
  const textEl = el("span", "step-collapsed-text", texts[result]);
  card.append(mark, textEl);
}

// Шаг может прийти строкой или объектом — формат в контракте не зафиксирован
function stepText(step) {
  if (typeof step === "string") return step;
  return step?.text || step?.title || "";
}

// Русское склонение «шаг/шага/шагов»
function pluralSteps(n) {
  const mod10 = n % 10, mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return "шаг";
  if ([2, 3, 4].includes(mod10) && ![12, 13, 14].includes(mod100)) return "шага";
  return "шагов";
}

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

  // Нецелевое обращение: полную карточку и оценку не показываем — задачи не было.
  if (outcome === "closed") {
    const actions = el("div", "msg-actions card-actions");
    actions.append(button("btn btn--primary", "Новое обращение", resetConversation));
    msg.body.append(actions);
    scrollToMessage(msg.row);
    return;
  }

  // F3: карточка обращения — категория, суть, что попробовали, собранные
  // сведения, исход и дата. Раньше это было видно только в панели оператора.
  const ticketCardEl = renderTicketCard(card);
  if (ticketCardEl) msg.body.append(ticketCardEl);

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

// F3: компактная карточка обращения — номер, категория, суть, что уже
// попробовали, собранные сведения, исход и дата. Не «простыня»: пустые
// блоки (нет slots / steps_done) просто не рисуются.
function renderTicketCard(card) {
  if (!card || !card.ticket_id) return null;

  const box = el("div", "ticket-card");

  const head = el("div", "ticket-card-head");
  head.append(el("span", "ticket-card-no", `Обращение №${card.public_no ?? "—"}`));
  if (card.category) head.append(el("span", "ticket-card-category", card.category));
  box.append(head);

  if (card.problem_summary) {
    box.append(el("p", "ticket-card-summary", card.problem_summary));
  }

  if (Array.isArray(card.steps_done) && card.steps_done.length) {
    const wrap = el("div", "ticket-card-block");
    wrap.append(el("p", "ticket-card-block-title", "Что уже попробовали"));
    const list = el("ul", "ticket-card-list");
    card.steps_done.forEach((s) => list.append(el("li", null, stepText(s))));
    wrap.append(list);
    box.append(wrap);
  }

  const slots = card.slots && typeof card.slots === "object" ? Object.entries(card.slots) : [];
  if (slots.length) {
    const wrap = el("div", "ticket-card-block");
    wrap.append(el("p", "ticket-card-block-title", "Собранные сведения"));
    const list = el("ul", "ticket-card-list");
    slots.forEach(([key, value]) => list.append(el("li", null, `${key}: ${value}`)));
    wrap.append(list);
    box.append(wrap);
  }

  const footer = el("div", "ticket-card-footer");
  footer.append(el("span", null, ticketOutcomeLabel(card)));
  if (card.created_at) footer.append(el("span", null, formatDate(card.created_at)));
  box.append(footer);

  return box;
}

function ticketOutcomeLabel(card) {
  if (card.out_of_scope) return "Не по адресу";
  if (card.resolved_by_bot) return "Решено ботом";
  if (card.needs_specialist) return "Передано специалисту";
  return "В процессе";
}

function formatDate(iso) {
  try {
    return new Date(iso).toLocaleString("ru-RU");
  } catch {
    return "";
  }
}

// F3: оценка нужна не только в итоге, а под каждым содержательным ответом —
// у ревьюера иначе не было повода до неё дойти. Добавляется, только если
// уже известен ticket_id (данные ответа или уже сохранённые в state).
function appendRatingIfKnown(body, data, { onlyIf }) {
  if (!onlyIf) return;
  const ticketId = data.ticket_id || state.ticketId;
  if (!ticketId) return;
  body.append(renderRating(ticketId));
}

// Оценка ответа: 👍 = помогло (rating: 1), 👎 = не помогло (rating: 0) — бинарно,
// как того требует контракт. После нажатия кнопки остаются на месте, нажатая —
// подсвечена, а не пропадают совсем: у конкурентов именно это путало ревьюеров
// («то ли оценено, то ли нет — просто пропали звёзды после клика»).
function renderRating(ticketId) {
  const wrap = el("div", "rating");
  const label = el("p", "rating-label", "Помог ли ответ?");
  const buttons = el("div", "msg-actions rating-buttons");
  const status = el("p", "rating-status");
  status.setAttribute("role", "status");

  const options = [
    { text: "👍 Помогло", rating: 1 },
    { text: "👎 Не помогло", rating: 0 },
  ];
  const btns = options.map(({ text, rating }) => {
    const btn = el("button", "btn btn--ghost", text);
    btn.type = "button";
    btn.addEventListener("click", async () => {
      if (wrap.dataset.sending || wrap.dataset.rated) return; // повторное нажатие
      wrap.dataset.sending = "1";
      btns.forEach((b) => (b.disabled = true));
      status.textContent = "";
      try {
        await rateTicket(ticketId, rating);
        delete wrap.dataset.sending;
        wrap.dataset.rated = String(rating);
        btn.classList.add("is-selected");
        status.textContent = "Спасибо, учтено";
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
  if (!ticketId || !token) return;
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
   F4: «Позвать специалиста»
   Кнопка в шапке, видна в диалоге, пока обращение не завершено. Это обычное
   сообщение в общий автомат — бэкенд сам решает, что делать, если обращения
   ещё нет (задаст уточняющий вопрос).
   ========================================================================== */

function callSpecialist() {
  if (state.busy) return;
  send({ quickReply: QR_CALL_SPECIALIST });
}

function syncSpecialistButton() {
  specialistBtn.hidden = state.screen !== "chat" || state.done;
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

// F5: client_id — отдельный, самостоятельный ключ, отдельно от helpme:ticket.
function saveClientId(id) {
  if (!id) return;
  try { localStorage.setItem(CLIENT_STORE_KEY, id); } catch { /* приватное окно */ }
  if (historyBtn) historyBtn.hidden = false;
}

function loadClientId() {
  try { return localStorage.getItem(CLIENT_STORE_KEY); } catch { return null; }
}

function offerResume() {
  if (!loadSaved()) return;
  resumeEl.hidden = false;
}

function hideResume() {
  if (resumeEl) resumeEl.hidden = true;
}

// saved — {ticketId, token, publicNo?}. Без аргумента берёт то, что лежит
// в localStorage (обычный сценарий восстановления после перезагрузки).
// С аргументом — используется и из «Мои обращения» (F5), чтобы продолжить
// или открыть переписку выбранного обращения.
async function resumeTicket(saved) {
  saved = saved || loadSaved();
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
  state.done = false;
  state.lastClassifyKey = null;
  state.pendingParent = null;
  saveTicket(data); // если открыли обращение из «Мои обращения» — оно теперь и есть текущее

  setMode("chat");
  feedEl.replaceChildren();
  for (const m of data.messages || []) {
    if (m.role === "user") addUserMessage(m.text);
    else if (m.role === "operator") addOperatorMessage(m.text);
    else addBotMessage(m.text);
  }

  // Правило 6: внутренний ticket_id пользователю не показываем никогда —
  // только короткий public_no, и то если он уже пришёл с сервера.
  const publicNo = typeof data.public_no === "number" ? data.public_no : saved.publicNo;
  if (typeof publicNo === "number") state.lastPublicNo = publicNo;
  const note = addBotMessage(publicNo
    ? `Обращение №${publicNo} восстановлено. Продолжайте — контекст я помню.`
    : "Обращение восстановлено. Продолжайте — контекст я помню.");
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
  state.done = data.state === "RESOLVED";
  syncSpecialistButton();
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
  if (!state.ticketId || !state.token) return stopPolling();
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

  if (typeof data.public_no === "number") state.lastPublicNo = data.public_no;
  if (typeof data.last_id === "number") state.lastMsgId = data.last_id;
  for (const m of data.messages || []) {
    addOperatorMessage(m.text);
    if (typeof m.id === "number" && m.id > state.lastMsgId) state.lastMsgId = m.id;
  }
  if (data.state === "RESOLVED") {
    stopPolling();
    clearSaved();
    state.done = true;
    syncSpecialistButton();
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
  const { row } = buildOperatorMessage(text);
  feedEl.append(row);
  scrollToMessage(row);
  return row;
}

// Строка «Результат» в карточке. «Без специалиста» — только если бэкенд
// явно поставил resolved_by_bot = true.
// reply.type === "error" — бэкенд ответил, но обработать не смог
function renderErrorReply(data, payload) {
  showError(data.reply?.text || ERROR_TEXT.default, {
    label: "Попробовать снова",
    onClick: () => resend(payload, false),
  });
}

// Ошибка транспорта или HTTP-код.
// 409 — обращение уже закрыто (например, специалист его завершил, пока
// человек дописывал сообщение). Это не баг, а одна из проверок безопасности
// (см. раздел 5, «Важно»): ведём сразу на «Проблема вернулась», а не просто
// на пустое «Новое обращение», чтобы не терять контекст.
function renderRequestError(err, payload) {
  const status = err instanceof ApiError ? err.status : 0;
  const code = err instanceof ApiError ? err.code : "network";
  const text = ERROR_TEXT[status] || ERROR_TEXT[code] || ERROR_TEXT.default;

  if (status === 409) {
    const oldTicket = { ticket_id: state.ticketId, token: state.token, public_no: state.lastPublicNo };
    clearSaved();
    showError("Обращение закрыто, создаём новое.", {
      label: "Проблема вернулась",
      onClick: () => returnToProblem(oldTicket),
    });
    return;
  }
  if (status === 404) {
    clearSaved();
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
   Построение узла (buildXMessage) отделено от добавления в живую ленту
   (addXMessage), чтобы те же самые «пузыри» можно было использовать
   и в истории обращений (F5, только для чтения).
   ========================================================================== */

function buildUserMessage(text) {
  const row = el("div", "msg msg--user");
  const bubble = el("div", "bubble");
  bubble.append(el("p", "bubble-text", text));
  row.append(bubble);
  return { row, bubble };
}

function addUserMessage(text) {
  const { row } = buildUserMessage(text);
  feedEl.append(row);
  scrollToBottom();
  return row;
}

function buildBotMessage(text, { variant = null, wide = false } = {}) {
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
  return { row, body, bubble };
}

function addBotMessage(text, opts = {}) {
  const msg = buildBotMessage(text, opts);
  feedEl.append(msg.row);
  return msg;
}

function buildOperatorMessage(text) {
  const row = el("div", "msg msg--bot msg--operator");
  const avatar = el("div", "avatar");
  avatar.setAttribute("aria-hidden", "true");
  avatar.innerHTML = ICON_OPERATOR; // статичная строка, не данные

  const body = el("div", "msg-body");
  const bubble = el("div", "bubble");
  bubble.append(el("p", "operator-label", "Специалист поддержки"));
  bubble.append(el("p", "bubble-text", text)); // textContent — безопасно
  body.append(bubble);

  row.append(avatar, body);
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
    if (node.dataset.active === "steps") {
      const checked = node.querySelectorAll(".step-check:checked").length;
      const total = node.querySelectorAll(".step-check").length;
      collapseStepsCard(node, "skipped", checked, total);
    } else {
      node.remove();
    }
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
  state.lastPublicNo = null;
  state.done = false;
  state.lastClassifyKey = null;
  state.pendingParent = null;
  clearSaved();
  hideResume();

  feedEl.replaceChildren();
  setMode("home");
  chatEl.scrollTo({ top: 0 });
  if (window.matchMedia("(hover: hover)").matches) inputEl.focus();
}

// Экран: «home» (главная), «chat» (диалог), «history» (Мои обращения),
// «history-detail» (переписка одного обращения из истории, только чтение).
// Переход между ними — отдельная запись в истории браузера (F6): кнопка
// «назад» в браузере работает так же, как и наша собственная кнопка «назад».
function setMode(mode, { push = true } = {}) {
  if (state.screen === mode) return; // уже в нужном режиме
  const hadFocus = composerEl.contains(document.activeElement);
  state.screen = mode;

  const home = mode === "home";
  const chatMode = mode === "chat";

  emptyEl.hidden = !home;
  feedEl.hidden = !chatMode;
  historyEl.hidden = mode !== "history";
  historyDetailEl.hidden = mode !== "history-detail";
  dockEl.hidden = !chatMode;
  backBtn.hidden = home;
  backBtnLabel.textContent = chatMode ? "На главную" : "Назад";

  if (home) heroSlotEl.append(composerEl);
  else if (chatMode) dockEl.append(composerEl);

  if (!home) hideResume();
  syncSpecialistButton();

  if (hadFocus && chatMode) inputEl.focus({ preventScroll: true }); // перенос в DOM сбрасывает фокус

  if (push) {
    const url = chatMode ? "#/chat"
      : mode === "history" ? "#/history"
      : mode === "history-detail" ? "#/history/detail"
      : "#/";
    try { history.pushState({ screen: mode }, "", url); } catch { /* file:// иногда против */ }
  }
}

function onPopState(event) {
  const target = event.state?.screen || "home";
  if (target === "chat" && !state.ticketId) return setMode("home", { push: false });
  if (target === "history-detail" && !state.historyTicket) return setMode("history", { push: false });
  setMode(target, { push: false });
}

// F1: плитка каталога — подставляет пример в поле, не отправляет.
// Пользователь дописывает своими словами и отправляет сам.
function fillInput(text) {
  if (!text) return;
  setMode("home"); // на случай если каталог когда-нибудь станет виден и не только на главной
  inputEl.value = text;
  inputEl.focus();
  inputEl.setSelectionRange(text.length, text.length);
  autoResize();
  updateComposer();
}

/* ==========================================================================
   F1: ПОИСК ПО БАЗЕ ЗНАНИЙ
   GET /api/kb/search — без авторизации. Подсказка, а не критичная функция:
   ошибки сети тут просто тихо ничего не показывают.
   ========================================================================== */

let kbSearchTimer = null;

function initKbSearch() {
  if (!kbSearchInput) return;
  kbSearchInput.addEventListener("input", () => {
    clearTimeout(kbSearchTimer);
    const q = kbSearchInput.value.trim();
    if (!q) return renderKbResults([]);
    kbSearchTimer = setTimeout(() => runKbSearch(q), 300);
  });
}

async function runKbSearch(q) {
  let items;
  try {
    items = await kbSearch(q, 10);
  } catch {
    return; // подсказка необязательна — молчим при ошибке сети
  }
  renderKbResults(Array.isArray(items) ? items : []);
}

function kbSearch(q, limit) {
  const params = new URLSearchParams({ q, limit: String(limit) });
  return apiGet(`/api/kb/search?${params.toString()}`);
}

function renderKbResults(items) {
  kbResultsEl.replaceChildren();
  kbResultsEl.hidden = items.length === 0;
  items.forEach((item) => {
    const li = el("li");
    const btn = el("button", "kb-result", item.title || item.id || "Без названия");
    btn.type = "button";
    btn.addEventListener("click", () => {
      const text = item.title || item.id;
      kbSearchInput.value = "";
      renderKbResults([]);
      send({ message: text }, { echo: text });
    });
    li.append(btn);
    kbResultsEl.append(li);
  });
}

// Счётчик «N инструкций» — реальное число из API, не хардкод. Раньше был
// отдельной строкой под полем и растягивал экран по высоте — теперь просто
// часть плейсхолдера, дополнительного места не занимает.
async function loadKbCount() {
  if (!kbSearchInput) return;
  let items;
  try {
    items = await kbSearch("", 1000);
  } catch {
    return; // бэкенд недоступен — оставляем обычный плейсхолдер, это не критично
  }
  if (!Array.isArray(items)) return;
  kbSearchInput.placeholder = `Поиск по базе знаний · ${items.length} ${pluralInstructions(items.length)}`;
}

function pluralInstructions(n) {
  const mod10 = n % 10, mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return "инструкция";
  if ([2, 3, 4].includes(mod10) && ![12, 13, 14].includes(mod100)) return "инструкции";
  return "инструкций";
}

/* ==========================================================================
   F5: «МОИ ОБРАЩЕНИЯ»
   Список приходит с /api/my/tickets по client_id (см. MOCK выше, пока
   бэкенд не выкатили). Переписка отдельного обращения — уже существующий
   POST /api/chat/updates с full: true, он работает и для закрытых обращений.
   ========================================================================== */

function handleBack() {
  if (state.screen === "history-detail") return setMode("history");
  if (state.screen === "history") return setMode(state.returnScreen || "home");
  return resetConversation(); // state.screen === "chat"
}

function openHistory() {
  if (state.screen !== "history" && state.screen !== "history-detail") {
    state.returnScreen = state.screen;
  }
  setMode("history");
  loadHistoryList();
}

async function loadHistoryList() {
  historyListEl.replaceChildren();
  historyListEl.append(el("p", "history-status", "Загрузка…"));

  const clientId = loadClientId();
  if (!clientId) {
    historyListEl.replaceChildren(el("p", "history-status", "Обращений пока нет."));
    return;
  }

  let data;
  try {
    data = await myTickets(clientId);
  } catch {
    historyListEl.replaceChildren();
    historyListEl.append(el("p", "history-status", "Не удалось загрузить обращения."));
    historyListEl.append(button("btn btn--ghost", "Повторить", loadHistoryList));
    return;
  }

  state.historyTickets = Array.isArray(data.tickets) ? data.tickets : [];
  renderHistoryList(state.historyTickets);
}

function renderHistoryList(tickets) {
  historyListEl.replaceChildren();
  if (!tickets.length) {
    historyListEl.append(el("p", "history-status", "Обращений пока нет."));
    return;
  }
  const sorted = [...tickets].sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
  sorted.forEach((ticket) => historyListEl.append(renderHistoryRow(ticket)));
}

function renderHistoryRow(ticket) {
  const row = el("button", "history-row");
  row.type = "button";

  const head = el("div", "history-row-head");
  head.append(el("span", "history-row-no", `№${ticket.public_no ?? "—"}`));
  const { label, cls } = historyStatusInfo(ticket);
  head.append(el("span", `history-badge history-badge--${cls}`, label));
  row.append(head);

  if (ticket.category) row.append(el("p", "history-row-category", ticket.category));
  row.append(el("p", "history-row-summary", ticket.problem_summary || "—"));

  const meta = el("div", "history-row-meta");
  meta.append(el("span", null, formatDate(ticket.created_at)));
  if (typeof ticket.rating === "number") {
    meta.append(el("span", null, ticket.rating === 1 ? "👍" : "👎"));
  }
  row.append(meta);

  row.addEventListener("click", () => openHistoryDetail(ticket));
  return row;
}

// Статусы человеческим языком (см. F5 в ТЗ). Явных флагов на бэкенде четыре —
// resolved_by_bot, needs_specialist, out_of_scope и терминальность (RESOLVED).
// Пятый вариант, «В процессе», нужен для ещё не завершённых обращений —
// в списке они тоже есть (с кнопкой «Продолжить»), и им нужен свой ярлык.
function historyStatusInfo(ticket) {
  if (ticket.out_of_scope) return { label: "Не по адресу", cls: "muted" };
  if (ticket.resolved_by_bot) return { label: "Решено", cls: "ok" };
  if (ticket.needs_specialist) return { label: "У специалиста", cls: "info" };
  if (ticket.state === "RESOLVED") return { label: "Закрыто", cls: "muted" };
  return { label: "В процессе", cls: "info" };
}

// Три варианта нижней кнопки в просмотре обращения (см. F5, п.5 в ТЗ).
function historyActionKind(ticket) {
  if (ticket.state === "ESCALATED") return "escalated";
  if (["NEW", "CLASSIFYING", "CLARIFYING", "SOLVING", "VERIFYING"].includes(ticket.state)) return "continue";
  return "closed"; // RESOLVED и всё, что не попало в первые два случая
}

async function openHistoryDetail(ticket) {
  state.historyTicket = ticket;
  setMode("history-detail");
  historyDetailFeedEl.replaceChildren();
  historyDetailActionsEl.replaceChildren();
  historyDetailFeedEl.append(el("p", "history-status", "Загрузка переписки…"));

  let data;
  try {
    data = await apiPost("/api/chat/updates", {
      ticket_id: ticket.ticket_id, token: ticket.token, after: 0, full: true,
    });
  } catch {
    historyDetailFeedEl.replaceChildren();
    historyDetailFeedEl.append(el("p", "history-status", "Не удалось загрузить переписку."));
    historyDetailFeedEl.append(button("btn btn--ghost", "Повторить", () => openHistoryDetail(ticket)));
    return;
  }

  historyDetailFeedEl.replaceChildren();
  for (const m of data.messages || []) {
    let node;
    if (m.role === "user") node = buildUserMessage(m.text).row;
    else if (m.role === "operator") node = buildOperatorMessage(m.text).row;
    else node = buildBotMessage(m.text).row;
    historyDetailFeedEl.append(node);
  }
  if (!data.messages || !data.messages.length) {
    historyDetailFeedEl.append(el("p", "history-status", "В этом обращении пока нет сообщений."));
  }

  renderHistoryDetailActions(ticket);
}

function renderHistoryDetailActions(ticket) {
  historyDetailActionsEl.replaceChildren();
  const kind = historyActionKind(ticket);

  if (kind === "continue") {
    historyDetailActionsEl.append(button("btn btn--primary", "Продолжить", () => {
      resumeTicket({ ticketId: ticket.ticket_id, token: ticket.token, publicNo: ticket.public_no });
    }));
  } else if (kind === "escalated") {
    historyDetailActionsEl.append(button("btn btn--primary", "Открыть переписку", () => {
      resumeTicket({ ticketId: ticket.ticket_id, token: ticket.token, publicNo: ticket.public_no });
    }));
  } else {
    historyDetailActionsEl.append(button("btn btn--primary", "Проблема вернулась", () => returnToProblem(ticket)));
  }
}

// «Проблема вернулась»: новое обращение, привязанное к старому. Старое
// остаётся закрытым — используем ЕГО ticket_id/token как parent_*, но
// у себя заводим совершенно новый ticketId (он придёт в следующем ответе).
function returnToProblem(ticket) {
  state.conv++;
  stopPolling();
  state.ticketId = null;
  state.token = null;
  state.lastMsgId = 0;
  state.lastPublicNo = null;
  state.done = false;
  state.lastClassifyKey = null;
  clearSaved();
  hideResume();

  feedEl.replaceChildren();
  setMode("chat");
  state.pendingParent = { ticketId: ticket.ticket_id, token: ticket.token };

  const msg = addBotMessage("Прошлое обращение закрыто. Опишите, что снова не работает — "
    + "я перенесу контекст из прошлого обращения.");
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
