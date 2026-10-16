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

})();
