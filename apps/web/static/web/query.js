// Конструктор запросов: разрезы и условия добавляются и снимаются на месте; изменённые
// условия притеняют прежний результат до «Показать» (практика APCS ui-filter-mode).
(function () {
  const form = document.getElementById('qb-form');
  if (!form) return;
  const groups = document.getElementById('qb-groups');
  const addGroup = document.getElementById('qb-add-group');
  const addCond = document.getElementById('qb-add-cond');
  const result = document.getElementById('qb-result');
  const stale = result && result.querySelector('.qb-stale');
  const labelOf = (key) => [...addGroup.options].find((o) => o.value === key).textContent;

  function changed() {
    if (!stale) return;
    result.classList.add('is-stale');
    stale.hidden = false;
  }

  function renumber() {
    const pills = [...groups.querySelectorAll('.qb-pill')];
    pills.forEach((li, i) => {
      li.querySelector('.n').textContent = i + 1;
      li.querySelector('input').name = 'g' + (i + 1);
    });
    const used = new Set(pills.map((li) => li.dataset.key));
    [...addGroup.options].forEach((o) => { if (o.value) o.disabled = used.has(o.value); });
    addGroup.disabled = pills.length >= 3;
    changed();
  }

  addGroup.addEventListener('change', () => {
    const key = addGroup.value;
    addGroup.value = '';
    if (!key) return;
    const li = document.createElement('li');
    li.className = 'qb-pill';
    li.dataset.key = key;
    li.innerHTML = '<span class="n"></span><span class="t"></span><input type="hidden">'
      + '<button type="button" class="x" data-qb-ungroup aria-label="Убрать разрез">×</button>';
    li.querySelector('.t').textContent = labelOf(key);
    li.querySelector('input').value = key;
    groups.appendChild(li);
    renumber();
  });
  groups.addEventListener('click', (e) => {
    const x = e.target.closest('[data-qb-ungroup]');
    if (!x) return;
    x.closest('.qb-pill').remove();
    renumber();
    addGroup.focus();
  });

  const condBox = (key) => form.querySelector('.qb-cond[data-cond="' + key + '"]');
  function syncCondMenu() {
    [...addCond.options].forEach((o) => { if (o.value) o.disabled = !condBox(o.value).hidden; });
  }
  addCond.addEventListener('change', () => {
    const box = condBox(addCond.value);
    addCond.value = '';
    if (!box) return;
    box.hidden = false;
    syncCondMenu();
    const first = box.querySelector('input:not([type=hidden]), select');
    if (first) first.focus();
    changed();
  });
  form.addEventListener('click', (e) => {
    const x = e.target.closest('[data-qb-uncond]');
    if (!x) return;
    const box = x.closest('.qb-cond');
    box.querySelectorAll('input, select').forEach((el) => {
      if (el.tagName === 'SELECT') el.selectedIndex = 0;
      else if (el.type === 'checkbox') el.checked = false;
      else el.value = '';
    });
    box.querySelectorAll('select.qb-op').forEach((el) => el.classList.remove('is-not'));
    box.hidden = true;
    syncCondMenu();
    addCond.focus();
    changed();
  });
  // В адрес — только заданное: скрытые и пустые условия не уходят, все столбцы — по умолчанию.
  // Так короче ссылка и сохранённый запрос.
  form.addEventListener('submit', () => {
    form.querySelectorAll('.qb-cond[hidden] input, .qb-cond[hidden] select').forEach((el) => { el.disabled = true; });
    form.querySelectorAll('.qb-cond input, .qb-cond select').forEach((el) => { if (!el.value) el.disabled = true; });
    // Условие без значения — без операции: «исключает» пустого не уходит в адрес.
    form.querySelectorAll('.qb-cond').forEach((box) => {
      const vals = [...box.querySelectorAll('input, select')].filter((el) => !el.classList.contains('qb-op')
        && !el.hasAttribute('data-qb-filter') && (el.type !== 'hidden' || el.closest('.np')));
      const has = vals.some((el) => !el.disabled && (el.type === 'checkbox' ? el.checked : el.value));
      if (!has) box.querySelectorAll('select.qb-op').forEach((el) => { el.disabled = true; });
      box.querySelectorAll('[data-qb-filter]').forEach((el) => { el.disabled = true; });
    });
    const m = [...form.querySelectorAll('input[name=m]')];
    if (m.every((el) => el.checked)) m.forEach((el) => { el.disabled = true; });
  });
  // «Назад» в браузере возвращает страницу из кэша с отключёнными полями — включить.
  window.addEventListener('pageshow', () => { form.querySelectorAll(':disabled').forEach((el) => { if (el !== addGroup) el.disabled = false; }); renumberMenus(); });
  function renumberMenus() {
    const used = new Set([...groups.querySelectorAll('.qb-pill')].map((li) => li.dataset.key));
    [...addGroup.options].forEach((o) => { if (o.value) o.disabled = used.has(o.value); });
    addGroup.disabled = used.size >= 3;
    syncCondMenu();
  }
  // Строка результата целиком — щелчок открывает её сводку в окне «qb-row» (одно окно на все
  // сводки: следующая строка открывается в том же окне); выделение текста щелчком не считается.
  document.querySelectorAll('tr.qb-row').forEach((tr) => {
    tr.addEventListener('click', (e) => {
      if (e.target.closest('a') || String(window.getSelection())) return;
      window.open(tr.dataset.href, 'qb-row');
    });
  });
  // Поиск по списку типов: строки, где нет набранного, прячутся (отмеченные — всегда видны).
  form.querySelectorAll('[data-qb-filter]').forEach((inp) => inp.addEventListener('input', () => {
    const q = inp.value.trim().toLowerCase();
    inp.parentElement.querySelectorAll('.qb-check-list label').forEach((l) => {
      l.hidden = !!q && !l.textContent.toLowerCase().includes(q) && !l.querySelector('input').checked;
    });
  }));
  form.querySelectorAll('select.qb-op').forEach((el) => el.addEventListener('change', () => {
    el.classList.toggle('is-not', el.value === 'not');
  }));
  form.addEventListener('input', changed);
  form.addEventListener('change', (e) => { if (e.target !== addGroup && e.target !== addCond) changed(); });
  syncCondMenu();
})();
