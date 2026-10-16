/* Страница обхода: ход без перезагрузки и дерево файлов с итогом по каждому.
   Раскрытые папки и выбранный вид не сбрасываются при обновлении. */
(function () {
  "use strict";
  const box = document.getElementById("sc");
  if (!box) return;
  const run = box.dataset.run;
  let finished = !!box.dataset.finished;
  const tree = document.getElementById("ftree");
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const num = (n) => Number(n || 0).toLocaleString("ru-RU");
  const get = (u) => fetch(u, { credentials: "same-origin" }).then((r) => r.json());
  const open = new Set();          // ключи раскрытых папок: "корень|путь"
  let only = "";                   // выбранный вид («только ошибки» и т. п.)
  const LABEL = { accepted: "считано", unchanged: "без изменений", same: "как в актуальной", copy: "копии",
    ignored: "проигнорировано", error: "ошибки", pending: "ждут просмотра" };

  const counts = (c) => Object.keys(LABEL).filter((k) => c[k]).map((k) =>
    `<span class="cnt st-${k}" title="${LABEL[k]}">${num(c[k])}</span>`).join("");
  const total = (c) => Object.values(c).reduce((a, b) => a + b, 0);

  function render(ul, data) {
    const dirs = data.dirs.filter((d) => !only || d.counts[only]);
    const files = data.files.filter((f) => !only || f.status === only);
    ul.innerHTML = dirs.map((d) => {
      const key = d.root + "|" + d.dir;
      return `<li role="treeitem" aria-expanded="${open.has(key)}" data-key="${esc(key)}" data-root="${d.root}" data-dir="${esc(d.dir)}">
        <div class="ft-row"><button type="button" class="tree-tog" aria-label="Раскрыть">${open.has(key) ? "▾" : "▸"}</button>
          <span class="ft-name">${esc(d.name)}</span><span class="ft-cnts">${counts(d.counts)}</span></div>
        <ul role="group" ${open.has(key) ? "" : "hidden"}></ul></li>`;
    }).join("") + files.map((f) => `<li class="ft-file" role="treeitem">
        <div class="ft-row"><span class="tree-tog"></span>
          <span class="ft-name">${f.inventory && (f.status === "accepted" || f.status === "same") ? `<a href="/inventories/${f.inventory}/">${esc(f.name)}</a>` : esc(f.name)}</span>
          <span class="tag st-${f.status}">${esc(f.label)}</span></div>
        ${f.note ? `<div class="ft-note muted">${esc(f.note)}</div>` : ""}</li>`).join("");
    if (!ul.children.length && ul !== tree) ul.innerHTML = `<li class="muted ft-empty">нет файлов этого вида</li>`;
    // раскрытые — подгрузить снова
    ul.querySelectorAll(":scope > li[aria-expanded=true]").forEach((li) => load(li.querySelector(":scope > ul"), li.dataset.root, li.dataset.dir));
  }
  function load(ul, root, dir) {
    const q = root === undefined ? "" : `?root=${root}&dir=${encodeURIComponent(dir)}`;
    return get(`/scans/${run}/tree/${q}`).then((d) => {
      render(ul, d);
      if (ul === tree) document.getElementById("ftree-empty").hidden = !!(d.dirs.length || d.files.length);
    });
  }
  tree.addEventListener("click", (e) => {
    const b = e.target.closest(".tree-tog");
    if (!b || b.tagName !== "BUTTON") return;
    const li = b.closest("li"), ul = li.querySelector(":scope > ul"), key = li.dataset.key;
    const on = ul.hidden;
    ul.hidden = !on; b.textContent = on ? "▾" : "▸"; li.setAttribute("aria-expanded", on);
    if (on) { open.add(key); ul.innerHTML = '<li class="muted ft-empty">загрузка…</li>'; load(ul, li.dataset.root, li.dataset.dir); }
    else open.delete(key);
  });
  box.querySelectorAll(".lg[data-st]").forEach((btn) => btn.addEventListener("click", () => {
    only = btn.dataset.st;
    box.querySelectorAll(".lg").forEach((x) => x.classList.toggle("on", x === btn));
    load(tree);
  }));

  // ход обхода: числа — каждые 2 с, дерево — каждые 6 с, пока обход идёт
  let tick = 0;
  function poll() {
    get(`/scans/${run}/progress/`).then((p) => {
      box.querySelectorAll("[data-k]").forEach((el) => { el.textContent = num(p[el.dataset.k]); });
      document.getElementById("sc-stage").textContent = p.finished ? p.state_label : (p.stage || "в очереди");
      document.getElementById("sc-current").textContent = p.finished ? "" : (p.current || "");
      const bar = document.getElementById("sc-bar");
      bar.hidden = p.finished || !p.to_parse;
      bar.firstElementChild.style.width = (p.to_parse ? Math.round(p.parsed / p.to_parse * 100) : 0) + "%";
      if (p.finished) {
        document.getElementById("sc-title").textContent = "Обход закончен";
        if (!finished) { finished = true; load(tree); setTimeout(() => location.reload(), 800); }
        return;
      }
      if (++tick % 3 === 0) load(tree);
      setTimeout(poll, 2000);
    }).catch(() => setTimeout(poll, 4000));
  }
  load(tree);
  if (!finished) poll();
})();
