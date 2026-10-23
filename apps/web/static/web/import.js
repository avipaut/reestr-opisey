/* Импорт: обзор папок сервера — выбор папки, которую приложение прочитает на месте. */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const num = (n) => Number(n || 0).toLocaleString("ru-RU");

  /* ---- обзор папок сервера */
  const path = $("srv-path"), fs = $("srv-fs"), list = $("fs-list"), crumbs = $("fs-crumbs"), msg = $("fs-msg");
  let cur = "";
  const show = (p) => fetch("/import/fs/?path=" + encodeURIComponent(p || ""), { credentials: "same-origin" })
    .then((r) => r.json().then((d) => ({ ok: r.ok, d })))
    .then(({ ok, d }) => {
      fs.hidden = false; $("srv-browse").setAttribute("aria-expanded", "true");
      if (!ok) { msg.textContent = d.error || "Папка недоступна"; return; }
      cur = d.path;
      if (cur) path.value = cur;
      crumbs.innerHTML = `<button type="button" data-p="">Диски</button>` +
        (cur ? ` › <span>${esc(cur)}</span>` : "") +
        (d.parent !== null && cur ? ` <button type="button" data-p="${esc(d.parent)}">↑ выше</button>` : "");
      list.innerHTML = d.dirs.map((x) => `<li><button type="button" data-p="${esc(x.path)}">${esc(x.name)}</button></li>`).join("") +
        (!cur && d.recent && d.recent.length ? `<li class="muted fs-sep">Недавние</li>` + d.recent.map((r) => `<li><button type="button" data-p="${esc(r)}">${esc(r)}</button></li>`).join("") : "");
      $("fs-take").hidden = !cur;
      msg.textContent = cur ? (d.books ? `Книг Excel прямо в этой папке: ${num(d.books)}; подпапок: ${num(d.dirs.length)}` :
        `Подпапок: ${num(d.dirs.length)}; книги — в подпапках`) : "Выберите диск или введите сетевой путь (\\\\сервер\\папка) в поле выше.";
    })
    .catch((e) => { fs.hidden = false; msg.textContent = "Не загрузилось: " + e.message; });
  if (path) {
    $("srv-browse").addEventListener("click", () => fs.hidden ? show(path.value.trim()) : (fs.hidden = true, $("srv-browse").setAttribute("aria-expanded", "false")));
    fs.addEventListener("click", (e) => { const b = e.target.closest("[data-p]"); if (b) show(b.dataset.p); });
    $("fs-take").addEventListener("click", () => { path.value = cur; fs.hidden = true; path.focus(); });
    document.querySelectorAll(".chip[data-path]").forEach((c) => c.addEventListener("click", () => { path.value = c.dataset.path; path.focus(); }));
    path.addEventListener("keydown", (e) => { if (e.key === "ArrowDown") { show(path.value.trim()); e.preventDefault(); } });
  }

  /* ---- с компьютера: браузер не даёт пути папки на диске, только файлы — книги
     загружаются пачками, сервер разбирает их обходом и стирает */
  const up = $("up");
  if (!up) return;
  const exts = up.dataset.exts.toLowerCase().split(";").filter(Boolean);
  const processed = up.dataset.processed.toLowerCase().split(";").map((s) => s.trim()).filter(Boolean);
  const drop = $("up-drop"), plan = $("up-plan"), sum = $("up-sum"), tops = $("up-tops"), bar = $("up-bar"), umsg = $("up-msg");
  const rootBox = $("up-root"), rootL = $("up-root-l"), rootT = $("up-root-t"), go = $("up-go"), cancel = $("up-cancel");
  const CODE = /^\s*\d{1,6}(?:[.)\-–—]|\s|$)/;  // «126», «8. Ошское ДНВТ» — папка учреждения или подведа
  const BATCH_FILES = 20, BATCH_BYTES = 30 * 1048576;
  let books = [], skipped = 0, top = "", busy = false, stop = false;

  const csrf = () => (document.querySelector("[name=csrfmiddlewaretoken]") || {}).value || "";
  const isBook = (name) => !name.startsWith("~$") && exts.some((e) => name.toLowerCase().endsWith(e));
  // из папок — только книги в «обработанных», как при чтении папки; отдельные книги — все
  const inProcessed = (parts) => !processed.length || parts.slice(0, -1).some((p) => processed.includes(p.trim().toLowerCase()));
  const rel = (b) => (top && rootBox.checked ? b.parts.slice(1) : b.parts).join("/");
  const post = (url, body) => fetch(url, { method: "POST", body, credentials: "same-origin", headers: { "X-CSRFToken": csrf() } })
    .then((r) => r.json().catch(() => ({})).then((d) => {
      if (!r.ok || d.error) throw new Error(d.error || "сервер ответил " + r.status);
      return d;
    }));

  // перетащенное: [{path, file | entry}] — файл у записи папки берётся только для книг
  const readAll = (reader) => new Promise((res, rej) => {
    const out = [];
    const next = () => reader.readEntries((es) => { if (!es.length) return res(out); out.push(...es); next(); }, rej);
    next();
  });
  async function walk(entry, out) {
    if (entry.isFile) out.push({ entry, path: entry.fullPath.replace(/^\/+/, "") });
    else if (entry.isDirectory) for (const e of await readAll(entry.createReader())) await walk(e, out);
  }
  const fileOf = (x) => x.file ? Promise.resolve(x.file) : new Promise((res, rej) => x.entry.file(res, rej));

  async function take(list) {
    books = []; skipped = 0;
    const keep = [];
    for (const x of list) {
      const parts = x.path.split("/").filter(Boolean);
      if (!parts.length || !isBook(parts[parts.length - 1])) continue;
      if (parts.length > 1 && !inProcessed(parts)) { skipped++; continue; }
      keep.push({ x, parts });
    }
    for (const k of keep) books.push({ file: await fileOf(k.x), parts: k.parts });
    const firsts = new Set(books.map((b) => (b.parts.length > 1 ? b.parts[0] : "")));
    top = firsts.size === 1 && !firsts.has("") ? [...firsts][0] : "";
    rootL.hidden = !top;
    if (top) {
      // одна общая папка без кода («Нормализованные описи») — корень, как при выборе папки на сервере
      rootBox.checked = !CODE.test(top);
      rootT.textContent = `«${top}» — общая папка, учреждения — её подпапки`;
    }
    bar.hidden = true; umsg.textContent = ""; go.disabled = !books.length;
    showPlan();
  }
  function showPlan() {
    const bytes = books.reduce((a, b) => a + b.file.size, 0);
    const size = bytes < 1048576 ? `${num(Math.ceil(bytes / 1024))} КБ`
      : `${(bytes / 1048576).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} МБ`;
    sum.innerHTML = books.length
      ? `Книг к загрузке: <b>${num(books.length)}</b> (${size})` +
        (skipped ? ` · вне «обработанных» пропущено: ${num(skipped)}` : "")
      : "Книг Excel для загрузки нет" + (skipped ? ` — ${num(skipped)} вне «обработанных»` : "");
    const level = new Map();
    let loose = 0;
    books.forEach((b) => {
      const p = rel(b).split("/");
      if (p.length > 1) level.set(p[0], (level.get(p[0]) || 0) + 1); else loose++;
    });
    const names = [...level.keys()].sort((a, b) => a.localeCompare(b, "ru", { numeric: true }));
    tops.textContent = (names.length ? `Учреждения (${num(names.length)}): ${names.slice(0, 20).join(", ")}` +
      (names.length > 20 ? ` и ещё ${num(names.length - 20)}` : "") : "") +
      (loose ? (names.length ? "; " : "") + `книг без папки: ${num(loose)} — узел укажете при просмотре` : "");
    plan.hidden = false;
  }
  async function gather(list) {
    if (busy) return;
    plan.hidden = false; sum.textContent = "Читаю перетащенное…"; tops.textContent = ""; rootL.hidden = true; go.disabled = true;
    try { await take(await list()); } catch (e) { sum.textContent = "Не прочиталось: " + e.message; }
  }

  // файлы, брошенные мимо поля, браузер открыл бы вместо страницы
  const hasFiles = (e) => e.dataTransfer && [...e.dataTransfer.types].includes("Files");
  window.addEventListener("dragover", (e) => { if (hasFiles(e)) e.preventDefault(); });
  window.addEventListener("drop", (e) => { if (hasFiles(e)) e.preventDefault(); });
  drop.addEventListener("dragover", (e) => { if (hasFiles(e)) { e.preventDefault(); drop.classList.add("over"); } });
  drop.addEventListener("dragleave", (e) => { if (!drop.contains(e.relatedTarget)) drop.classList.remove("over"); });
  drop.addEventListener("drop", (e) => {
    e.preventDefault(); drop.classList.remove("over");
    // записи берутся сразу: после события список перетащенного пуст
    const entries = [...(e.dataTransfer.items || [])].map((i) => i.webkitGetAsEntry && i.webkitGetAsEntry()).filter(Boolean);
    const files = [...e.dataTransfer.files];
    gather(async () => {
      if (!entries.length) return files.map((f) => ({ file: f, path: f.name }));
      const out = [];
      for (const en of entries) await walk(en, out);
      return out;
    });
  });
  $("up-pick-dir").addEventListener("click", () => $("up-dir").click());
  $("up-pick-files").addEventListener("click", () => $("up-files").click());
  $("up-dir").addEventListener("change", (e) => {
    const fl = [...e.target.files]; e.target.value = "";
    gather(async () => fl.map((f) => ({ file: f, path: f.webkitRelativePath || f.name })));
  });
  $("up-files").addEventListener("change", (e) => {
    const fl = [...e.target.files]; e.target.value = "";
    gather(async () => fl.map((f) => ({ file: f, path: f.name })));
  });
  rootBox.addEventListener("change", showPlan);

  go.addEventListener("click", async () => {
    if (busy || !books.length) return;
    busy = true; stop = false; go.disabled = rootBox.disabled = true; cancel.textContent = "Остановить";
    bar.hidden = false;
    const fill = bar.firstElementChild, total = books.reduce((a, b) => a + b.file.size, 0) || 1;
    let i = 0, sent = 0, refused = 0;
    try {
      while (i < books.length) {
        if (stop) throw new Error("Загрузка остановлена — ничего не разобрано; загруженное сотрётся при следующей загрузке.");
        const fd = new FormData();
        if (i === 0) fd.append("first", "1");
        for (let n = 0, size = 0; i < books.length && n < BATCH_FILES && (!n || size + books[i].file.size <= BATCH_BYTES); n++, i++) {
          fd.append("files", books[i].file, books[i].file.name);
          fd.append("paths", rel(books[i]));
          size += books[i].file.size; sent += books[i].file.size;
        }
        refused += ((await post("/import/upload/", fd)).skipped || []).length;
        fill.style.width = Math.round(sent / total * 100) + "%";
        umsg.textContent = `Загружено ${num(i)} из ${num(books.length)} книг` + (refused ? `; не приняты сервером: ${num(refused)}` : "");
      }
      umsg.textContent = "Загружено. Запускаю разбор…";
      const url = (await post("/import/upload/start/", new FormData())).url;
      busy = false;  // уход на страницу обхода — без вопроса «покинуть страницу?»
      location.href = url;
    } catch (e) {
      umsg.textContent = e.message;
      busy = false; go.disabled = rootBox.disabled = false; cancel.textContent = "Отмена";
    }
  });
  cancel.addEventListener("click", () => {
    if (busy) { stop = true; return; }
    books = []; plan.hidden = true;
  });
  window.addEventListener("beforeunload", (e) => { if (busy && !stop) e.preventDefault(); });
})();
