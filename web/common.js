/* LearnBuddy 公共 JS（V0.28 · 产品体验打磨）
   三个页通用：showToast / setButtonLoading / fetchJson
*/
(function (global) {
  'use strict';

  // ==================== Toast ====================
  function ensureToastBox() {
    let box = document.getElementById('toastBox');
    if (!box) {
      box = document.createElement('div');
      box.id = 'toastBox';
      document.body.appendChild(box);
    }
    return box;
  }
  const ICONS = { ok: '✓', err: '✕', warn: '!', info: 'i' };
  function showToast(msg, kind, ms) {
    kind = kind || 'info';
    ms = ms || 3200;
    const box = ensureToastBox();
    const el = document.createElement('div');
    el.className = 'toast ' + kind;
    el.setAttribute('role', kind === 'err' ? 'alert' : 'status');
    el.innerHTML = `<span class="t-icon">${ICONS[kind] || 'i'}</span><span>${escapeHtml(msg)}</span>`;
    box.appendChild(el);
    setTimeout(() => {
      el.classList.add('out');
      setTimeout(() => el.remove(), 240);
    }, ms);
    return el;
  }
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }

  // ==================== LLM / Markdown 安全渲染 ====================
  const MARKDOWN_TAGS = new Set([
    'a', 'blockquote', 'br', 'code', 'del', 'em', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'hr', 'img', 'li', 'ol', 'p', 'pre', 'strong', 'table', 'tbody', 'td', 'th', 'thead', 'tr', 'ul',
  ]);
  const MARKDOWN_ATTRS = {
    a: new Set(['href', 'title']),
    img: new Set(['src', 'alt', 'title']),
    code: new Set(['class']),
    th: new Set(['align']),
    td: new Set(['align']),
  };
  const BLOCKED_MARKDOWN_TAGS = new Set([
    'base', 'button', 'canvas', 'embed', 'form', 'iframe', 'input', 'link', 'math', 'meta',
    'object', 'option', 'script', 'select', 'source', 'style', 'svg', 'textarea', 'video', 'audio',
  ]);

  function safeMarkdownUrl(value, tagName, attrName) {
    const clean = String(value || '').replace(/[\u0000-\u001F\u007F\s]+/g, '').toLowerCase();
    if (!clean) return false;
    if (/^(javascript|vbscript|data):/.test(clean)) return false;
    if (clean.startsWith('#') || clean.startsWith('/') || clean.startsWith('./') || clean.startsWith('../')) return true;
    try {
      const protocol = new URL(value, global.location && global.location.origin).protocol.toLowerCase();
      if (tagName === 'a' && attrName === 'href') return ['http:', 'https:', 'mailto:', 'tel:'].includes(protocol);
      if (tagName === 'img' && attrName === 'src') return ['http:', 'https:'].includes(protocol);
    } catch (e) {
      return false;
    }
    return false;
  }

  function sanitizeHtml(html) {
    const template = document.createElement('template');
    template.innerHTML = String(html || '');
    template.content.querySelectorAll('*').forEach(el => {
      const tagName = el.tagName.toLowerCase();
      if (!MARKDOWN_TAGS.has(tagName)) {
        if (BLOCKED_MARKDOWN_TAGS.has(tagName)) el.remove();
        else el.replaceWith(...el.childNodes);
        return;
      }
      const allowed = MARKDOWN_ATTRS[tagName] || new Set();
      Array.from(el.attributes).forEach(attr => {
        const name = attr.name.toLowerCase();
        if (!allowed.has(name)) {
          el.removeAttribute(attr.name);
          return;
        }
        if ((name === 'href' || name === 'src') && !safeMarkdownUrl(attr.value, tagName, name)) {
          el.removeAttribute(attr.name);
        }
        if (tagName === 'code' && name === 'class' && !/^language-[a-z0-9_+-]+$/i.test(attr.value)) {
          el.removeAttribute(attr.name);
        }
      });
      if (tagName === 'a' && el.hasAttribute('href')) {
        el.setAttribute('rel', 'noopener noreferrer');
      }
      if (tagName === 'img' && el.hasAttribute('src')) {
        el.setAttribute('loading', 'lazy');
      }
    });
    return template.innerHTML;
  }

  function renderSafeMarkdown(source, inline) {
    const text = String(source == null ? '' : source);
    let html = escapeHtml(text);
    try {
      if (global.marked) {
        html = inline && typeof global.marked.parseInline === 'function'
          ? global.marked.parseInline(text)
          : global.marked.parse(text);
      }
    } catch (e) {
      html = escapeHtml(text);
    }
    return sanitizeHtml(html);
  }

  // ==================== Button loading ====================
  function setButtonLoading(btn, on, label) {
    if (!btn) return;
    if (on) {
      if (btn.dataset._orig === undefined) btn.dataset._orig = btn.textContent;
      if (label) btn.textContent = label;
      btn.classList.add('loading');
      btn.setAttribute('aria-busy', 'true');
      btn.disabled = true;
    } else {
      if (btn.dataset._orig !== undefined) btn.textContent = btn.dataset._orig;
      btn.classList.remove('loading');
      btn.removeAttribute('aria-busy');
      btn.disabled = false;
    }
  }

  // ==================== fetch 包装（自动 toast 错误） ====================
  async function fetchJson(url, opts) {
    opts = opts || {};
    try {
      const r = await fetch(url, {
        ...opts,
        headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
        credentials: 'same-origin',
      });
      let body = null;
      try { body = await r.json(); } catch (e) { /* non-JSON */ }
      if (!r.ok) {
        const msg = (body && (body.detail || body.error)) || `请求失败 ${r.status}`;
        showToast(msg, 'err');
        const err = new Error(msg);
        err.status = r.status;
        err.body = body;
        throw err;
      }
      return body;
    } catch (e) {
      // 网络错误（fetch 拒绝）
      if (!e.status) showToast('网络异常，请稍后重试', 'err');
      throw e;
    }
  }

  // ==================== Runtime capabilities ====================
  // One frontend serves both the hosted trial and a personal deployment. Keep
  // edition checks here so product pages do not grow their own env heuristics.
  const RUNTIME_ENDPOINT = '/api/runtime-capabilities';
  const LEGACY_RUNTIME = Object.freeze({
    edition: 'legacy',
    registration: 'enabled',
    quota_mode: 'platform',
    llm_credentials: 'platform',
    llm_configured: null,
    feishu: true,
    openclaw: false,
    backup_restore: false,
    admin_console: true,
    setup_required: false,
    links: Object.freeze({}),
    source: 'fallback',
  });
  let runtimeValue = null;
  let runtimePromise = null;

  function runtimeBool(value, fallback) {
    return typeof value === 'boolean' ? value : fallback;
  }

  function safeRuntimeLink(value) {
    if (!value || typeof value !== 'string') return '';
    try {
      const url = new URL(value, global.location && global.location.origin);
      return ['http:', 'https:'].includes(url.protocol) ? url.href : '';
    } catch (e) {
      return '';
    }
  }

  function normalizeRuntimeCapabilities(payload, source) {
    const raw = payload && typeof payload === 'object' ? payload : {};
    const integrations = raw.integrations && typeof raw.integrations === 'object' ? raw.integrations : {};
    const rawLinks = raw.links && typeof raw.links === 'object' ? raw.links : {};
    const rawRegistration = raw.registration && typeof raw.registration === 'object'
      ? (raw.registration.mode || (raw.registration.enabled === false ? 'disabled' : 'enabled'))
      : raw.registration;
    const registration = ['enabled', 'setup_only', 'disabled'].includes(rawRegistration)
      ? rawRegistration
      : LEGACY_RUNTIME.registration;
    const edition = ['cloud', 'personal'].includes(raw.edition) ? raw.edition : LEGACY_RUNTIME.edition;
    const quotaMode = raw.quota_mode || (raw.quota && raw.quota.mode);
    const llmCredentials = raw.llm_credentials || (raw.llm && raw.llm.credentials);
    return Object.freeze({
      edition,
      registration,
      quota_mode: ['platform', 'provider'].includes(quotaMode) ? quotaMode : LEGACY_RUNTIME.quota_mode,
      llm_credentials: ['platform', 'owner_managed'].includes(llmCredentials)
        ? llmCredentials
        : LEGACY_RUNTIME.llm_credentials,
      llm_configured: typeof raw.llm_configured === 'boolean' ? raw.llm_configured : null,
      feishu: runtimeBool(raw.feishu, runtimeBool(integrations.feishu, LEGACY_RUNTIME.feishu)),
      openclaw: runtimeBool(raw.openclaw, runtimeBool(integrations.openclaw, LEGACY_RUNTIME.openclaw)),
      backup_restore: runtimeBool(raw.backup_restore, LEGACY_RUNTIME.backup_restore),
      admin_console: runtimeBool(raw.admin_console, LEGACY_RUNTIME.admin_console),
      setup_required: runtimeBool(raw.setup_required, false),
      links: Object.freeze({
        github: safeRuntimeLink(rawLinks.github || raw.github_url),
        deploy: safeRuntimeLink(rawLinks.deploy || raw.deploy_url),
        setup: safeRuntimeLink(rawLinks.setup || rawLinks.openclaw_setup || raw.setup_url),
      }),
      source: source || 'server',
    });
  }

  function publishRuntimeCapabilities(value) {
    runtimeValue = value;
    const root = document.documentElement;
    root.dataset.runtimeEdition = value.edition;
    root.dataset.runtimeState = value.source === 'server' ? 'ready' : 'fallback';
    if (typeof global.CustomEvent === 'function') {
      global.dispatchEvent(new CustomEvent('learnbuddy:runtime-ready', { detail: value }));
    }
    return value;
  }

  function loadRuntimeCapabilities(options) {
    options = options || {};
    if (runtimePromise && !options.force) return runtimePromise;
    runtimePromise = (async () => {
      const controller = typeof AbortController === 'function' ? new AbortController() : null;
      const timeout = controller ? setTimeout(() => controller.abort(), options.timeout || 2500) : null;
      try {
        const response = await fetch(RUNTIME_ENDPOINT, {
          cache: 'no-store',
          credentials: 'same-origin',
          signal: controller ? controller.signal : undefined,
        });
        if (!response.ok) throw new Error(`runtime capabilities ${response.status}`);
        return publishRuntimeCapabilities(normalizeRuntimeCapabilities(await response.json(), 'server'));
      } catch (error) {
        // Older servers do not expose this endpoint. Preserve their current UI
        // and let each page show a small compatibility state where useful.
        return publishRuntimeCapabilities(normalizeRuntimeCapabilities(LEGACY_RUNTIME, 'fallback'));
      } finally {
        if (timeout) clearTimeout(timeout);
      }
    })();
    return runtimePromise;
  }

  const LearnBuddyRuntime = Object.freeze({
    endpoint: RUNTIME_ENDPOINT,
    load: loadRuntimeCapabilities,
    get: () => runtimeValue,
    normalize: payload => normalizeRuntimeCapabilities(payload, 'server'),
    isCloud: value => (value || runtimeValue || LEGACY_RUNTIME).edition === 'cloud',
    isPersonal: value => (value || runtimeValue || LEGACY_RUNTIME).edition === 'personal',
  });

  global.showToast = showToast;
  global.setButtonLoading = setButtonLoading;
  global.fetchJson = fetchJson;
  global.sanitizeHtml = sanitizeHtml;
  global.renderSafeMarkdown = renderSafeMarkdown;
  global.LearnBuddyRuntime = LearnBuddyRuntime;
  loadRuntimeCapabilities();
})(window);
