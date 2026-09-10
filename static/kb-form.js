/* ==========================================================================
   Форма добавления статьи в базу знаний — вкладка «База знаний».
   Вставить содержимое этого файла В КОНЕЦ admin.js (лид сказал, что туда
   больше не полезет). Разметка формы — в kb-form.html, внутри
   <div id="kb-form-slot"></div>.

   Ничего глобального не трогает и не переопределяет: если #kb-form-slot
   на странице нет (например, открыта другая вкладка при первой отрисовке),
   код просто ничего не делает.
   ========================================================================== */

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
      if (list.children.length === 0) addRow(list, list.id === "kb-symptoms" ? "symptoms" : "steps");
    }
  });

  form.addEventListener("submit", onSubmit);

  function addRow(list, kind, placeholder = "") {
    const row = document.createElement("div");
    row.className = "kb-row";
    row.innerHTML = `
      <input type="text" data-kind="${kind}" placeholder="${placeholder}">
      <button type="button" class="kb-remove" data-remove aria-label="Удалить">✕</button>
    `;
    list.append(row);
  }

  function collect(list) {
    return [...list.querySelectorAll("input")]
      .map((i) => i.value.trim())
      .filter(Boolean);
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

    // Базовая проверка на клиенте — дублирует контракт, но не заменяет 422 с сервера
    if (!category) return showError("Выберите категорию.");
    if (!title) return showError("Укажите заголовок статьи.");
    if (symptoms.length === 0) return showError("Добавьте хотя бы один симптом.");

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

      if (!res.ok) {
        let detail = `Ошибка ${res.status}`;
        try {
          const body = await res.json();
          if (body?.detail) detail = body.detail;
        } catch {
          /* тело не JSON — оставляем сообщение по умолчанию */
        }
        return showError(detail);
      }

      form.reset();
      symptomsList.replaceChildren();
      stepsList.replaceChildren();
      addRow(symptomsList, "symptoms");
      addRow(stepsList, "steps");
      showSuccess("Статья добавлена и уже доступна в поиске.");

      // Список пробелов и поиск ведёт admin.js — переиспользуем их, если функции есть
      if (typeof window.loadGaps === "function") window.loadGaps();
      if (typeof window.searchKB === "function") window.searchKB();
    } catch {
      showError("Не удалось связаться с сервером. Проверьте соединение и повторите.");
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = "Добавить статью";
    }
  }

  function showError(text) {
    errorEl.textContent = text;
    show(errorEl);
  }
  function showSuccess(text) {
    successEl.textContent = text;
    show(successEl);
  }
  function show(el) { el.hidden = false; }
  function hide(el) { el.hidden = true; el.textContent = ""; }
})();
