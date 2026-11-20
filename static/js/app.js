/* nitsdate — progressive enhancement. Every form also works without JS. */
(function () {
  "use strict";

  const csrf = () => document.querySelector('meta[name="csrf-token"]').content;
  const post = (url, body) =>
    fetch(url, { method: "POST", body, headers: { "X-CSRFToken": csrf(), "X-Requested-With": "fetch" }, credentials: "same-origin" });

  /* ---- light / dark theme ---- */
  const root = document.documentElement;
  const metaTheme = document.querySelector('meta[name="theme-color"]');
  const paintMeta = () => metaTheme && (metaTheme.content = getComputedStyle(root).getPropertyValue("--paper").trim() || "#fffdfd");
  paintMeta();
  document.querySelectorAll("[data-theme-toggle]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      try { localStorage.setItem("theme", next); } catch (_) {}
      paintMeta();
    });
  });
  // no saved choice yet: keep following the phone / OS setting
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", (e) => {
    let saved = null;
    try { saved = localStorage.getItem("theme"); } catch (_) {}
    if (!saved) { root.setAttribute("data-theme", e.matches ? "dark" : "light"); paintMeta(); }
  });

  /* ---- file pickers that upload as soon as you choose ---- */
  document.querySelectorAll("input[data-autosubmit]").forEach((input) => {
    input.addEventListener("change", () => { if (input.files.length) input.form.submit(); });
  });

  /* ---- photo viewer for galleries ---- */
  const shots = Array.from(document.querySelectorAll("[data-lightbox]"));
  if (shots.length && window.HTMLDialogElement) {
    const dlg = document.createElement("dialog");
    dlg.className = "lightbox";
    dlg.innerHTML = '<img alt="">' +
      '<button type="button" class="lb-btn lb-prev" aria-label="Previous photo">&#8249;</button>' +
      '<button type="button" class="lb-btn lb-next" aria-label="Next photo">&#8250;</button>' +
      '<button type="button" class="lb-btn lb-close" aria-label="Close">&times;</button>';
    document.body.appendChild(dlg);
    const img = dlg.querySelector("img");
    let at = 0;
    const show = (n) => { at = (n + shots.length) % shots.length; img.src = shots[at].href; img.alt = shots[at].querySelector("img")?.alt || ""; };
    if (shots.length < 2) dlg.querySelectorAll(".lb-prev, .lb-next").forEach((b) => b.remove());
    shots.forEach((a, n) => a.addEventListener("click", (e) => { e.preventDefault(); show(n); dlg.showModal(); }));
    dlg.querySelector(".lb-close").addEventListener("click", () => dlg.close());
    dlg.querySelector(".lb-prev")?.addEventListener("click", () => show(at - 1));
    dlg.querySelector(".lb-next")?.addEventListener("click", () => show(at + 1));
    dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
    dlg.addEventListener("keydown", (e) => {
      if (e.key === "ArrowLeft") show(at - 1);
      if (e.key === "ArrowRight") show(at + 1);
    });
  }

  /* ---- flashes ---- */
  document.querySelectorAll(".flash").forEach((el) => {
    el.querySelector(".flash-x")?.addEventListener("click", () => el.remove());
    if (!el.classList.contains("flash-error")) setTimeout(() => el.remove(), 6000);
  });

  /* ---- "Any" chip: picking it clears the others, picking another clears it, and it comes back
     when nothing else is picked ---- */
  document.querySelectorAll("[data-any-group]").forEach((group) => {
    const any = group.querySelector("[data-any]");
    const others = [...group.querySelectorAll("input[type=checkbox]")].filter((i) => i !== any);
    const sync = (changed) => {
      if (changed === any && any.checked) others.forEach((i) => { i.checked = false; });
      else if (changed !== any && changed.checked) any.checked = false;
      if (!others.some((i) => i.checked)) any.checked = true;
    };
    [any, ...others].forEach((i) => i.addEventListener("change", () => sync(i)));
  });

  /* ---- typed confirmation: the button wakes up once the phrase matches (any case) ---- */
  document.querySelectorAll("[data-phrase]").forEach((input) => {
    const btn = input.form.querySelector("button");
    const sync = () => { btn.disabled = input.value.trim().replace(/\s+/g, " ").replace(/^["“”]+|["“”]+$/g, "").toLowerCase() !== input.dataset.phrase; };
    input.addEventListener("input", sync);
    sync();
  });

  /* ---- confirm before destructive forms ---- */
  document.addEventListener("submit", (e) => {
    const msg = e.target.dataset?.confirm;
    if (msg && !window.confirm(msg)) e.preventDefault();
  }, true);

  /* ---- popovers: only one open, close on outside click / Esc ---- */
  document.addEventListener("click", (e) => {
    document.querySelectorAll("details.pop[open]").forEach((d) => {
      if (!d.contains(e.target)) d.removeAttribute("open");
    });
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") document.querySelectorAll("details.pop[open]").forEach((d) => d.removeAttribute("open"));
  });
  // keep opened menus inside the screen (narrow phones, tiles near the edge)
  document.addEventListener("toggle", (e) => {
    const d = e.target;
    if (!(d instanceof HTMLDetailsElement) || !d.open || !d.classList.contains("pop")) return;
    const body = d.querySelector(":scope > .pop-body");
    if (!body) return;
    body.style.transform = "";
    const r = body.getBoundingClientRect(), pad = 8;
    let dx = 0;
    if (r.left < pad) dx = pad - r.left;
    else if (r.right > window.innerWidth - pad) dx = window.innerWidth - pad - r.right;
    if (dx) body.style.transform = "translateX(" + Math.round(dx) + "px)";
  }, true);

  /* ---- profile: inline edit toggles ---- */
  document.querySelectorAll("[data-edit-toggle]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const row = btn.closest(".field-row");
      row.classList.toggle("editing");
      if (row.classList.contains("editing")) row.querySelector(".field-editor input:not([type=hidden]), .field-editor textarea, .field-editor select")?.focus();
    });
  });

  /* ---- votes ---- */
  document.querySelectorAll("form[data-vote]").forEach((form) => {
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const value = e.submitter?.value;
      if (!value) return;
      const data = new FormData(form);
      data.set("value", value);
      try {
        const res = await post(form.action, data);
        if (!res.ok) throw new Error(res.status);
        const json = await res.json();
        form.querySelector(".score").textContent = json.score;
        form.querySelector(".vote-up").classList.toggle("on", json.mine === 1);
        form.querySelector(".vote-down").classList.toggle("on", json.mine === -1);
      } catch (_) {
        // fall back to a normal post; submit() drops the clicked button, so add its value
        const hidden = document.createElement("input");
        hidden.type = "hidden"; hidden.name = "value"; hidden.value = value;
        form.appendChild(hidden);
        form.submit();
      }
    });
  });

  /* ---- comments: reply + collapse ---- */
  document.querySelectorAll("[data-reply-toggle]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const form = btn.closest(".comment-inner").querySelector(":scope > .reply-form");
      form.hidden = !form.hidden;
      if (!form.hidden) form.querySelector("textarea").focus();
    });
  });
  document.querySelectorAll("[data-collapse]").forEach((btn) => {
    btn.addEventListener("click", () => btn.closest(".comment").classList.toggle("collapsed"));
  });

  /* ---- chat room ---- */
  const chat = document.getElementById("chat");
  if (chat) {
    const scroller = document.getElementById("chat-scroll");
    const list = document.getElementById("messages");
    const panel = document.getElementById("chat-panel");
    const form = document.getElementById("composer");
    const input = document.getElementById("msg-input");
    const photo = document.getElementById("photo-input");
    const chip = document.getElementById("photo-chip");
    const draftKey = "draft:" + chat.dataset.sendUrl;
    let lastId = parseInt(chat.dataset.lastId, 10) || 0;
    let busy = false;

    const nearBottom = () => scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 120;

    // older messages load 50 at a time: from a button, or automatically when you scroll to the top
    async function loadOlder(url, before, into, button) {
      if (button.dataset.loading) return;
      button.dataset.loading = "1";
      try {
        const res = await fetch(url + (before ? "&before=" + before : ""), { headers: { "X-Requested-With": "fetch" } });
        if (!res.ok) return;
        const data = await res.json();
        const keep = scroller.scrollHeight - scroller.scrollTop;   // stay at the same message after prepending
        into.insertAdjacentHTML("afterbegin", data.html);
        scroller.scrollTop = scroller.scrollHeight - keep;
        button.dataset.before = data.first_id || "";
        button.hidden = !data.has_more;
      } catch (_) {
      } finally {
        delete button.dataset.loading;
      }
    }
    const earlier = document.getElementById("load-earlier");
    earlier.addEventListener("click", () => loadOlder(earlier.dataset.url, earlier.dataset.before, list, earlier));
    scroller.addEventListener("scroll", () => {
      if (scroller.scrollTop < 60 && !earlier.hidden) earlier.click();
    });
    document.querySelectorAll("details.archive[data-history-url]").forEach((d) => {
      const body = d.querySelector(".archive-body");
      const more = d.querySelector(".load-earlier");
      more.addEventListener("click", () => loadOlder(d.dataset.historyUrl, more.dataset.before, body, more));
      d.addEventListener("toggle", () => {
        if (!d.open || d.dataset.loaded) return;
        d.dataset.loaded = "1";
        body.innerHTML = "";
        loadOlder(d.dataset.historyUrl, "", body, more);
      });
    });
    const toBottom = () => { scroller.scrollTop = scroller.scrollHeight; };
    toBottom();
    list.querySelectorAll("img").forEach((img) => img.addEventListener("load", toBottom, { once: true }));

    const grow = () => { if (!input) return; input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 140) + "px"; };

    async function poll() {
      if (busy) return;
      busy = true;
      try {
        const res = await fetch(chat.dataset.pollUrl + "?after=" + lastId, { headers: { "X-Requested-With": "fetch" } });
        if (!res.ok) return;
        const data = await res.json();
        // tier changed (moved to inbox / priority) or chat ended: reload to show archive + new header
        if (data.tier !== chat.dataset.tier || (data.active ? "1" : "0") !== chat.dataset.active ||
            (data.blocked ? "1" : "0") !== chat.dataset.blocked) {
          if (input) try { sessionStorage.setItem(draftKey, input.value); } catch (_) {}
          location.reload();
          return;
        }
        if (data.html) {
          const stick = nearBottom();
          const tmp = document.createElement("div");
          tmp.innerHTML = data.html;
          tmp.querySelectorAll("[data-id]").forEach((node) => {
            if (!list.querySelector('[data-id="' + node.dataset.id + '"]')) {
              list.appendChild(node);
              node.querySelectorAll("img").forEach((img) => img.addEventListener("load", () => stick && toBottom(), { once: true }));
            }
          });
          lastId = Math.max(lastId, data.last_id);
          if (stick) toBottom();
        }
        if (data.panel_key !== panel.dataset.key) {
          panel.innerHTML = data.panel;
          panel.dataset.key = data.panel_key;
        }
      } catch (_) {
        /* offline – try again next tick */
      } finally {
        busy = false;
      }
    }
    setInterval(() => { if (!document.hidden) poll(); }, 3000);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });

    if (form) {
      try { const d = sessionStorage.getItem(draftKey); if (d) { input.value = d; sessionStorage.removeItem(draftKey); grow(); } } catch (_) {}
      input.addEventListener("input", grow);
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey && !e.isComposing && window.matchMedia("(pointer: fine)").matches) {
          e.preventDefault();
          form.requestSubmit();
        }
      });
      photo.addEventListener("change", () => {
        chip.hidden = !photo.files.length;
        document.getElementById("photo-name").textContent = photo.files[0] ? "📷 " + photo.files[0].name : "";
      });
      document.getElementById("photo-clear").addEventListener("click", () => { photo.value = ""; chip.hidden = true; });

      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        if (!input.value.trim() && !photo.files.length) return;
        const btn = form.querySelector(".send-btn");
        btn.disabled = true;
        try {
          const res = await post(chat.dataset.sendUrl, new FormData(form));
          const data = await res.json().catch(() => ({}));
          if (!res.ok || !data.ok) {
            alertInline(data.error || "Couldn't send. Try again.");
            return;
          }
          input.value = "";
          photo.value = "";
          chip.hidden = true;
          grow();
          await poll();
          toBottom();
        } catch (_) {
          alertInline("You seem to be offline.");
        } finally {
          btn.disabled = false;
          input.focus();
        }
      });
    }

    function alertInline(text) {
      const div = document.createElement("div");
      div.className = "msg-system";
      div.innerHTML = '<span style="background:var(--red-soft);color:var(--red)"></span>';
      div.firstChild.textContent = text;
      list.appendChild(div);
      toBottom();
      setTimeout(() => div.remove(), 5000);
    }
  }

  /* ---- matchmaking: wait for a match ---- */
  const searching = document.getElementById("searching");
  if (searching) {
    const expires = new Date(searching.dataset.expires);
    const label = document.getElementById("expires-in");
    let expiredChecked = false;
    const tick = () => {
      const ms = expires - new Date();
      if (ms <= 0) {
        // the phone's clock may be ahead of the server: ask the server instead of reload-looping
        label.textContent = "about to expire";
        if (!expiredChecked) { expiredChecked = true; check(); }
        return;
      }
      const h = Math.floor(ms / 3.6e6), m = Math.floor((ms % 3.6e6) / 6e4);
      label.textContent = "expires in " + (h ? h + "h " : "") + m + "m";
    };
    const check = async () => {
      if (document.hidden) return;
      try {
        const res = await fetch(searching.dataset.statusUrl, { headers: { "X-Requested-With": "fetch" } });
        const data = await res.json();
        if (data.status === "matched" && data.url) location.href = data.url;
        else if (data.status !== "searching") location.reload();
      } catch (_) {}
    };
    // a new search shows "matching" for a few seconds; the first check comes right after that
    setTimeout(() => { check(); setInterval(check, 4000); }, +searching.dataset.firstCheck || 4000);
    tick();
    setInterval(tick, 30000);
  }
})();
