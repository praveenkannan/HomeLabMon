(function () {
  const THEME_KEY = 'homelabmon.theme';
  const MOBILE_QUERY = '(max-width: 900px)';
  const DARK_QUERY = '(prefers-color-scheme: dark)';

  function parseJson(id, fallback) {
    const el = document.getElementById(id);
    if (!el || !el.textContent) return fallback;
    try {
      return JSON.parse(el.textContent);
    } catch (err) {
      return fallback;
    }
  }

  function parseIso(iso) {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return null;
    return d;
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function cssEscape(value) {
    if (window.CSS && typeof window.CSS.escape === 'function') return window.CSS.escape(value);
    return String(value).replace(/[^a-zA-Z0-9_-]/g, '\\$&');
  }

  function formatLocalUtc(iso) {
    const d = parseIso(iso);
    if (!d) return null;
    const local = new Intl.DateTimeFormat(undefined, {
      year: 'numeric',
      month: 'short',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      timeZoneName: 'short'
    }).format(d);
    const utc = new Intl.DateTimeFormat('en-US', {
      timeZone: 'UTC',
      year: 'numeric',
      month: 'short',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false
    }).format(d);
    return { local, utc: `${utc} UTC` };
  }

  function formatHourLabel(iso, fallback) {
    const d = parseIso(iso);
    if (!d) return fallback;
    return new Intl.DateTimeFormat(undefined, {
      hour: '2-digit',
      minute: '2-digit'
    }).format(d);
  }

  function preferredTheme() {
    try {
      const saved = window.localStorage.getItem(THEME_KEY);
      if (saved === 'light' || saved === 'dark' || saved === 'system') return saved;
    } catch (err) {
      return 'system';
    }
    return 'system';
  }

  const root = document.documentElement;
  const body = document.body;
  const mobileQuery = window.matchMedia(MOBILE_QUERY);
  const darkQuery = window.matchMedia(DARK_QUERY);
  const hourly = parseJson('hourly-data', {});
  const restartCaps = parseJson('restart-caps', {});
  const deviceMetrics = parseJson('device-metrics', {});
  const piMetrics = parseJson('pi-metrics-data', {});
  const detail = document.getElementById('hourly-detail');
  const piMetricsEl = document.getElementById('pi-host-metrics');
  const aiCapabilityEl = document.getElementById('ai-capability-shell');
  const themeSelect = setupThemeControl();
  const selectedSection = detail ? detail.closest('section') : null;
  const drawerScrim = ensureDrawerScrim();
  let selectedDevice = deviceFromHash() || '';
  let selectedPanelFlashTimer = null;
  let livePollTimer = null;
  let selectedLiveState = null;
  let selectedLiveSignature = '';

  function supportsMatchMediaListener(query) {
    return query && typeof query.addEventListener === 'function';
  }

  function isMobile() {
    return mobileQuery.matches;
  }

  function resolvedTheme(theme) {
    if (theme === 'system') return darkQuery.matches ? 'dark' : 'light';
    return theme;
  }

  function applyTheme(theme) {
    const next = theme === 'light' || theme === 'dark' || theme === 'system' ? theme : 'system';
    root.dataset.theme = next;
    root.dataset.themeResolved = resolvedTheme(next);
    root.style.colorScheme = root.dataset.themeResolved;
    if (themeSelect && themeSelect.value !== next) {
      themeSelect.value = next;
    }
  }

  function setTheme(theme) {
    try {
      window.localStorage.setItem(THEME_KEY, theme);
    } catch (err) {
      // Ignore storage failures and keep the in-memory preference.
    }
    applyTheme(theme);
  }

  function setupThemeControl() {
    const banner = findSectionByHeading('home ops monitor') || document.querySelector('main.container > section');
    if (!banner) return null;

    let control = banner.querySelector('[data-theme-control], #theme-control, .theme-control');
    if (!control) {
      control = document.createElement('div');
      control.className = 'dashboard-toolbar';
      control.innerHTML = (
        '<div class="theme-control" id="theme-control" data-theme-control>' +
          '<label for="theme-mode-select">Theme</label>' +
          '<select id="theme-mode-select" data-theme-selector aria-label="Theme selector">' +
            '<option value="system">System</option>' +
            '<option value="light">Light</option>' +
            '<option value="dark">Dark</option>' +
          '</select>' +
        '</div>'
      );
      const summaryGrid = banner.querySelector('.summary-grid');
      if (summaryGrid) {
        banner.insertBefore(control, summaryGrid);
      } else {
        banner.appendChild(control);
      }
    }

    const select = control.querySelector('select') || control.querySelector('#theme-mode-select') || control.querySelector('[data-theme-selector]');
    if (!select) return null;
    select.value = preferredTheme();
    select.addEventListener('change', () => setTheme(select.value));
    return select;
  }

  function ensureDrawerScrim() {
    let scrim = document.querySelector('.drawer-scrim');
    if (scrim) {
      scrim.addEventListener('click', closeDrawer);
      return scrim;
    }

    scrim = document.createElement('button');
    scrim.type = 'button';
    scrim.className = 'drawer-scrim';
    scrim.setAttribute('aria-label', 'Close selected device drawer');
    scrim.addEventListener('click', closeDrawer);
    document.body.appendChild(scrim);
    return scrim;
  }

  function closeDrawer() {
    body.dataset.drawerOpen = 'false';
  }

  function openDrawer() {
    if (!selectedDevice) return;
    if (isMobile()) {
      body.dataset.drawerOpen = 'true';
    } else {
      closeDrawer();
    }
  }

  function findSectionByHeading(text) {
    const needle = text.toLowerCase();
    return Array.from(document.querySelectorAll('main.container > section')).find((section) => {
      const heading = section.querySelector('h1, h2');
      return heading && heading.textContent.trim().toLowerCase().includes(needle);
    }) || null;
  }

  function bootstrapSections() {
    const main = document.querySelector('main.container');
    if (main) {
      main.dataset.layout = 'observability-dashboard';
    }

    const sections = Array.from(document.querySelectorAll('main.container > section'));
    sections.forEach((section) => {
      section.classList.add('panel');
    });

    const banner = sections[0];
    if (banner) {
      banner.classList.add('banner', 'dashboard-banner');
      banner.id = banner.id || 'dashboard-banner';
      banner.dataset.panel = 'banner';
    }

    if (piMetricsEl) {
      const piSection = piMetricsEl.closest('section');
      if (piSection) {
        piSection.classList.add('dashboard-pi-metrics');
        piSection.id = piSection.id || 'pi-host-metrics-panel';
        piSection.dataset.panel = 'metrics';
      }
    }

    const cardsSection = findSectionByHeading('device health cards');
    if (cardsSection) {
      cardsSection.classList.add('device-cards-panel');
      cardsSection.id = cardsSection.id || 'device-cards-panel';
      cardsSection.dataset.panel = 'cards';
    }

    if (selectedSection) {
      selectedSection.classList.add('selected-device-panel');
      selectedSection.id = selectedSection.id || 'selected-device-panel';
      selectedSection.dataset.panel = 'selected';
    }

    const weeklySection = findSectionByHeading('weekly health history');
    if (weeklySection) {
      weeklySection.classList.add('dashboard-weekly-panel');
      weeklySection.id = weeklySection.id || 'weekly-health-panel';
      weeklySection.dataset.panel = 'weekly';
    }

    const telemetrySection = findSectionByHeading('telemetry feed');
    if (telemetrySection) {
      telemetrySection.classList.add('dashboard-telemetry-panel');
      telemetrySection.id = telemetrySection.id || 'telemetry-feed-panel';
      telemetrySection.dataset.panel = 'telemetry';
    }

    document.querySelectorAll('.device-card').forEach((card) => {
      card.setAttribute('role', 'button');
      card.setAttribute('tabindex', '0');
      card.setAttribute('aria-selected', 'false');
    });
  }

  function badge(status) {
    if (status === true) return '<span class="status-up">UP</span>';
    if (status === false) return '<span class="status-down">DOWN</span>';
    return '<span class="status-unknown">N/A</span>';
  }

  function heatLabel(heat) {
    const normalized = String(heat || '').toLowerCase();
    if (normalized === 'warm') return 'Warm';
    if (normalized === 'hot') return 'Hot';
    if (normalized === 'normal') return 'Normal';
    return 'Unknown';
  }

  function trendLabel(trend) {
    const normalized = String(trend || '').toLowerCase();
    if (normalized === 'up') return 'Rising';
    if (normalized === 'down') return 'Falling';
    if (normalized === 'flat') return 'Flat';
    return 'Trend';
  }

  function getCard(device) {
    if (!device) return null;
    return document.querySelector(`.device-card[data-device="${cssEscape(device)}"]`);
  }

  function cardText(card, selector, fallback = '') {
    if (!card) return fallback;
    const node = card.querySelector(selector);
    return node ? node.textContent.trim() : fallback;
  }

  function renderHeatBadge(card) {
    if (!card) return '';
    const heat = card.dataset.heat || card.querySelector('[data-heat]')?.dataset.heat;
    if (!heat) return '';
    const label = card.dataset.heatLabel || card.querySelector('[data-heat-label]')?.dataset.heatLabel || heatLabel(heat);
    return `<span class="heat-badge" data-heat="${escapeHtml(heat)}">${escapeHtml(label)}</span>`;
  }

  function renderHeatBadgeFromState(state) {
    if (!state || !state.heat_state) return '';
    const heat = String(state.heat_state).toLowerCase();
    let label = heatLabel(heat);
    if (state.heat_value_c != null && (heat === 'normal' || heat === 'warm' || heat === 'hot')) {
      label = `${label} ${state.heat_value_c} C`;
    } else if (heat === 'unknown') {
      label = 'Temperature unavailable';
    }
    return `<span class="heat-badge" data-heat="${escapeHtml(heat)}">${escapeHtml(label)}</span>`;
  }

  function renderTrendStrip(card) {
    if (!card) return '';
    const existing = card.querySelector('.trend-strip');
    if (existing) return existing.outerHTML;

    const trend = card.dataset.trend || card.querySelector('[data-trend]')?.dataset.trend;
    const seriesText = card.dataset.trendSeries || card.querySelector('[data-trend-series]')?.dataset.trendSeries;
    if (!trend && !seriesText) return '';

    const label = card.dataset.trendLabel || card.querySelector('[data-trend-label]')?.dataset.trendLabel || trendLabel(trend);
    const points = String(seriesText || '')
      .split(',')
      .map((item) => Number(item.trim()))
      .filter((item) => Number.isFinite(item));

    if (points.length > 0) {
      const max = Math.max(...points);
      const min = Math.min(...points);
      const span = max - min || 1;
      const bars = points.slice(-8).map((value) => {
        const height = 4 + Math.round(((value - min) / span) * 12);
        return `<span class="trend-strip__bar" style="height:${height}px"></span>`;
      }).join('');
      return `<span class="trend-strip" data-trend="${escapeHtml(trend || 'flat')}" title="${escapeHtml(label)}"><span class="trend-strip__bars">${bars}</span></span>`;
    }

    return `<span class="trend-strip" data-trend="${escapeHtml(trend || 'flat')}">${escapeHtml(label)}</span>`;
  }

  function selectedStateMarkup(card) {
    const heat = renderHeatBadge(card);
    const trend = renderTrendStrip(card);
    const parts = [heat, trend].filter(Boolean);
    if (parts.length === 0) return '';
    return `<div class="selection-badges">${parts.join('')}</div>`;
  }

  function selectedStateMarkupFromSources(card, liveState) {
    const heat = renderHeatBadgeFromState(liveState) || renderHeatBadge(card);
    const trend = renderTrendStrip(card);
    const parts = [heat, trend].filter(Boolean);
    if (parts.length === 0) return '';
    return `<div class="selection-badges">${parts.join('')}</div>`;
  }

  function stripPrefix(text, prefix) {
    if (!text) return '';
    return text.startsWith(prefix) ? text.slice(prefix.length).trim() : text;
  }

  function selectedMetaMarkup(reason, checked, uptimeMeta) {
    const items = [
      { label: 'Reason', value: reason || '-' },
      { label: 'Last checked', value: stripPrefix(checked, 'Last checked:') || '-' },
      { label: '7d health', value: stripPrefix(uptimeMeta, '7d health:') || '-' }
    ];
    return (
      '<div class="selected-meta-grid">' +
        items.map((item) => (
          '<div class="selected-meta-card">' +
            `<span class="selected-meta-label">${escapeHtml(item.label)}</span>` +
            `<span class="selected-meta-value">${escapeHtml(item.value)}</span>` +
          '</div>'
        )).join('') +
      '</div>'
    );
  }

  function metricSummary(device) {
    return metricSummaryFromMetrics(deviceMetrics[device] || {});
  }

  function metricSummaryFromMetrics(m) {
    const ping = m.ping ? `avg ${escapeHtml(m.ping.avg_ms ?? '-')} ms / loss ${escapeHtml(m.ping.loss_pct ?? '-')}%` : '-';
    const tcp = (m.tcp || []).map((x) => `${escapeHtml(x.port)}:${x.ok ? 'ok' : 'fail'}${x.latency_ms != null ? `@${escapeHtml(x.latency_ms)}ms` : ''}`).join('; ') || '-';
    const http = (m.http || []).map((x) => `${escapeHtml(x.status ?? 'ERR')}${x.latency_ms != null ? `@${escapeHtml(x.latency_ms)}ms` : ''}`).join('; ') || '-';
    const dns = (m.dns || []).map((x) => `${escapeHtml(x.name)}:${x.ok ? 'ok' : 'fail'}${x.latency_ms != null ? `@${escapeHtml(x.latency_ms)}ms` : ''}`).join('; ') || '-';

    return (
      '<div class="probe-metric-grid">' +
        `<div class="probe-metric-card"><span class="probe-metric-label">Ping</span><span class="probe-metric-value">${ping}</span></div>` +
        `<div class="probe-metric-card"><span class="probe-metric-label">TCP</span><span class="probe-metric-value">${tcp}</span></div>` +
        `<div class="probe-metric-card"><span class="probe-metric-label">HTTP</span><span class="probe-metric-value">${http}</span></div>` +
        `<div class="probe-metric-card"><span class="probe-metric-label">DNS</span><span class="probe-metric-value">${dns}</span></div>` +
      '</div>'
    );
  }

  function liveSignature(data) {
    return JSON.stringify({
      status: data.status || '',
      reason: data.reason || '',
      checked_at: data.checked_at || '',
      metrics: data.metrics || {},
      heat_state: data.heat_state || '',
      heat_value_c: data.heat_value_c ?? null
    });
  }

  function triggerSelectedPanelMotion(className, timeoutMs) {
    if (!selectedSection || !selectedDevice) return;
    selectedSection.classList.remove(className);
    void selectedSection.offsetWidth;
    selectedSection.classList.add(className);
    window.setTimeout(() => {
      selectedSection.classList.remove(className);
    }, timeoutMs);
  }

  function renderSelectedDevice(device) {
    if (!detail) return;
    const card = getCard(device);
    const liveState = selectedLiveState && selectedLiveState.device_id === device ? selectedLiveState : null;

    if (!device || !hourly[device]) {
      detail.classList.add('selected-panel-empty');
      detail.classList.add('muted');
      detail.innerHTML = '<div class="selected-panel-empty"><p class="eyebrow">Selected Device</p><h3>No device selected</h3><p class="muted">Click a card to open the right-side panel on desktop or the slide-over drawer on mobile.</p></div>';
      return;
    }

    const display = liveState && liveState.display_name ? liveState.display_name : cardText(card, 'h3', device);
    const slug = liveState && liveState.slug ? liveState.slug : cardText(card, '.subtext', device);
    const status = liveState && liveState.status ? liveState.status : cardText(card, '.pill', 'N/A');
    const reason = liveState && liveState.reason ? liveState.reason : cardText(card, '.muted', 'No recent reason available.');
    const checked = liveState && liveState.checked_at ? `Last checked: ${liveState.checked_at}` : cardText(card, '.meta', '');
    const uptimeMeta = Array.from(card ? card.querySelectorAll('.meta') : []).map((node) => node.textContent.trim()).find((text) => text.startsWith('7d health:')) || '';
    const points = (hourly[device] || []).slice(0, 6);
    const rows = points.map((point) => (
      '<li class="selected-timeline-item">' +
        `<span class="selected-timeline-hour">${escapeHtml(formatHourLabel(point.slot_start, point.hour))}</span>` +
        `<span class="selected-timeline-status">${badge(point.healthy)}</span>` +
        `<span class="selected-timeline-reason">${escapeHtml(point.reason)}</span>` +
      '</li>'
    )).join('');
    const supplemental = selectedStateMarkupFromSources(card, liveState);

    detail.classList.remove('selected-panel-empty', 'muted');
    detail.innerHTML = (
      '<div class="selected-panel-shell">' +
        '<div class="selected-panel-head">' +
          '<div>' +
            '<p class="eyebrow">Selected Device</p>' +
            `<h3>${escapeHtml(display)}</h3>` +
            `<p class="subtext">${escapeHtml(slug)}</p>` +
          '</div>' +
          '<button class="drawer-close-btn" type="button" data-action="close-selected-panel" id="selected-panel-close">Close</button>' +
        '</div>' +
        '<div class="selected-panel-scroll">' +
          '<div class="selected-panel-summary">' +
            '<div class="selected-panel-state">' +
              `<span class="pill ${escapeHtml(statusClass(status))}">${escapeHtml(status)}</span>` +
              supplemental +
            '</div>' +
            '<div class="selected-panel-meta">' +
              `${selectedMetaMarkup(reason, checked, uptimeMeta)}` +
            '</div>' +
          '</div>' +
          '<div class="hourly-detail">' +
            '<h4 class="tw-text-lg tw-font-semibold">Last 6 Hours</h4>' +
            `<ul class="selected-timeline-list">${rows}</ul>` +
            '<h4 class="tw-text-lg tw-font-semibold">Latest Probe Metrics</h4>' +
            `${metricSummaryFromMetrics(liveState && liveState.metrics ? liveState.metrics : (deviceMetrics[device] || {}))}` +
          '</div>' +
        '</div>' +
      '</div>'
    );
  }

  function statusClass(label) {
    const normalized = String(label || '').trim().toLowerCase();
    if (normalized === 'up') return 'status-up';
    if (normalized === 'down') return 'status-down';
    if (normalized === 'disabled') return 'status-disabled';
    return 'status-unknown';
  }

  function updateCardState(device) {
    const cards = Array.from(document.querySelectorAll('.device-card'));
    cards.forEach((card) => {
      const isSelected = card.dataset.device === device;
      card.classList.toggle('card-selected', isSelected);
      card.classList.toggle('card-faded', Boolean(device) && !isSelected);
      card.setAttribute('aria-selected', isSelected ? 'true' : 'false');
    });

    if (device) {
      root.dataset.selectedDevice = device;
    } else {
      delete root.dataset.selectedDevice;
    }
  }

  function syncHash(device) {
    if (!device) return;
    history.replaceState(null, '', `#device=${encodeURIComponent(device)}`);
  }

  function setSelectedDevice(device, options = {}) {
    const next = device && hourly[device] ? device : '';
    selectedDevice = next;
    selectedLiveState = null;
    selectedLiveSignature = '';
    updateCardState(next);
    renderSelectedDevice(next);
    if (selectedSection && next) {
      triggerSelectedPanelMotion('selected-panel-flash', 700);
      window.clearTimeout(selectedPanelFlashTimer);
      selectedPanelFlashTimer = window.setTimeout(() => {
        selectedSection.classList.remove('selected-panel-flash');
      }, 700);
    }

    if (next && options.syncHash !== false) {
      syncHash(next);
    }

    if (next) {
      startSelectedDeviceLivePolling();
      openDrawer();
    } else {
      stopSelectedDeviceLivePolling();
      closeDrawer();
    }
  }

  async function pollSelectedDeviceLive() {
    if (!selectedDevice || document.hidden) return;
    try {
      const resp = await fetch(`/api/v1/devices/${encodeURIComponent(selectedDevice)}/live`, {
        headers: { Accept: 'application/json' },
        cache: 'no-store'
      });
      const payload = await resp.json();
      if (!resp.ok || !payload || !payload.data) return;
      const nextState = payload.data;
      const nextSignature = liveSignature(nextState);
      if (nextSignature === selectedLiveSignature) return;
      selectedLiveState = nextState;
      selectedLiveSignature = nextSignature;
      renderSelectedDevice(selectedDevice);
      triggerSelectedPanelMotion('selected-panel-live', 900);
    } catch (err) {
      // Keep the static dashboard state if live polling fails.
    }
  }

  function stopSelectedDeviceLivePolling() {
    if (livePollTimer) {
      window.clearInterval(livePollTimer);
      livePollTimer = null;
    }
  }

  function startSelectedDeviceLivePolling() {
    stopSelectedDeviceLivePolling();
    if (!selectedDevice) return;
    pollSelectedDeviceLive();
    livePollTimer = window.setInterval(pollSelectedDeviceLive, 2000);
  }

  function deviceFromHash() {
    const raw = window.location.hash || '';
    if (!raw.startsWith('#device=')) return '';
    return decodeURIComponent(raw.slice('#device='.length));
  }

  function openFromHash() {
    const device = deviceFromHash();
    if (device && hourly[device]) {
      setSelectedDevice(device, { syncHash: false });
    } else {
      if (window.location.hash.startsWith('#device=')) {
        history.replaceState(null, '', `${window.location.pathname}${window.location.search}`);
      }
      setSelectedDevice('', { syncHash: false });
    }
  }

  function renderPiMetrics() {
    if (!piMetricsEl) return;
    if (!piMetrics || Object.keys(piMetrics).length === 0) {
      piMetricsEl.textContent = 'Host metrics unavailable in this payload.';
      return;
    }

    const load = piMetrics.load || {};
    const mem = piMetrics.memory || {};
    piMetricsEl.classList.remove('muted');
    piMetricsEl.innerHTML = `<div class="table-wrap"><table><thead><tr><th>Host</th><th>Load (1/5/15)</th><th>Memory</th><th>CPU Temp</th><th>Collected (UTC)</th></tr></thead><tbody><tr><td>${escapeHtml(piMetrics.hostname || '-')}</td><td>${escapeHtml(load.l1 ?? '-')} / ${escapeHtml(load.l5 ?? '-')} / ${escapeHtml(load.l15 ?? '-')}</td><td>${escapeHtml(mem.used_pct ?? '-')}% used (${escapeHtml(mem.available_mb ?? '-')} MB free / ${escapeHtml(mem.total_mb ?? '-')} MB total)</td><td>${piMetrics.temp_c != null ? `${escapeHtml(piMetrics.temp_c)} C` : '-'}</td><td>${escapeHtml(piMetrics.collected_at || '-')}</td></tr></tbody></table></div>`;
  }

  async function renderAiCapability() {
    if (!aiCapabilityEl) return;
    aiCapabilityEl.textContent = 'Checking AI capability...';
    try {
      const resp = await fetch('/api/v1/ai/capability', {
        headers: { Accept: 'application/json' }
      });
      const payload = await resp.json();
      if (!resp.ok || !payload || !payload.data) {
        throw new Error(payload && payload.error ? payload.error.message : 'Capability check failed');
      }
      const data = payload.data;
      const chat = data.chat || {};
      const actions = data.actions || {};
      aiCapabilityEl.classList.remove('muted');
      aiCapabilityEl.innerHTML = (
        '<div class="table-wrap"><table><thead><tr><th>Mode</th><th>Chat</th><th>Reason</th><th>Actions</th></tr></thead>' +
        `<tbody><tr><td>${escapeHtml(data.mode || 'AI_DISABLED')}</td><td>${chat.available ? 'Available' : 'Unavailable'}</td><td>${escapeHtml(data.reason || '-')}</td><td>${actions.confirm_available ? 'Human confirm required' : 'Unavailable'}</td></tr></tbody></table></div>`
      );
    } catch (err) {
      aiCapabilityEl.textContent = `AI unavailable: ${err.message || 'capability endpoint failed'}`;
    }
  }

  function responseMessage(payload, fallback) {
    if (!payload) return fallback;
    if (payload.error && payload.error.message) return payload.error.message;
    if (payload.data && payload.data.status) return payload.data.status;
    return fallback;
  }

  function applyClientTimezone() {
    const updatedLabel = Array.from(document.querySelectorAll('.label')).find((el) => el.textContent.trim().toLowerCase().startsWith('updated'));
    if (updatedLabel) {
      updatedLabel.textContent = 'Updated (Local)';
      const valueEl = updatedLabel.parentElement ? updatedLabel.parentElement.querySelector('.value.small') : null;
      if (valueEl) {
        const fmt = formatLocalUtc(valueEl.textContent.trim());
        if (fmt) {
          valueEl.innerHTML = `${fmt.local}<br><span class="subtext">UTC: ${fmt.utc}</span>`;
        }
      }
    }

    document.querySelectorAll('.meta').forEach((el) => {
      const raw = el.textContent.trim();
      if (!raw.startsWith('Last checked:')) return;
      const iso = raw.replace('Last checked:', '').trim();
      const fmt = formatLocalUtc(iso);
      if (!fmt) return;
      el.innerHTML = `Last checked: ${fmt.local}<br><span class="subtext">UTC: ${fmt.utc}</span>`;
    });

    document.querySelectorAll('table').forEach((table) => {
      const heads = Array.from(table.querySelectorAll('thead th')).map((h) => h.textContent.trim().toLowerCase());
      const idx = heads.indexOf('last checked');
      if (idx === -1) return;
      table.querySelectorAll('tbody tr').forEach((tr) => {
        const td = tr.children[idx];
        if (!td) return;
        const iso = td.textContent.trim();
        const fmt = formatLocalUtc(iso);
        if (!fmt) return;
        td.innerHTML = `${fmt.local}<br><span class="subtext">UTC: ${fmt.utc}</span>`;
      });
    });
  }

  function wireCardInteractions() {
    document.querySelectorAll('.device-card').forEach((card) => {
      card.addEventListener('click', (event) => {
        if (event.target.closest('.restart-btn')) return;
        setSelectedDevice(card.dataset.device);
      });

      card.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        if (event.target.closest('.restart-btn')) return;
        event.preventDefault();
        setSelectedDevice(card.dataset.device);
      });
    });
  }

  function wireRestartButtons() {
    document.querySelectorAll('.restart-btn').forEach((btn) => {
      btn.addEventListener('click', async (event) => {
        event.stopPropagation();
        const device = btn.dataset.device;
        if (!restartCaps[device]) {
          alert(`Restart is not configured for ${device}.`);
          return;
        }
        const reason = prompt(`Reason for restart request for ${device}:`, 'Operator confirmed maintenance action') || 'Operator confirmed maintenance action';
        const proposeResp = await fetch('/api/v1/actions/propose', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Accept: 'application/json'
          },
          body: JSON.stringify({
            device_id: device,
            action: 'restart',
            reason
          })
        });
        const proposePayload = await proposeResp.json();
        if (!proposeResp.ok || !proposePayload.data || !proposePayload.data.action_id) {
          alert(responseMessage(proposePayload, `Unable to queue restart for ${device}.`));
          return;
        }

        const token = prompt('Admin token for action confirmation:');
        if (!token) return;
        const resp = await fetch('/api/v1/actions/confirm', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Accept: 'application/json',
            'X-Admin-Token': token
          },
          body: JSON.stringify({
            action_id: proposePayload.data.action_id
          })
        });
        const payload = await resp.json();
        const message = resp.ok
          ? `Action ${payload.data.action} for ${payload.data.device_id} is ${payload.data.status}. Execution remains human-confirmed.`
          : responseMessage(payload, 'Action confirmation failed.');
        alert(message);
      });
    });
  }

  function syncResponsiveDrawer() {
    if (!selectedDevice) {
      closeDrawer();
      return;
    }
    if (isMobile()) {
      openDrawer();
    } else {
      closeDrawer();
    }
  }

  function bindResponsiveListeners() {
    if (supportsMatchMediaListener(mobileQuery)) {
      mobileQuery.addEventListener('change', syncResponsiveDrawer);
    } else if (typeof mobileQuery.addListener === 'function') {
      mobileQuery.addListener(syncResponsiveDrawer);
    }

    if (supportsMatchMediaListener(darkQuery)) {
      darkQuery.addEventListener('change', () => {
        if (root.dataset.theme === 'system') applyTheme('system');
      });
    } else if (typeof darkQuery.addListener === 'function') {
      darkQuery.addListener(() => {
        if (root.dataset.theme === 'system') applyTheme('system');
      });
    }

    window.addEventListener('hashchange', openFromHash);
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) {
        stopSelectedDeviceLivePolling();
      } else if (selectedDevice) {
        startSelectedDeviceLivePolling();
      }
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') closeDrawer();
    });
    if (detail) {
      detail.addEventListener('click', (event) => {
        if (event.target.closest('[data-action="close-selected-panel"], #selected-panel-close, .drawer-close-btn')) {
          closeDrawer();
        }
      });
    }
  }

  applyTheme(preferredTheme());
  bootstrapSections();
  renderPiMetrics();
  renderAiCapability();
  applyClientTimezone();
  wireCardInteractions();
  wireRestartButtons();
  bindResponsiveListeners();
  openFromHash();
  syncResponsiveDrawer();
})();
