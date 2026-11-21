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

  /* ---- live updates: one WebSocket per tab (live/consumers.py). The server pushes the badge
     counts and short "look again" signals (a chat changed, you've got a match); pages then fetch
     the details through the normal views. While the socket is up the timers below are only a
     slow safety net; when it can't connect they ask on their usual beat. ---- */
  const mainnav = document.querySelector(".mainnav[data-badges-url]");
  const topbar = document.querySelector(".topbar");
  const SAFETY_MS = 60000;
  const Live = (() => {
    const handlers = {};
    let ws = null, up = false, stopped = !mainnav || !("WebSocket" in window);
    let retries = 0, retryTimer = null, pingTimer = null, pongTimer = null;
    const url = mainnav && (mainnav.dataset.liveUrl ||
      (location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws/live/");
    const emit = (type, data) => (handlers[type] || []).forEach((fn) => { try { fn(data); } catch (_) {} });
    const closed = (code) => {
      const was = up;
      up = false; ws = null;
      clearInterval(pingTimer); clearTimeout(pongTimer);
      if (was) emit("close");
      if (code === 4401) { stopped = true; return; }   // signed out, or no longer allowed in
      retry();
    };
    const drop = () => {   // a dead network can take ages to report the close: don't wait for it
      const old = ws;
      if (!old) return;
      old.onclose = old.onmessage = null;
      try { old.close(); } catch (_) {}
      closed(1006);
    };
    const ping = () => {
      if (!ws || ws.readyState !== 1) return;
      ws.send(JSON.stringify({ t: "ping", visible: !document.hidden }));
      clearTimeout(pongTimer);
      pongTimer = setTimeout(drop, 10000);   // no answer within 10 s: treat it as dropped
    };
    const connect = () => {
      clearTimeout(retryTimer);
      if (stopped || ws) return;
      try { ws = new WebSocket(url); } catch (_) { ws = null; return retry(); }
      ws.onopen = () => {
        up = true; retries = 0;
        clearInterval(pingTimer); pingTimer = setInterval(ping, 30000);
        emit("open");
      };
      ws.onmessage = (e) => {
        let d;
        try { d = JSON.parse(e.data); } catch (_) { return; }
        if (d.t === "pong") clearTimeout(pongTimer);
        else emit(d.t, d);
      };
      ws.onclose = (e) => closed(e.code);
    };
    const retry = () => {   // 1 s, 2 s, 4 s ... up to 30 s, spread out so tabs don't all retry at once
      const wait = Math.min(30000, 1000 * 2 ** retries++) * (0.5 + Math.random() / 2);
      retryTimer = setTimeout(connect, wait);
    };
    // phones put background tabs to sleep: reconnect (or say hello) as soon as the tab is back
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) return;
      if (ws) ping(); else if (!stopped) { retries = 0; connect(); }
    });
    return {
      on(type, fn) { (handlers[type] = handlers[type] || []).push(fn); },
      send(data) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(data)); },
      get up() { return up; },
      start: connect,
    };
  })();

  // run `fn` every `fast` ms while the socket is down and every SAFETY_MS while it's up. With
  // catchUp it also runs straight away whenever the socket (re)connects, for anything missed
  const loops = [];
  const every = (fn, fast, { first, catchUp = true } = {}) => {
    let timer = null;
    const loop = {
      fast,
      catchUp,
      run(ms) {
        clearTimeout(timer);
        timer = setTimeout(async () => {
          try { await fn(); } catch (_) { /* offline for a moment */ }
          loop.run(Live.up ? SAFETY_MS : fast);
        }, ms);
      },
    };
    loops.push(loop);
    loop.run(first === undefined ? fast : first);
    return loop;
  };
  Live.on("open", () => loops.forEach((l) => l.run(l.catchUp ? 0 : SAFETY_MS)));
  Live.on("close", () => loops.forEach((l) => l.run(l.fast)));

  /* ---- nav badges: unread messages, connection requests, notifications and (for mods) open
     reports. On the Messages page the list itself reloads when something changed. ---- */
  if (mainnav) {
    const baseTitle = document.title.replace(/^\(\d+\)\s*/, "");
    const setBadge = (key, n) => {
      const link = topbar.querySelector(`[data-badge="${key}"]`);
      if (!link || n === undefined) return;
      let b = link.querySelector(".badge");
      if (n > 0) {
        if (!b) { b = document.createElement("b"); b.className = "badge"; link.appendChild(b); }
        b.textContent = n;
      } else if (b) b.remove();
    };
    const setDot = (on) => {
      const link = mainnav.querySelector('[data-dot="searching"]');
      const dot = link && link.querySelector(".dot");
      if (on && link && !dot) link.insertAdjacentHTML("beforeend", '<i class="dot pulse" title="Searching"></i>');
      else if (!on && dot) dot.remove();
    };
    const live = document.querySelector("[data-live-inbox]");
    const reloadInbox = async () => {
      const url = new URL(location.href);
      url.searchParams.set("tab", live.dataset.tab);   // stay on the tab you're looking at
      const res = await fetch(url, { headers: { "X-Requested-With": "fetch" }, credentials: "same-origin" });
      if (!res.ok) return;
      const fresh = new DOMParser().parseFromString(await res.text(), "text/html").querySelector("[data-live-inbox]");
      if (fresh) { live.innerHTML = fresh.innerHTML; live.dataset.version = fresh.dataset.version; }
    };
    const apply = async (d) => {
      setBadge("messages", d.messages);
      setBadge("requests", d.requests);
      setBadge("reports", d.reports);
      setBadge("notifications", d.notifications);
      setDot(d.searching);
      const waiting = d.messages + d.requests + (d.notifications || 0);
      document.title = (waiting ? `(${waiting}) ` : "") + baseTitle;   // background tabs too
      // a hidden Messages tab catches up when you come back to it (see below)
      if (live && !document.hidden && d.inbox !== live.dataset.version) await reloadInbox();
    };
    let busy = false;
    const refresh = async () => {
      if (document.hidden || busy) return;
      busy = true;
      try {
        const res = await fetch(mainnav.dataset.badgesUrl, { headers: { "X-Requested-With": "fetch" }, credentials: "same-origin" });
        const type = res.headers.get("content-type") || "";
        if (res.ok && type.includes("json")) await apply(await res.json());
      } catch (_) { /* offline for a moment: try again on the next beat */ }
      busy = false;
    };
    Live.on("badges", apply);
    every(refresh, 8000, { catchUp: false });   // the socket sends fresh counts when it connects
    // the Priority list shows "Active now / Active 5 minutes ago": keep it current while it's open
    if (live && live.dataset.tab === "priority") {
      every(() => { if (!document.hidden) return reloadInbox(); }, 60000, { catchUp: false });
    }
    document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
  }

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
    // Priority chats only: "Active now / Active 5 minutes ago", "typing…" and "Seen"
    const presenceEl = document.getElementById("presence");
    const presenceLive = document.getElementById("presence-live");
    const receiptEl = document.getElementById("receipt");
    let typingTimer = null;
    const announce = (text) => { if (presenceLive) presenceLive.textContent = text; };
    const stopTyping = () => {
      if (!presenceEl) return;
      clearTimeout(typingTimer);
      presenceEl.classList.remove("typing");
      presenceEl.textContent = presenceEl.dataset.text || "";
    };
    const showPresence = (p) => {
      if (!presenceEl || !p) return;
      presenceEl.dataset.text = p.text || "View profile";
      if (presenceEl.classList.contains("online") !== !!p.now) announce(p.now ? "Active now" : "Offline");
      presenceEl.classList.toggle("online", !!p.now);
      if (!presenceEl.classList.contains("typing")) presenceEl.textContent = presenceEl.dataset.text;
    };
    const showReceipt = (text) => {
      if (!receiptEl || text === undefined) return;
      receiptEl.textContent = text;
      receiptEl.hidden = !text;
    };
    Live.on("typing", (d) => {
      if (!presenceEl || String(d.conv) !== chat.dataset.conv) return;
      if (!presenceEl.classList.contains("typing")) announce("typing");
      presenceEl.classList.add("typing");
      presenceEl.textContent = "typing…";
      clearTimeout(typingTimer);
      typingTimer = setTimeout(stopTyping, 6000);   // no news for a while: they stopped
    });
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

    // fetches what's new in this chat (and marks it read, so never from a hidden tab)
    let again = false;
    async function poll() {
      if (busy) { again = true; return; }   // a signal came in mid-fetch: fetch once more after
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
        showPresence(data.presence);
        showReceipt(data.receipt);
        if (data.html) {
          const stick = nearBottom();
          const tmp = document.createElement("div");
          tmp.innerHTML = data.html;
          if (tmp.querySelector(".msg.theirs")) stopTyping();   // their message arrived
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
        if (again) { again = false; poll(); }
      }
    }
    Live.on("chat", (d) => {
      if (String(d.conv) === chat.dataset.conv && !document.hidden) poll();   // hidden: caught up on return
    });
    every(() => { if (!document.hidden) return poll(); }, 3000);
    if (presenceEl && !document.hidden) poll();   // Priority: this marks it read, so "Seen" means seen
    document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });

    if (form) {
      try { const d = sessionStorage.getItem(draftKey); if (d) { input.value = d; sessionStorage.removeItem(draftKey); grow(); } } catch (_) {}
      input.addEventListener("input", grow);
      let typedAt = 0;
      if (presenceEl) input.addEventListener("input", () => {
        const now = Date.now();
        if (input.value.trim() && now - typedAt > 2500) {
          typedAt = now;
          Live.send({ t: "typing", conv: +chat.dataset.conv });
        }
      });
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
    // a new search shows "matching" for a few seconds; without a live connection the first check
    // comes right after that. With one, the match arrives as a push (handled below).
    every(check, 4000, { first: +searching.dataset.firstCheck || 4000 });
    tick();
    setInterval(tick, 30000);
  }

  /* ---- a matchmaking search found someone: go there from the match page, or say so elsewhere ---- */
  Live.on("match", (d) => {
    if (searching || !d.url) { if (d.url) location.href = d.url; return; }
    if (chat && chat.dataset.conv && d.url.endsWith("/" + chat.dataset.conv + "/")) return;
    let stack = document.querySelector(".flash-stack");
    if (!stack) {
      stack = document.createElement("div");
      stack.className = "flash-stack";
      stack.setAttribute("role", "status");
      document.querySelector("main")?.prepend(stack);
    }
    const note = document.createElement("div");
    note.className = "flash flash-success";
    note.innerHTML = '<span>It\'s a match! <a></a></span><button class="flash-x" aria-label="Dismiss">✕</button>';
    const link = note.querySelector("a");
    link.href = d.url;
    link.textContent = "Open the chat";
    note.querySelector(".flash-x").addEventListener("click", () => note.remove());
    stack.prepend(note);
  });

  Live.start();
})();
