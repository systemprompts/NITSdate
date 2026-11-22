/* nitsdate service worker (served at /sw.js). It only shows push notifications for new messages and
   matches and opens the chat when one is tapped. It caches nothing and never touches other requests. */
const ICON = "{% static 'img/icon-192.png' %}";
const BADGE = "{% static 'img/badge-96.png' %}";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  let d = {};
  try { d = event.data ? event.data.json() : {}; } catch (_) {}
  const url = d.url || "/";
  event.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    const looking = wins.filter((w) => w.focused && w.visibilityState === "visible");
    // already in front of it: the page shows it itself (and plays its sound)
    if (d.quiet_if === "site" && looking.length) return;
    if (d.quiet_if === "page" && looking.some((w) => new URL(w.url).pathname === url)) return;
    await self.registration.showNotification(d.title || "nitsdate", {
      body: d.body || "", tag: d.tag || undefined, renotify: !!d.tag, icon: ICON, badge: BADGE, data: { url },
    });
  })());
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = new URL((event.notification.data && event.notification.data.url) || "/", self.location.origin).href;
  event.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    const same = wins.find((w) => w.url === url);
    if (same) return same.focus();
    const any = wins.find((w) => "navigate" in w);
    if (any) {
      try { await any.focus(); return await any.navigate(url); } catch (_) { /* fall through */ }
    }
    return self.clients.openWindow(url);
  })());
});
