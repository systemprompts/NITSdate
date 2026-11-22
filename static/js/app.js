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

  /* ---- sounds: a quick two-note pop for a new message or a match, a softer chime for notifications
     and connection requests. Made with Web Audio, so there are no sound files. Browsers only allow
     sound once you've tapped, clicked or typed on the page; with several tabs open only one plays.
     Each device can switch them off on the notifications page. ---- */
  const Sound = (() => {
    let ctx = null;
    const on = () => { try { return localStorage.getItem("sounds") !== "off"; } catch (_) { return true; } };
    const unlock = () => {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return;
      if (!ctx) ctx = new AC();
      if (ctx.state !== "running" && ctx.state !== "closed") ctx.resume().catch(() => {});
    };
    ["pointerdown", "keydown", "touchend"].forEach((ev) => document.addEventListener(ev, unlock, { capture: true, passive: true }));
    // Start straight away too: Chrome lets a page play sound without a new tap when you tapped or clicked
    // on the page you came from on this site, so moving from the chat to the feed doesn't go quiet.
    // Where that isn't allowed it simply waits for the first tap or key press here.
    if (on() && mainnav) unlock();
    // phones pause audio while the screen is off or another app is in front: wake it on the way back
    document.addEventListener("visibilitychange", () => { if (!document.hidden && ctx) unlock(); });
    const tone = (freq, at, length, type, volume) => {
      const osc = ctx.createOscillator(), gain = ctx.createGain(), t = ctx.currentTime + at;
      osc.type = type;
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0.0001, t);
      gain.gain.exponentialRampToValueAtTime(volume, t + 0.012);
      gain.gain.exponentialRampToValueAtTime(0.0001, t + length);
      osc.connect(gain).connect(ctx.destination);
      osc.start(t);
      osc.stop(t + length + 0.05);
    };
    const sounds = {
      // two bright notes going up: "pop-pop"
      message: () => { tone(880, 0, 0.11, "sine", 0.2); tone(1318.5, 0.09, 0.17, "sine", 0.18); },
      // a soft, lower bell that rings out a little longer
      notification: () => { tone(587.3, 0, 0.55, "triangle", 0.14); tone(880, 0.13, 0.6, "sine", 0.08); },
    };
    const last = {};
    const play = (kind, force, retried) => {
      if ((!on() && !force) || !ctx) return;
      if (ctx.state !== "running") {
        // paused (or never allowed to start): try to wake it, and play only if that works straight away,
        // so a sound never arrives late
        if (retried || ctx.state === "closed") return;
        ctx.resume().catch(() => {});
        setTimeout(() => { if (ctx.state === "running") play(kind, force, true); }, 250);
        return;
      }
      const now = Date.now();
      if (now - (last[kind] || 0) < 1500) return;   // one event reported twice still makes one sound
      last[kind] = now;
      const go = () => { try { sounds[kind](); } catch (_) {} };
      if (force) return go();
      if (navigator.locks) {   // several tabs open: whichever takes the lock plays, the others stay quiet
        navigator.locks.request("nitsdate-sound-" + kind, { ifAvailable: true }, (lock) =>
          lock ? (go(), new Promise((done) => setTimeout(done, 1500))) : null);
      } else {
        try {
          if (now - (+localStorage.getItem("sound:" + kind) || 0) < 1500) return;
          localStorage.setItem("sound:" + kind, now);
        } catch (_) {}
        go();
      }
    };
    return { play, unlock, on };
  })();

  /* ---- push notifications (notifications/push.py): new messages and matches, even with the site
     closed. Asked for only once someone has written to you and you're looking at their message (see
     the chat room), never on a first visit. ---- */
  const Push = (() => {
    const meta = document.querySelector('meta[name="push-key"]');
    const supported = !!(meta && "serviceWorker" in navigator && "PushManager" in window && "Notification" in window);
    const SYNC = "push-sync", PROMPT = "push-prompt", OFF = "push-off";
    const store = {
      get: (k) => { try { return localStorage.getItem(k); } catch (_) { return null; } },
      set: (k, v) => { try { localStorage.setItem(k, v); } catch (_) {} },
      del: (k) => { try { localStorage.removeItem(k); } catch (_) {} },
    };
    const today = () => (meta ? meta.dataset.user : "") + ":" + new Date().toDateString();
    const keyBytes = () => {
      const s = meta.content, pad = "=".repeat((4 - (s.length % 4)) % 4);
      const raw = atob((s + pad).replace(/-/g, "+").replace(/_/g, "/"));
      return Uint8Array.from(raw, (c) => c.charCodeAt(0));
    };
    let reg = null;
    const ready = () => reg || (reg = navigator.serviceWorker.register(meta.dataset.sw).then(() => navigator.serviceWorker.ready));
    const sameKey = (sub) => {
      const k = sub.options && sub.options.applicationServerKey;
      if (!k) return true;
      const a = new Uint8Array(k), b = keyBytes();
      return a.length === b.length && a.every((v, i) => v === b[i]);
    };
    async function subscribe() {
      const r = await ready();
      let sub = await r.pushManager.getSubscription();
      if (sub && !sameKey(sub)) { await sub.unsubscribe(); sub = null; }   // the server's key changed
      if (!sub) sub = await r.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes() });
      const res = await post(meta.dataset.subscribe, JSON.stringify(sub));
      if (!res.ok) throw new Error("not saved");
      store.set(SYNC, today());
    }
    async function enable() {
      const answer = await Notification.requestPermission();   // the browser's own question
      if (answer === "granted") {
        store.del(OFF);
        await subscribe();
      }
      return answer;
    }
    async function disable() {
      store.set(OFF, "1");
      store.del(SYNC);
      const sub = await (await ready()).pushManager.getSubscription();
      if (sub) {
        await post(meta.dataset.unsubscribe, JSON.stringify({ endpoint: sub.endpoint })).catch(() => {});
        await sub.unsubscribe();
      }
    }
    async function isOn() {
      if (!supported || Notification.permission !== "granted") return false;
      return !!(await (await ready()).pushManager.getSubscription());
    }
    // allowed already: once a day, and after signing in again, tell the server this browser is still
    // yours (signing out removes it there, and a shared browser follows whoever is signed in)
    if (supported && Notification.permission === "granted" && !store.get(OFF) && store.get(SYNC) !== today()) {
      subscribe().catch(() => {});
    }
    document.querySelectorAll(".logout-form").forEach((f) => f.addEventListener("submit", () => store.del(SYNC)));

    // the "Turn on notifications?" card
    const card = document.getElementById("push-ask");
    const step = (name) => card.querySelectorAll("[data-push-step]").forEach((el) => { el.hidden = el.dataset.pushStep !== name; });
    const snooze = () => {
      let s = {};
      try { s = JSON.parse(store.get(PROMPT) || "{}"); } catch (_) {}
      store.set(PROMPT, JSON.stringify({ at: Date.now(), n: (s.n || 0) + 1 }));
    };
    let asked = false;
    function ask() {
      if (!supported || !card || asked || document.hidden || Notification.permission !== "default") return;
      let s = {};
      try { s = JSON.parse(store.get(PROMPT) || "{}"); } catch (_) {}
      if ((s.n || 0) >= 3 || (s.at && Date.now() - s.at < 7 * 864e5)) return;   // "Not now": again in a week, 3 times at most
      asked = true;
      step("ask");
      card.hidden = false;
    }
    if (card) {
      card.querySelector("[data-push-no]").addEventListener("click", () => { snooze(); card.hidden = true; });
      card.querySelectorAll("[data-push-close]").forEach((b) => b.addEventListener("click", () => { card.hidden = true; }));
      card.querySelector("[data-push-yes]").addEventListener("click", async () => {
        let answer;
        try { answer = await enable(); } catch (_) { answer = "failed"; }
        if (answer === "granted") { step("done"); setTimeout(() => { card.hidden = true; }, 4000); }
        else if (answer === "denied" || answer === "failed") step(answer === "denied" ? "blocked" : "failed");
        else { snooze(); card.hidden = true; }   // closed the browser's question without answering
      });
    }

    // Notifications page: on / off for this device
    const panel = document.querySelector("[data-push-panel]");
    if (panel) {
      const state = panel.querySelector("[data-push-state]");
      const on = panel.querySelector("[data-push-on]"), off = panel.querySelector("[data-push-off]");
      const show = async (note) => {
        on.hidden = off.hidden = true;
        if (!supported) {
          state.textContent = /iPhone|iPad/.test(navigator.userAgent)
            ? "Add nitsdate to your Home Screen and open it from there to get notifications on this iPhone."
            : "This browser can't show push notifications.";
          return;
        }
        if (Notification.permission === "denied") {
          state.textContent = "Blocked by your browser. Allow notifications for nitsdate in the browser's site settings.";
          return;
        }
        const now = await isOn().catch(() => false);
        state.textContent = note || (now ? "On for this device." : "Off for this device.");
        on.hidden = now;
        off.hidden = !now;
      };
      on.addEventListener("click", async () => {
        on.disabled = true;
        let note = "";
        try { await enable(); } catch (_) { note = "Couldn't turn them on in this browser. Try again later."; }
        on.disabled = false;
        show(note);
      });
      off.addEventListener("click", async () => {
        off.disabled = true;
        try { await disable(); } catch (_) {}
        off.disabled = false;
        show();
      });
      show();
    }
    return { ask };
  })();

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
    let seen = null;   // the last counts, to hear what's new (the first counts are just the starting point)
    const apply = async (d) => {
      const now = { messages: d.messages || 0, notifications: d.notifications || 0, requests: d.requests || 0 };
      if (seen) {
        if (now.messages > seen.messages) Sound.play("message");
        else if (now.notifications > seen.notifications || now.requests > seen.requests) Sound.play("notification");
      }
      seen = now;
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

  /* ---- sounds on/off for this device (notifications page) ---- */
  document.querySelectorAll("[data-sound-toggle]").forEach((box) => {
    box.checked = Sound.on();
    box.addEventListener("change", () => {
      try { localStorage.setItem("sounds", box.checked ? "on" : "off"); } catch (_) {}
      if (box.checked) { Sound.unlock(); setTimeout(() => Sound.play("message", true), 60); }
    });
  });
  document.querySelectorAll("[data-sound-test]").forEach((btn) => btn.addEventListener("click", () => {
    Sound.unlock();
    setTimeout(() => Sound.play(btn.dataset.soundTest, true), 60);
  }));

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
    let cursor = chat.dataset.cursor || "";   // edits and deletes since this server time get fetched
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
    // you're looking at something they wrote: a good moment to offer notifications (never before that)
    if (list.querySelector(".msg.theirs")) setTimeout(Push.ask, 1500);
    list.querySelectorAll("img").forEach((img) => img.addEventListener("load", toBottom, { once: true }));

    const grow = () => { if (!input) return; input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 140) + "px"; };

    // fetches what's new in this chat (and marks it read, so never from a hidden tab)
    let again = false;
    async function poll() {
      if (busy) { again = true; return; }   // a signal came in mid-fetch: fetch once more after
      busy = true;
      try {
        const res = await fetch(chat.dataset.pollUrl + "?after=" + lastId + "&changed=" + cursor,
                                { headers: { "X-Requested-With": "fetch" } });
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
          if (tmp.querySelector(".msg.theirs")) {   // their message arrived
            stopTyping();
            Sound.play("message");
            setTimeout(Push.ask, 1200);
          }
          tmp.querySelectorAll("[data-id]").forEach((node) => {
            if (!list.querySelector('[data-id="' + node.dataset.id + '"]')) {
              list.appendChild(node);
              node.querySelectorAll("img").forEach((img) => img.addEventListener("load", () => stick && toBottom(), { once: true }));
            }
          });
          lastId = Math.max(lastId, data.last_id);
          if (stick) toBottom();
        }
        if (data.cursor) cursor = data.cursor;
        applyChanged(data.changed);
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
    // a message on the page was edited, deleted or removed (or one it quotes was): swap in the new copy
    function applyChanged(html) {
      if (!html) return;
      const tmp = document.createElement("div");
      tmp.innerHTML = html;
      tmp.querySelectorAll("[data-id]").forEach((node) => {
        const old = scroller.querySelector('[data-id="' + node.dataset.id + '"]');
        if (!old || old.outerHTML === node.outerHTML) return;
        old.replaceWith(node);
        // what you were replying to or editing was deleted meanwhile
        if (mode && mode.id === node.dataset.id && !node.hasAttribute(mode.kind === "edit" ? "data-edit" : "data-reply")) clearContext();
      });
    }

    Live.on("chat", (d) => {
      if (String(d.conv) === chat.dataset.conv && !document.hidden) poll();   // hidden: caught up on return
    });

    /* ---- reply, edit, delete: the ⋯ menu on each message, and swiping to reply (like Messenger:
       swipe their message to the right, or yours to the left) ---- */
    const menu = document.getElementById("msg-menu");
    const ctxBar = document.getElementById("composer-context");
    const replyInput = document.getElementById("reply-to");
    const attach = form ? form.querySelector(".attach") : null;
    let menuFor = null;
    let mode = null;          // { kind: "reply" | "edit", id }
    let savedDraft = "";      // what you'd typed before starting an edit

    const closeMenu = () => { menu.hidden = true; menuFor = null; };
    function openMenu(msg, anchor) {
      if (menuFor === msg) return closeMenu();
      menuFor = msg;
      const show = (act, on) => { menu.querySelector('[data-act="' + act + '"]').hidden = !on; };
      show("reply", form && msg.hasAttribute("data-reply"));
      show("edit", form && msg.hasAttribute("data-edit"));
      show("delete", msg.hasAttribute("data-delete"));
      show("report", !!msg.dataset.report);
      if (msg.dataset.report) menu.querySelector('[data-act="report"]').href = msg.dataset.report;
      if (!menu.querySelector("[data-act]:not([hidden])")) return closeMenu();
      menu.hidden = false;
      const r = anchor.getBoundingClientRect(), w = menu.offsetWidth, h = menu.offsetHeight;
      const left = msg.classList.contains("mine") ? r.right - w : r.left;
      const top = r.bottom + 4 + h > window.innerHeight - 8 ? r.top - h - 4 : r.bottom + 4;
      menu.style.left = Math.max(8, Math.min(left, window.innerWidth - w - 8)) + "px";
      menu.style.top = Math.max(8, top) + "px";
      menu.querySelector("[data-act]:not([hidden])").focus();
    }
    document.addEventListener("click", (e) => { if (!menu.hidden && !menu.contains(e.target)) closeMenu(); });
    scroller.addEventListener("scroll", () => { if (!menu.hidden) closeMenu(); });
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      if (!menu.hidden) closeMenu();
      else if (mode) clearContext();
    });

    function jumpTo(id) {
      const target = scroller.querySelector('.msg[data-id="' + id + '"]');
      if (!target) { alertInline("That message is further up. Load earlier messages to see it."); return; }
      target.scrollIntoView({ block: "center", behavior: "smooth" });
      target.classList.remove("flash");
      void target.offsetWidth;   // restart the highlight
      target.classList.add("flash");
    }

    scroller.addEventListener("click", (e) => {
      const more = e.target.closest(".msg-more");
      if (more) { e.stopPropagation(); openMenu(more.closest(".msg"), more); return; }
      const quote = e.target.closest(".msg-quote");
      if (quote) jumpTo(quote.dataset.jump);
    });

    function setContext(kind, msg) {
      if (!form) return;
      if (mode && mode.kind === "edit") input.value = savedDraft;   // switching away from an edit
      mode = { kind, id: msg.dataset.id };
      const text = msg.querySelector(".bubble p:not(.removed)");
      const snippet = text ? text.textContent.trim() : (msg.querySelector(".msg-photo") ? "📷 Photo" : "");
      document.getElementById("context-icon").innerHTML = document.getElementById("tpl-" + kind).innerHTML;
      document.getElementById("context-title").textContent = kind === "edit" ? "Editing your message"
        : "Replying to " + (msg.classList.contains("mine") ? "yourself" : chat.dataset.otherName);
      document.getElementById("context-text").textContent = snippet.length > 90 ? snippet.slice(0, 89) + "…" : snippet;
      ctxBar.hidden = false;
      replyInput.value = kind === "reply" ? msg.dataset.id : "";
      if (attach) attach.hidden = kind === "edit";   // an edit changes the text only
      if (kind === "edit") {
        savedDraft = input.value;
        input.value = msg.dataset.text || "";
        photo.value = "";
        chip.hidden = true;
      }
      grow();
      input.focus();
      if (kind === "edit") input.setSelectionRange(input.value.length, input.value.length);
    }
    function clearContext() {
      if (!mode) return;
      if (mode.kind === "edit") input.value = savedDraft;
      mode = null;
      savedDraft = "";
      ctxBar.hidden = true;
      replyInput.value = "";
      if (attach) attach.hidden = false;
      grow();
    }
    if (form) document.getElementById("context-clear").addEventListener("click", () => { clearContext(); input.focus(); });

    async function deleteMessage(msg) {
      if (!window.confirm("Delete this message? It will say “Message deleted” for both of you. " +
                          "Moderators can still read it if this chat is reported.")) return;
      try {
        const res = await post(chat.dataset.deleteUrl.replace("/0/", "/" + msg.dataset.id + "/"), new FormData());
        if (!res.ok) { alertInline("Couldn't delete it. Try again."); return; }
        if (mode && mode.id === msg.dataset.id) clearContext();
        await poll();
      } catch (_) {
        alertInline("You seem to be offline.");
      }
    }

    menu.addEventListener("click", (e) => {
      const item = e.target.closest("[data-act]");
      const msg = menuFor;
      if (!item || !msg || item.dataset.act === "report") return;   // Report is a normal link
      closeMenu();
      if (item.dataset.act === "delete") deleteMessage(msg);
      else setContext(item.dataset.act, msg);
    });

    // swipe to reply (touch only; a mouse uses the ⋯ menu). .msg has touch-action: pan-y, so up and
    // down still scroll and sideways moves come here.
    const SWIPE_AT = 56, SWIPE_MAX = 80;
    let swipe = null;
    scroller.addEventListener("pointerdown", (e) => {
      if (e.pointerType === "mouse" || !form) return;
      const msg = e.target.closest(".msg[data-reply]");
      if (msg) swipe = { msg, x: e.clientX, y: e.clientY, pull: 0, on: false, id: e.pointerId };
    });
    scroller.addEventListener("pointermove", (e) => {
      if (!swipe || e.pointerId !== swipe.id) return;
      const dx = e.clientX - swipe.x, dy = e.clientY - swipe.y;
      if (!swipe.on) {
        if (Math.abs(dy) > 10 && Math.abs(dy) > Math.abs(dx)) { swipe = null; return; }   // scrolling
        if (Math.abs(dx) < 10) return;
        swipe.on = true;
        swipe.msg.classList.add("swiping");
        try { swipe.msg.setPointerCapture(e.pointerId); } catch (_) {}
      }
      const dir = swipe.msg.classList.contains("mine") ? -1 : 1;   // yours: to the left; theirs: to the right
      swipe.pull = Math.max(0, Math.min(SWIPE_MAX, dx * dir));
      swipe.msg.style.transform = "translateX(" + swipe.pull * dir + "px)";
      swipe.msg.classList.toggle("swipe-ready", swipe.pull >= SWIPE_AT);
    });
    const endSwipe = (e) => {
      if (!swipe || e.pointerId !== swipe.id) return;
      const { msg, pull, on } = swipe;
      swipe = null;
      msg.classList.remove("swiping", "swipe-ready");
      msg.style.transform = "";
      if (!on) return;
      // the lift after a swipe isn't a tap on a link or photo (if the browser sends a click at all)
      const swallow = (ev) => { ev.preventDefault(); ev.stopPropagation(); };
      msg.addEventListener("click", swallow, { capture: true, once: true });
      setTimeout(() => msg.removeEventListener("click", swallow, { capture: true }), 400);
      if (e.type === "pointerup" && pull >= SWIPE_AT) {
        if (navigator.vibrate) try { navigator.vibrate(10); } catch (_) {}
        setContext("reply", msg);
      }
    };
    scroller.addEventListener("pointerup", endSwipe);
    scroller.addEventListener("pointercancel", endSwipe);
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
        const editing = mode && mode.kind === "edit" ? mode.id : null;
        if (!input.value.trim() && (editing || !photo.files.length)) return;
        const btn = form.querySelector(".send-btn");
        btn.disabled = true;
        try {
          let body = new FormData(form), url = chat.dataset.sendUrl;
          if (editing) {
            body = new FormData();
            body.append("text", input.value);
            url = chat.dataset.editUrl.replace("/0/", "/" + editing + "/");
          }
          const res = await post(url, body);
          const data = await res.json().catch(() => ({}));
          if (!res.ok || !data.ok) {
            alertInline(data.error || (editing ? "Couldn't save the edit. Try again." : "Couldn't send. Try again."));
            return;
          }
          if (!editing) input.value = "";
          clearContext();   // after an edit this brings back what you were typing before
          photo.value = "";
          chip.hidden = true;
          grow();
          await poll();
          if (!editing) toBottom();
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
    // a count-up timer from when the search started (Fast queue keeps that time). It counts from the
    // server's figure at page load, so a phone with a wrong clock still shows the right time.
    const clock = document.getElementById("search-elapsed");
    const fast = document.getElementById("fast-queue");
    const loaded = Date.now();
    const base = clock ? +clock.dataset.elapsed || 0 : 0;
    const fastWait = fast ? +fast.dataset.wait || 0 : 0;
    const pad = (n) => String(n).padStart(2, "0");
    const tick = () => {
      const passed = Math.max(0, Math.floor((Date.now() - loaded) / 1000));
      const t = base + passed, h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
      if (clock) clock.textContent = h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
      if (fast && fast.hidden && passed >= fastWait) fast.hidden = false;   // Fast queue opens after 5 minutes
    };
    const check = async () => {
      if (document.hidden) return;
      try {
        const res = await fetch(searching.dataset.statusUrl, { headers: { "X-Requested-With": "fetch" } });
        const data = await res.json();
        if (data.status === "matched" && data.url) {
          Sound.play("message");
          setTimeout(() => { location.href = data.url; }, 600);
        }
        else if (data.status !== "searching") location.reload();
      } catch (_) {}
    };
    // a new search shows "matching" for a few seconds; without a live connection the first check
    // comes right after that. With one, the match arrives as a push (handled below).
    every(check, 4000, { first: +searching.dataset.firstCheck || 4000 });
    tick();
    setInterval(tick, 1000);
  }

  /* ---- a matchmaking search found someone: go there from the match page, or say so elsewhere ---- */
  Live.on("match", (d) => {
    Sound.play("message");
    if (searching || !d.url) { if (d.url) setTimeout(() => { location.href = d.url; }, 600); return; }
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
