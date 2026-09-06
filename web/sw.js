// LearnBuddy service worker：收 Web Push + 点击通知跳学习页
// install/activate 立即接管（skipWaiting + claim），避免改版后被旧 SW 卡住
self.addEventListener('install', (e) => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));

// 收到推送 → 弹系统通知（userVisibleOnly 要求必须显示，否则浏览器撤销订阅）
self.addEventListener('push', (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; }
  catch (_) { d = { title: (e.data && e.data.text()) || 'LearnBuddy' }; }
  e.waitUntil(self.registration.showNotification(d.title || 'LearnBuddy', {
    body: d.body || '',
    tag: d.tag || 'itutor',          // 同 tag 新通知盖旧的，不堆积
    data: d.data || {},
    icon: '/assets/cover-language.png',
    badge: '/assets/cover-language.png',
    requireInteraction: false,
  }));
});

// 点击通知 → 聚焦已打开的学习页，否则新开 deep link
self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const data = e.notification.data || {};
  const kind = data.kind || 'daily';
  const url = data.url || '/space';
  e.waitUntil((async () => {
    const all = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    // 按 kind 区分：selfcheck 优先匹配 /self-check/，daily 优先匹配 /instance/
    const matchPrefix = kind === 'selfcheck' ? '/self-check/' : '/instance/';
    for (const c of all) {
      if (c.url.includes(matchPrefix)) { c.focus(); return; }
    }
    return self.clients.openWindow(url);
  })());
});

// 订阅即将失效：通知服务端删旧订阅（部分浏览器触发；iOS 不可靠，靠服务端 410 兜底）
self.addEventListener('pushsubscriptionchange', (e) => {
  e.waitUntil((async () => {
    if (!e.oldSubscription) return;
    try {
      await fetch('/api/push/subscribe', {
        method: 'DELETE',
        body: JSON.stringify({ endpoint: e.oldSubscription.endpoint }),
        headers: { 'Content-Type': 'application/json' },
      });
    } catch (_) { /* 忽略：服务端 410 仍会清理 */ }
  })());
});
