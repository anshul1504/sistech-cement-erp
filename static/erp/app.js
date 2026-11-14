(() => {
  const root = document.documentElement;
  // Native details keeps every section link accessible on compact screens.
  const compactSections = window.matchMedia('(max-width: 767.98px)');
  document.querySelectorAll('[data-section-navigation]').forEach(menu => {
    const current = menu.querySelector('[aria-current="page"]');
    const label = menu.querySelector('[data-current-section]');
    if (current && label) label.textContent = current.textContent.trim();
    const resize = () => { menu.open = !compactSections.matches; };
    resize();
    compactSections.addEventListener('change', resize);
  });

  const sidebar = document.getElementById('app-sidebar');
  const backdrop = document.querySelector('.sb-backdrop');
  const toggle = document.querySelector('[data-sidebar-toggle]');
  const desktop = window.matchMedia('(min-width: 1024px)');

  // ----- Tablet / phone drawer -----
  const setDrawer = open => {
    if (!sidebar) return;
    root.classList.toggle('sb-open', open);
    if (backdrop) backdrop.hidden = !open;
    sync();
    if (open) sidebar.querySelector('.sb-link.is-active, a.sb-link')?.focus({ preventScroll: true });
  };
  document.querySelectorAll('[data-sidebar-close]').forEach(el => el.addEventListener('click', () => setDrawer(false)));

  // ----- Desktop collapse (remembered per browser) -----
  const setCollapsed = collapsed => {
    root.classList.toggle('sb-collapsed', collapsed);
    try { localStorage.setItem('sistech.sidebar', collapsed ? 'collapsed' : 'expanded'); } catch (e) { /* storage unavailable */ }
    sync();
  };

  // One header button: collapses the sidebar on desktop, opens the drawer on smaller screens.
  function sync() {
    if (!toggle) return;
    const label = desktop.matches
      ? (root.classList.contains('sb-collapsed') ? 'Expand sidebar' : 'Collapse sidebar')
      : (root.classList.contains('sb-open') ? 'Close menu' : 'Open menu');
    const expanded = desktop.matches ? !root.classList.contains('sb-collapsed') : root.classList.contains('sb-open');
    toggle.setAttribute('aria-label', label);
    toggle.setAttribute('title', label);
    toggle.setAttribute('aria-expanded', String(expanded));
  }
  toggle?.addEventListener('click', () => {
    if (desktop.matches) setCollapsed(!root.classList.contains('sb-collapsed'));
    else setDrawer(!root.classList.contains('sb-open'));
  });
  desktop.addEventListener('change', () => { setDrawer(false); sync(); });
  sync();

  // ----- Header menus open on hover (mouse/trackpad only; click and touch still work) -----
  const canHover = window.matchMedia('(hover: hover) and (pointer: fine)');
  // With hover-open, a click on an already-open header menu must not toggle it closed.
  // Capture on window so this runs before Bootstrap's own click handling.
  window.addEventListener('click', e => {
    const button = e.target.closest?.('.app-header [data-bs-toggle="dropdown"]');
    if (button && canHover.matches && button.getAttribute('aria-expanded') === 'true') { e.preventDefault(); e.stopPropagation(); }
  }, true);
  document.querySelectorAll('.app-header .dropdown').forEach(wrapper => {
    const button = wrapper.querySelector('[data-bs-toggle="dropdown"]');
    if (!button || !window.bootstrap) return;
    const menu = window.bootstrap.Dropdown.getOrCreateInstance(button);
    let closeTimer;
    wrapper.addEventListener('mouseenter', () => {
      if (!canHover.matches) return;
      clearTimeout(closeTimer);
      // Close any other open header menu first.
      document.querySelectorAll('.app-header [data-bs-toggle="dropdown"][aria-expanded="true"]').forEach(other => {
        if (other !== button) window.bootstrap.Dropdown.getOrCreateInstance(other).hide();
      });
      menu.show();
    });
    wrapper.addEventListener('mouseleave', () => {
      if (!canHover.matches) return;
      clearTimeout(closeTimer);
      closeTimer = setTimeout(() => menu.hide(), 220);
    });
  });

  // Keep the active menu item visible inside a long sidebar.
  sidebar?.querySelector('.sb-link.is-active')?.scrollIntoView({ block: 'nearest' });

  // ----- Menu search palette (Ctrl/⌘ + K, or "/") -----
  const palette = document.getElementById('palette');
  const input = palette?.querySelector('[data-palette-input]');
  const list = palette?.querySelector('[data-palette-list]');
  const empty = palette?.querySelector('[data-palette-empty]');
  const entries = [];
  document.querySelectorAll('[data-nav-item]').forEach(a => entries.push({
    label: a.querySelector('.sb-text').textContent.trim(), hint: a.dataset.group, href: a.getAttribute('href'),
    icon: a.querySelector('use')?.getAttribute('href') || '#ic-arrow' }));
  document.querySelectorAll('[data-quick-create]').forEach(a => entries.push({
    label: a.textContent.trim(), hint: 'Create', href: a.getAttribute('href'),
    icon: a.querySelector('use')?.getAttribute('href') || '#ic-plus' }));
  let results = [], selected = 0, lastFocus = null;

  const render = () => {
    const q = input.value.trim().toLowerCase();
    results = entries.filter(e => !q || `${e.label} ${e.hint}`.toLowerCase().includes(q));
    selected = Math.min(selected, Math.max(results.length - 1, 0));
    list.innerHTML = '';
    results.forEach((e, i) => {
      const li = document.createElement('li');
      li.setAttribute('role', 'option');
      li.setAttribute('aria-selected', String(i === selected));
      const a = document.createElement('a');
      a.href = e.href;
      if (i === selected) a.className = 'is-selected';
      const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
      use.setAttribute('href', e.icon); svg.appendChild(use);
      const label = document.createElement('span'); label.textContent = e.label;
      const hint = document.createElement('small'); hint.textContent = e.hint;
      a.append(svg, label, hint);
      a.addEventListener('mousemove', () => { if (selected !== i) { selected = i; render(); } });
      li.appendChild(a); list.appendChild(li);
    });
    empty.hidden = results.length > 0;
    list.querySelector('.is-selected')?.scrollIntoView({ block: 'nearest' });
  };
  const openPalette = () => {
    if (!palette) return;
    lastFocus = document.activeElement;
    palette.hidden = false; input.value = ''; selected = 0; render(); input.focus();
  };
  const closePalette = () => { if (palette && !palette.hidden) { palette.hidden = true; lastFocus?.focus?.({ preventScroll: true }); } };
  document.querySelectorAll('[data-palette-open]').forEach(b => b.addEventListener('click', openPalette));
  palette?.addEventListener('click', e => { if (e.target === palette) closePalette(); });
  input?.addEventListener('input', () => { selected = 0; render(); });
  input?.addEventListener('keydown', e => {
    if (e.key === 'ArrowDown') { e.preventDefault(); selected = Math.min(selected + 1, results.length - 1); render(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); selected = Math.max(selected - 1, 0); render(); }
    else if (e.key === 'Enter' && results[selected]) { e.preventDefault(); window.location.href = results[selected].href; }
  });
  // Show the right shortcut hint for the platform.
  if (!/Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent)) document.querySelectorAll('.hd-search kbd').forEach(k => { k.textContent = 'Ctrl K'; });

  document.addEventListener('keydown', e => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName) || document.activeElement?.isContentEditable;
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); palette?.hidden ? openPalette() : closePalette(); }
    else if (e.key === '/' && !typing && palette?.hidden) { e.preventDefault(); openPalette(); }
    else if (e.key === 'Escape') { if (palette && !palette.hidden) closePalette(); else if (root.classList.contains('sb-open')) setDrawer(false); }
  });
})();

// ----- Toast notifications: auto-dismiss after 3 s, pause on hover, close button -----
(() => {
  const DURATION = {};  // every type closes after 3 s; hovering pauses it
  const dismiss = toast => { if (toast.classList.contains('is-leaving')) return; toast.classList.add('is-leaving'); toast.addEventListener('animationend', () => toast.remove(), { once: true }); setTimeout(() => toast.remove(), 400); };
  const arm = toast => {
    const kind = (toast.className.match(/is-(success|error|warning|info)/) || [])[1];
    const total = DURATION[kind] || 3000, timer = toast.querySelector('.toast-timer');
    let remaining = total, started = Date.now(), handle;
    const run = () => {
      started = Date.now(); handle = setTimeout(() => dismiss(toast), remaining);
      if (timer) { timer.style.transition = 'none'; timer.style.transform = `scaleX(${remaining / total})`; void timer.offsetWidth; timer.style.transition = `transform ${remaining}ms linear`; timer.style.transform = 'scaleX(0)'; }
    };
    const pause = () => { clearTimeout(handle); remaining -= Date.now() - started; if (timer) { const s = getComputedStyle(timer).transform; timer.style.transition = 'none'; timer.style.transform = s; } };
    toast.addEventListener('mouseenter', pause); toast.addEventListener('mouseleave', run);
    toast.addEventListener('focusin', pause); toast.addEventListener('focusout', run);
    toast.querySelector('[data-toast-close]')?.addEventListener('click', () => dismiss(toast));
    run();
  };
  document.querySelectorAll('[data-toast]').forEach((t, i) => setTimeout(() => arm(t), i * 120));
  // Other scripts can raise a toast: window.erpToast('success', 'Saved')
  window.erpToast = (kind, text) => {
    const box = document.querySelector('[data-toasts]'); if (!box) return;
    const titles = { success: 'Success', error: 'Error', warning: 'Warning', info: 'Notice' };
    const el = document.createElement('div'); el.className = `toast-card is-${kind}`; el.setAttribute('role', kind === 'error' ? 'alert' : 'status');
    el.innerHTML = '<span class="toast-bar"></span><div class="toast-body"><strong></strong><p></p></div><button type="button" class="toast-close" aria-label="Dismiss notification" data-toast-close><svg><use href="#ic-close"/></svg></button><span class="toast-timer"></span>';
    el.querySelector('strong').textContent = titles[kind] || 'Notice'; el.querySelector('p').textContent = text;
    box.appendChild(el); arm(el);
  };
})();

// ----- Existing page behaviours -----
document.querySelectorAll('[data-confirm]').forEach(form => form.addEventListener('submit', event => { if (!window.confirm(form.dataset.confirm)) event.preventDefault(); }));
document.getElementById('add-line')?.addEventListener('click', () => {
  const total = document.querySelector('[name="lines-TOTAL_FORMS"]');
  if (Number(total.value) >= 50) return;
  const html = document.getElementById('empty-line').innerHTML.replaceAll('__prefix__', total.value);
  document.getElementById('order-lines').insertAdjacentHTML('beforeend', html);
  total.value = Number(total.value) + 1;
});
const statuses = ['Draft', 'Submitted', 'Approved', 'Completed', 'Cancelled', 'Quality hold', 'Released', 'Planned', 'Pass', 'Fail', 'Available', 'Low stock'];
document.querySelectorAll('.cell-value[data-status]').forEach(el => { if (statuses.includes(el.dataset.status)) el.classList.add('status'); });
// My work bell: counts what is waiting for this user (approvals, PODs, follow-ups); refreshes every 2 minutes.
(() => {
  const wrap = document.querySelector('[data-my-work]');
  if (!wrap) return;
  const badge = wrap.querySelector('.hd-bell-count'), list = wrap.querySelector('.hd-bell-list');
  const esc = s => String(s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const load = () => fetch(wrap.dataset.myWork + '?format=json', {headers: {'Accept': 'application/json'}, credentials: 'same-origin'})
    .then(r => r.ok ? r.json() : null).then(data => {
      if (!data) return;
      badge.hidden = !data.count;
      badge.textContent = data.count > 99 ? '99+' : data.count;
      wrap.querySelector('.hd-bell').setAttribute('aria-label', data.count ? `My work: ${data.count} waiting` : 'My work: nothing waiting');
      list.innerHTML = data.items.length ? data.items.map(i => `<a class="hd-bell-item is-${esc(i.tone)}" href="${esc(i.url)}"><b>${esc(i.count)}</b><span>${esc(i.label)}<small>${esc(i.hint)}</small></span></a>`).join('')
        : '<p class="hd-bell-empty">Nothing waiting for you. 🎉</p>';
    }).catch(() => {});
  load();
  setInterval(() => { if (!document.hidden) load(); }, 120000);
})();
