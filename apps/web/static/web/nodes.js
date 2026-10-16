/* Выбор узла и дерево учреждений.
   Практики: поиск по мере ввода с полным путём предков у каждого результата
   (иначе одинаковые имена не различить), обзор по уровням с хлебными
   крошками, «Не найдено» с подсказкой; дерево — раскрытие по уровням с
   подгрузкой детей, видно, где ты, стрелки и Enter (WAI-ARIA tree/combobox). */
(function () {
  "use strict";
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const num = (n) => Number(n || 0).toLocaleString("ru-RU");
  const get = (url) => fetch(url, { headers: { Accept: "application/json" }, credentials: "same-origin" })
    .then((r) => { if (!r.ok) throw new Error("сервер ответил " + r.status); return r.json(); });

  /* ---------------- выбор узла: <div class="np" data-name data-value data-label [data-submit]> */
  function picker(root) {
    const name = root.dataset.name || "node";
    root.innerHTML = `
      <input type="hidden" name="${esc(name)}" value="${esc(root.dataset.value || "")}">
      <div class="np-field">
        <input type="text" class="np-input" role="combobox" aria-expanded="false" aria-autocomplete="list"
          placeholder="${esc(root.dataset.placeholder || "код или часть названия")}" value="${esc(root.dataset.label || "")}">
        <button type="button" class="np-clear" aria-label="Очистить" ${root.dataset.value ? "" : "hidden"}>×</button>
        <button type="button" class="btn np-browse" aria-expanded="false">Обзор</button>
      </div>
      <div class="np-pop" hidden><div class="np-crumbs"></div><ul class="np-list" role="listbox"></ul><div class="np-msg muted"></div></div>`;
    const hidden = root.querySelector("input[type=hidden]");
    const input = root.querySelector(".np-input");
    const clear = root.querySelector(".np-clear");
    const pop = root.querySelector(".np-pop");
    const list = root.querySelector(".np-list");
    const crumbs = root.querySelector(".np-crumbs");
    const msg = root.querySelector(".np-msg");
    const uid = "np" + Math.random().toString(36).slice(2, 8);
    list.id = uid;
    input.setAttribute("aria-controls", uid);
    let items = [], active = -1, timer = 0, mode = "";

    const open = (on) => { pop.hidden = !on; input.setAttribute("aria-expanded", on ? "true" : "false"); if (!on) mode = ""; };
    const choose = (it) => {
      hidden.value = it ? it.id : "";
      input.value = it ? it.trail : "";
      clear.hidden = !it;
      open(false);
      if (root.dataset.submit !== undefined) root.closest("form").requestSubmit();
    };
    const render = () => {
      list.innerHTML = items.map((it, i) => `
        <li role="option" id="${uid}-${i}" class="${i === active ? "on" : ""}" data-i="${i}" aria-selected="${i === active}">
          <span class="np-pick"><b>${esc(it.label)}</b>${mode === "search" && it.trail !== it.label ? `<span class="muted">${esc(it.trail)}</span>` : ""}</span>
          ${mode === "browse" ? `<span class="muted np-count">${it.inventories ? num(it.inventories) + " оп." : ""}</span>
          ${it.has_children ? `<button type="button" class="np-in" data-in="${it.id}" aria-label="Подведы: ${esc(it.label)}">›</button>` : ""}` : ""}
        </li>`).join("");
      input.setAttribute("aria-activedescendant", active >= 0 ? `${uid}-${active}` : "");
    };
    const search = (q) => {
      mode = "search"; crumbs.innerHTML = "";
      get("/nodes/search/?q=" + encodeURIComponent(q)).then((d) => {
        items = d.items; active = items.length ? 0 : -1; render();
        msg.textContent = items.length ? "" : "Не найдено. Введите код («126») или часть названия; или откройте «Обзор».";
        open(true);
      }).catch((e) => { msg.textContent = "Поиск не выполнен: " + e.message; open(true); });
    };
    const browse = (pid) => {
      mode = "browse";
      get("/nodes/children/?parent=" + (pid || "")).then((d) => {
        items = d.items.map((x) => ({ ...x, trail: x.trail })); active = -1;
        crumbs.innerHTML = `<button type="button" data-in="">Все учреждения</button>` +
          d.crumbs.map((c) => ` › <button type="button" data-in="${c.id}">${esc(c.label)}</button>`).join("") +
          (d.parent ? ` <button type="button" class="np-take" data-take="${d.parent.id}">выбрать «${esc(d.parent.label)}»</button>` : "");
        crumbs._parent = d.parent;
        render();
        msg.textContent = items.length ? "" : "Подведов нет.";
        open(true);
      }).catch((e) => { msg.textContent = "Не загрузилось: " + e.message; open(true); });
    };

    input.addEventListener("input", () => {
      clearTimeout(timer);
      const q = input.value.trim();
      if (!q) { open(false); return; }
      timer = setTimeout(() => search(q), 200);
    });
    input.addEventListener("keydown", (e) => {
      if (pop.hidden && e.key === "ArrowDown") { input.value.trim() ? search(input.value.trim()) : browse(""); e.preventDefault(); return; }
      if (pop.hidden) return;
      if (e.key === "ArrowDown") { active = Math.min(items.length - 1, active + 1); render(); e.preventDefault(); }
      else if (e.key === "ArrowUp") { active = Math.max(0, active - 1); render(); e.preventDefault(); }
      else if (e.key === "Enter") { if (active >= 0) { choose(items[active]); e.preventDefault(); } }
      else if (e.key === "Escape") { open(false); }
      else if (e.key === "ArrowRight" && mode === "browse" && active >= 0 && items[active].has_children) { browse(items[active].id); e.preventDefault(); }
    });
    root.querySelector(".np-browse").addEventListener("click", () => (pop.hidden || mode !== "browse") ? browse("") : open(false));
    clear.addEventListener("click", () => { choose(null); input.focus(); });
    pop.addEventListener("click", (e) => {
      const into = e.target.closest("[data-in]");
      if (into) { browse(into.dataset.in); return; }
      const take = e.target.closest("[data-take]");
      if (take) { choose(crumbs._parent); return; }
      const li = e.target.closest("li[data-i]");
      if (li) choose(items[+li.dataset.i]);
    });
    document.addEventListener("click", (e) => { if (!root.contains(e.target)) open(false); });
  }

  /* ---------------- дерево: <ul class="tree" data-selected data-open="1,5,9"> */
  function tree(rootUl) {
    const selected = rootUl.dataset.selected || "";
    const openIds = new Set((rootUl.dataset.open || "").split(",").filter(Boolean));
    const load = (ul, pid) => get("/nodes/children/?parent=" + (pid || "")).then((d) => {
      ul.innerHTML = d.items.length ? d.items.map((n) => `
        <li role="treeitem" aria-expanded="${n.has_children ? "false" : ""}" data-id="${n.id}" ${String(n.id) === selected ? 'aria-selected="true" class="on"' : ""}>
          <div class="tree-row">
            ${n.has_children ? `<button type="button" class="tree-tog" tabindex="-1" aria-label="Раскрыть">▸</button>` : `<span class="tree-tog"></span>`}
            <a href="?node=${n.id}">${esc(n.label)}</a>
            <span class="muted tree-count">${n.inventories ? num(n.inventories) + " оп. · " + num(n.rows) + " поз." : ""}</span>
          </div>
          <ul role="group" hidden></ul>
        </li>`).join("") : `<li class="muted tree-empty">${pid ? "Подведов нет" : "Учреждений нет — «Добавить учреждение» справа"}</li>`;
      ul.querySelectorAll(":scope > li[data-id]").forEach((li) => {
        if (openIds.has(li.dataset.id)) toggle(li, true);
      });
      const sel = rootUl.querySelector("li.on > .tree-row a");
      if (sel) sel.scrollIntoView({ block: "nearest" });
    });
    const toggle = (li, force) => {
      const sub = li.querySelector(":scope > ul");
      const btn = li.querySelector(":scope > .tree-row .tree-tog");
      if (!btn || btn.tagName !== "BUTTON") return;
      const on = force ?? sub.hidden;
      sub.hidden = !on;
      btn.textContent = on ? "▾" : "▸";
      li.setAttribute("aria-expanded", on ? "true" : "false");
      if (on && !sub.dataset.loaded) { sub.dataset.loaded = "1"; sub.innerHTML = '<li class="muted tree-empty">загрузка…</li>'; load(sub, li.dataset.id); }
    };
    rootUl.addEventListener("click", (e) => {
      const b = e.target.closest(".tree-tog");
      if (b && b.tagName === "BUTTON") toggle(b.closest("li"));
    });
    rootUl.addEventListener("keydown", (e) => {
      const a = document.activeElement.closest("li[data-id]");
      if (!a) return;
      const rows = [...rootUl.querySelectorAll("li[data-id]")].filter((li) => li.offsetParent !== null);
      const i = rows.indexOf(a);
      const focus = (li) => li && li.querySelector(":scope > .tree-row a").focus();
      if (e.key === "ArrowDown") { focus(rows[i + 1]); e.preventDefault(); }
      else if (e.key === "ArrowUp") { focus(rows[i - 1]); e.preventDefault(); }
      else if (e.key === "ArrowRight") { toggle(a, true); e.preventDefault(); }
      else if (e.key === "ArrowLeft") {
        if (a.getAttribute("aria-expanded") === "true") toggle(a, false);
        else focus(a.parentElement.closest("li[data-id]"));
        e.preventDefault();
      }
    });
    load(rootUl, "");
  }

  document.querySelectorAll(".np").forEach(picker);
  document.querySelectorAll("ul.tree").forEach(tree);
})();
