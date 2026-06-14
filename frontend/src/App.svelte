<script>
  import { onMount } from 'svelte';
  import Icon from './Icon.svelte';
  import { buildUsagePeriods, emptyUsageSlice, isUnknownModel, sessionModelName } from './usage.js';

  let state = $state({
    server: { ok: false, url: '', message: 'loading' },
    staleBusy: { thresholdChecks: 10, sessionIds: [] },
    recentWorkspaces: [],
    tasks: [],
    sessions: [],
    sessionsByDirectory: {},
    archivedSessions: [],
    sessionStatus: {},
    permissions: [],
    inbox: { enabled: false },
    notify: { webhooks: [], files: [] },
    metrics: { running: 0, waiting: 0, failed: 0, completedToday: 0, sessionsBusy: 0, sessionsIdle: 0, sessionsRetry: 0 },
    usage: {
      periods: {
        today: emptyUsageSlice(),
        week: emptyUsageSlice(),
        month: emptyUsageSlice(),
        total: emptyUsageSlice(),
      },
    },
  });

  const usagePeriods = [
    { id: 'today', label: 'Today' },
    { id: 'week', label: 'This week' },
    { id: 'month', label: 'This month' },
    { id: 'total', label: 'Total' },
  ];

  const USAGE_PERIOD_PREFS_KEY = 'openloom.prefs.usagePeriod';
  const VALID_USAGE_PERIODS = new Set(usagePeriods.map((period) => period.id));

  function loadUsagePeriodPref() {
    if (typeof localStorage === 'undefined') return 'today';
    const raw = localStorage.getItem(USAGE_PERIOD_PREFS_KEY);
    return VALID_USAGE_PERIODS.has(raw) ? raw : 'today';
  }

  function saveUsagePeriodPref(id) {
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem(USAGE_PERIOD_PREFS_KEY, id);
    }
  }

  function setUsagePeriod(id) {
    usagePeriod = id;
    saveUsagePeriodPref(id);
  }

  let mainView = $state('dashboard');
  let usagePeriod = $state(loadUsagePeriodPref());

  let loading = $state(true);
  let error = $state('');
  let taskError = $state('');
  let taskSubmitting = $state(false);
  let lastRefreshed = $state(0);
  let now = $state(Date.now());
  let selectedTaskId = $state(null);

  let taskWorkspace = $state('');
  let taskCheckInterval = $state(5);
  const AUTO_ACCEPT_PREFS_KEY = 'openloom.prefs.autoAcceptPermissions';

  function loadAutoAcceptPref() {
    if (typeof localStorage === 'undefined') return false;
    const raw = localStorage.getItem(AUTO_ACCEPT_PREFS_KEY);
    if (raw === null) return false;
    return raw === 'true';
  }

  function saveAutoAcceptPref(value) {
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem(AUTO_ACCEPT_PREFS_KEY, value ? 'true' : 'false');
    }
  }

  let taskAutoAcceptPermissions = $state(loadAutoAcceptPref());
  let taskPlan = $state({
    title: '',
    goal: '',
    steps: [{ title: '', acceptance: [] }],
    globalAcceptance: [],
  });
  let taskTarget = $state('workspace');
  let selectedProjectDir = $state('');
  let selectedSessionId = $state('');
  let _autoSessionPicked = false;

  let collapsedDirs = $state(new Set());
  let collapsedDirsInitialized = false;
  $effect(() => {
    if (collapsedDirsInitialized) return;
    if (sortedDirectories.length === 0) return;
    collapsedDirsInitialized = true;
    collapsedDirs = new Set(sortedDirectories);
  });

  let drawerSessionId = $state('');
  let drawerTab = $state('messages');
  let drawerLoading = $state(false);
  let drawerRefreshing = $state(false);
  let drawerError = $state('');
  let drawerMessages = $state([]);
  let drawerDiff = $state([]);
  let drawerLoadedAt = $state(0);
  let drawerMsgExpanded = $state(new Set());

  let drawerTaskId = $state('');
  let drawerTaskTab = $state('overview');

  const intervalPresets = [
    { label: '5m', minutes: 5 },
    { label: '10m', minutes: 10 },
    { label: '15m', minutes: 15 },
    { label: '30m', minutes: 30 },
  ];

  let archivedPopoverOpen = $state(false);

  let folderPickerOpen = $state(false);
  let folderBrowsePath = $state('');
  let folderBrowseParent = $state(null);
  let folderBrowseChildren = $state([]);
  let folderPickerLoading = $state(false);
  let folderPickerError = $state('');

  let confirmDialog = $state(null);
  function askConfirm({ title, message, confirmLabel = 'Confirm', danger = false }) {
    return new Promise((resolve) => {
      confirmDialog = { title, message, confirmLabel, danger, resolve };
    });
  }
  function resolveConfirm(result) {
    if (confirmDialog?.resolve) confirmDialog.resolve(result);
    confirmDialog = null;
  }

  const sortedDirectories = $derived(
    Object.keys(state.sessionsByDirectory).filter((dir) => dir !== '(unknown)').concat(
      Object.keys(state.sessionsByDirectory).includes('(unknown)') ? ['(unknown)'] : []
    )
  );

  const drawerSession = $derived(
    state.sessions.find((session) => session.id === drawerSessionId) || null
  );

  const drawerTask = $derived(
    state.tasks.find((task) => task.id === drawerTaskId) || null
  );

  const archivedRecords = $derived(
    (state.archivedSessions || [])
      .map((a) => ({ id: a.id, title: a.title || a.id.slice(0, 16), directory: a.directory || '' }))
      .sort((a, b) => a.title.toLowerCase().localeCompare(b.title.toLowerCase()))
  );

  const drawerSessionArchived = $derived(
    drawerSession ? state.archivedSessions?.some((a) => a.id === drawerSession.id) : false
  );

  const refreshAgeSeconds = $derived(
    lastRefreshed ? Math.max(0, Math.floor((now - lastRefreshed) / 1000)) : null
  );

  const activeTasks = $derived((state.tasks || []).filter((task) => task.status !== 'archived'));
  const archivedTasks = $derived((state.tasks || []).filter((task) => task.status === 'archived'));

  const inboxSummary = $derived.by(() => {
    const ib = state.inbox;
    if (!ib || !ib.enabled) {
      return { enabled: false, label: 'Off', detail: 'OPENLOOM_INBOX_DIR not set' };
    }
    const dir = ib.directory || '?';
    const poll = ib.pollIntervalSeconds != null ? `${Math.round(ib.pollIntervalSeconds)}s poll` : '';
    const file = ib.filename ? `${ib.filename}` : '';
    const session = ib.defaultSession ? `session:${ib.defaultSession.slice(0, 12)}` : '';
    return {
      enabled: true,
      label: 'Watching',
      detail: `${dir} · ${file}${poll ? ` · ${poll}` : ''}${session ? ` · ${session}` : ''}`,
    };
  });

  const webhookSummary = $derived.by(() => {
    const list = state.notify?.webhooks || [];
    if (list.length === 0) {
      return { enabled: false, label: 'Off', detail: 'no webhook configured' };
    }
    const wh = list[0];
    return {
      enabled: true,
      label: `${list.length} webhook${list.length === 1 ? '' : 's'}`,
      detail: wh.url,
    };
  });

  const fileNotifySummary = $derived.by(() => {
    const list = state.notify?.files || [];
    if (list.length === 0) {
      return { enabled: false, label: 'Off', detail: 'no file sink configured' };
    }
    const fe = list[0];
    return {
      enabled: true,
      label: `${list.length} file sink${list.length === 1 ? '' : 's'}`,
      detail: `${fe.directory} · ${fe.prefix}-*`,
    };
  });

  const selectedTask = $derived(
    drawerTask || state.tasks.find((task) => task.id === selectedTaskId) || null
  );

  const usageBundle = $derived.by(() => {
    if (state.usage?.periods) return state.usage;
    return buildUsagePeriods(state.sessions || [], state.now || Date.now() / 1000);
  });

  const usage = $derived.by(() => {
    const periods = usageBundle.periods;
    if (periods) {
      return periods[usagePeriod] || periods.total || emptyUsageSlice();
    }
    return emptyUsageSlice();
  });

  const tokenBreakdown = $derived.by(() => {
    const t = usage.totalTokens || {};
    const parts = [
      { key: 'input', label: 'Input', value: t.input || 0, tone: 'input' },
      { key: 'output', label: 'Output', value: t.output || 0, tone: 'output' },
      { key: 'reasoning', label: 'Reasoning', value: t.reasoning || 0, tone: 'reasoning' },
      { key: 'cacheRead', label: 'Cache read', value: t.cacheRead || 0, tone: 'cache-read' },
      { key: 'cacheWrite', label: 'Cache write', value: t.cacheWrite || 0, tone: 'cache-write' },
    ];
    const total = parts.reduce((sum, part) => sum + part.value, 0);
    return {
      hasData: total > 0,
      parts: parts.map((part) => ({
        ...part,
        pct: total > 0 ? Math.max(0, (part.value / total) * 100) : 0,
      })),
    };
  });

  const sessionsInSelectedProject = $derived(
    selectedProjectDir ? state.sessionsByDirectory[selectedProjectDir] || [] : []
  );

  $effect(() => {
    if (_autoSessionPicked) return;
    if (selectedSessionId) return;
    if (state.sessions.length === 0) return;
    _autoSessionPicked = true;
    const first = state.sessions[0];
    selectedProjectDir = first.directory || '(unknown)';
    selectedSessionId = first.id;
  });

  $effect(() => {
    if (sortedDirectories.length === 0) return;
    if (!selectedProjectDir || !state.sessionsByDirectory[selectedProjectDir]) {
      selectedProjectDir = sortedDirectories[0];
    }
  });

  $effect(() => {
    const sessions = sessionsInSelectedProject;
    if (!sessions.length) return;
    if (!selectedSessionId || !sessions.some((session) => session.id === selectedSessionId)) {
      selectedSessionId = sessions[0].id;
    }
  });

  function statusClass(status) {
    const value = String(status || '').toLowerCase();
    if (value.includes('archived') || value.includes('deleted') || value.includes('paused')) return 'pill-src';
    if (value === 'retry' || value.includes('wait') || value.includes('permission')) return 'pill-wait';
    if (value.includes('fail') || value.includes('error')) return 'pill-fail';
    if (value === 'idle' || value.includes('complete')) return 'pill-idle';
    if (value === 'busy' || value.includes('pending') || value.includes('running') || value.includes('streaming')) {
      return 'pill-run';
    }
    return 'pill-idle';
  }

  function statusShowsDot(status) {
    const value = String(status || '').toLowerCase();
    return value === 'busy' || value === 'retry' || value.includes('running') || value.includes('wait');
  }

  function taskIntervalLabel(task) {
    const sec = task?.check_interval_seconds || 300;
    if (sec % 60 === 0) return `${sec / 60}m`;
    return `${sec}s`;
  }

  function stepProgressLabel(task) {
    const total = task?.spec?.steps?.length || 0;
    if (!total) return task?.status === 'completed' ? 'Done' : '—';
    const completed = Array.isArray(task?.completed_steps) ? task.completed_steps.length : 0;
    const done = task?.status === 'completed' ? total : Math.min(completed, total);
    return `${done}/${total}`;
  }

  function sessionStatus(session) {
    if (!session?.id) return 'unknown';
    const status = state.sessionStatus?.[session.id];
    if (typeof status === 'string') return status;
    if (status && typeof status === 'object') {
      return status.type || status.status || status.state || 'idle';
    }
    return 'idle';
  }

  function permissionLabel(perm) {
    const tool = perm?.permission || 'tool';
    const patterns = perm?.patterns || [];
    const hint = patterns[0] ? String(patterns[0]) : '';
    return hint ? `${tool}: ${hint}` : String(tool);
  }

  function sessionTitle(sessionId) {
    const session = state.sessions.find((item) => item.id === sessionId);
    return session?.title || sessionId || 'Session';
  }

  function sessionPendingPermissions(sessionId) {
    return (state.permissions || []).filter((perm) => perm.sessionId === sessionId);
  }

  let permissionResponding = $state('');

  async function respondPermission(sessionId, permissionId, response) {
    const key = `${sessionId}:${permissionId}`;
    permissionResponding = key;
    try {
      const res = await fetch(`/api/sessions/${sessionId}/permissions/${permissionId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ response }),
      });
      if (!res.ok) throw new Error(await extractError(res));
      await refresh();
    } catch (err) {
      error = err instanceof Error ? err.message : String(err);
    } finally {
      permissionResponding = '';
    }
  }

  function openSessionDrawerForPermission(sessionId) {
    openSessionDrawer(sessionId, 'messages');
  }

  function directoryStatus(sessions) {
    let hasRetry = false;
    for (const session of sessions) {
      const status = String(sessionStatus(session)).toLowerCase();
      if (status === 'busy' || status.includes('running') || status.includes('streaming')) return 'busy';
      if (status === 'retry' || status.includes('wait') || status.includes('permission')) hasRetry = true;
    }
    return hasRetry ? 'retry' : 'idle';
  }

  function updatedAgeSeconds(item) {
    const ts = item?.last_check_at ?? item?.updated ?? item?.updated_at ?? item?.created ?? item?.at;
    if (!ts) return null;
    const seconds = Math.max(0, Math.floor(Date.now() / 1000 - Number(ts)));
    if (seconds < 60) return `${seconds}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`;
    return `${Math.floor(seconds / 86400)}d`;
  }

  function toggleDir(path) {
    const next = new Set(collapsedDirs);
    if (next.has(path)) next.delete(path);
    else next.add(path);
    collapsedDirs = next;
  }

  function formatCost(value) {
    const n = Number(value) || 0;
    if (n === 0) return '$0.00';
    if (n < 0.01) return `$${n.toFixed(4)}`;
    return `$${n.toFixed(2)}`;
  }

  function formatTokens(value) {
    const n = Number(value) || 0;
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
    return n.toLocaleString('en-US');
  }

  function formatPercent(value) {
    return `${Math.round((Number(value) || 0) * 100)}%`;
  }

  function periodUsage(id) {
    const periods = usageBundle.periods;
    if (periods?.[id]) return periods[id];
    return emptyUsageSlice();
  }

  function periodLabel(id) {
    return usagePeriods.find((item) => item.id === id)?.label || id;
  }

  function sessionUsage(session) {
    if (!session) return null;
    const tokens = session.tokens || {};
    const cache = tokens.cache || {};
    const parsed = {
      input: Number(tokens.input) || 0,
      output: Number(tokens.output) || 0,
      reasoning: Number(tokens.reasoning) || 0,
      cacheRead: Number(cache.read) || 0,
      cacheWrite: Number(cache.write) || 0,
    };
    const model = session.model || {};
    return {
      cost: Number(session.cost) || 0,
      tokens: parsed,
      totalTokens: Object.values(parsed).reduce((sum, n) => sum + n, 0),
      model: sessionModelName(session),
      providerID: model.providerID || '',
      modelID: model.id || model.modelID || '',
    };
  }

  async function openSessionDrawer(sessionId, tab = 'messages') {
    closeTaskDrawer();
    drawerSessionId = sessionId;
    drawerTab = tab;
    drawerError = '';
    drawerMessages = [];
    drawerDiff = [];
    drawerMsgExpanded = new Set();
    drawerLoading = true;
    await loadDrawerData(sessionId);
  }

  async function loadDrawerData(sessionId) {
    drawerLoading = true;
    drawerError = '';
    try {
      const [msgRes, diffRes] = await Promise.all([
        fetch(`/api/sessions/${sessionId}/messages`),
        fetch(`/api/sessions/${sessionId}/diff`)
      ]);
      if (!msgRes.ok) throw new Error(await extractError(msgRes));
      drawerMessages = (await msgRes.json()).messages || [];
      if (diffRes.ok) drawerDiff = (await diffRes.json()).diff || [];
      drawerLoadedAt = Date.now();
    } catch (err) {
      drawerError = err instanceof Error ? err.message : String(err);
    } finally {
      drawerLoading = false;
    }
  }

  async function refreshDrawer() {
    if (!drawerSessionId || drawerRefreshing) return;
    drawerRefreshing = true;
    const started = Date.now();
    try {
      await loadDrawerData(drawerSessionId);
    } finally {
      const wait = Math.max(0, 650 - (Date.now() - started));
      if (wait) await new Promise((resolve) => setTimeout(resolve, wait));
      drawerRefreshing = false;
    }
  }

  function closeDrawer() {
    closeSessionDrawer();
  }

  async function archiveSessionFromDrawer() {
    if (!drawerSessionId) return;
    const sessionId = drawerSessionId;
    try {
      const res = await fetch(`/api/sessions/${sessionId}/archive`, { method: 'POST' });
      if (!res.ok) throw new Error(await extractError(res));
      closeDrawer();
      await refresh();
    } catch (err) {
      drawerError = err instanceof Error ? err.message : String(err);
    }
  }

  async function unarchiveSessionFromPopover(sessionId) {
    try {
      const res = await fetch(`/api/sessions/${sessionId}/archive`, { method: 'DELETE' });
      if (!res.ok) throw new Error(await extractError(res));
      await refresh();
    } catch (err) {
      error = err instanceof Error ? err.message : String(err);
    }
  }

  async function archiveSessionInline(sessionId) {
    try {
      const res = await fetch(`/api/sessions/${sessionId}/archive`, { method: 'POST' });
      if (!res.ok) throw new Error(await extractError(res));
      await refresh();
    } catch (err) {
      error = err instanceof Error ? err.message : String(err);
    }
  }

  async function deleteSessionHard(sessionId) {
    const ok = await askConfirm({
      title: 'Delete session',
      message: 'This will permanently delete the session and all its data. This cannot be undone.',
      confirmLabel: 'Delete',
      danger: true,
    });
    if (!ok) return;
    try {
      const res = await fetch(`/api/sessions/${sessionId}/delete`, { method: 'POST' });
      if (!res.ok) throw new Error(await extractError(res));
      await refresh();
    } catch (err) {
      error = err instanceof Error ? err.message : String(err);
    }
  }

  function pickFirst(obj, keys, fallback = '') {
    if (!obj || typeof obj !== 'object') return fallback;
    for (const key of keys) {
      const v = obj[key];
      if (v !== undefined && v !== null && v !== '') return v;
    }
    return fallback;
  }

  function getMessageInfo(message) {
    if (message?.info && typeof message.info === 'object') return message.info;
    if (message?.metadata && typeof message.metadata === 'object') return message.metadata;
    return null;
  }

  function getMessageParts(message) {
    if (Array.isArray(message?.parts)) return message.parts;
    if (Array.isArray(message?.content)) return message.content;
    return [];
  }

  const MSG_COLLAPSE_LINES = 12;

  function messageLineCount(text) {
    return String(text || '').split('\n').length;
  }

  function isLogLike(text) {
    const lines = String(text || '')
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean);
    if (lines.length >= 10) return true;
    if (lines.length < 3) return false;
    const patterns = [
      /^\d{4}[-/]\d{2}/,
      /^\[/,
      /^time=/,
      /^level=\w+/i,
      /^(GET|POST|PUT|PATCH|DELETE|HEAD)\s/,
      /\|\s*(GET|POST)\s/,
      /oauth:/i,
      /status=\d{3}/,
      /msg="/,
      /invalid signature/i,
    ];
    let hits = 0;
    for (const line of lines.slice(0, 15)) {
      if (patterns.some((pattern) => pattern.test(line))) hits += 1;
    }
    return hits >= 2;
  }

  function shouldCollapseText(text) {
    return messageLineCount(text) > MSG_COLLAPSE_LINES || isLogLike(text);
  }

  function partToBlock(part) {
    if (part == null) return null;
    if (typeof part === 'string') {
      const text = part.trim();
      if (!text) return null;
      return { type: 'text', text, collapsed: shouldCollapseText(text), lines: messageLineCount(text) };
    }
    if (typeof part !== 'object') return null;

    const type = String(part.type || '').toLowerCase();
    if (type === 'text' || type === '') {
      const text = String(part.text || part.content || '').trim();
      if (!text) return null;
      return { type: 'text', text, collapsed: shouldCollapseText(text), lines: messageLineCount(text) };
    }
    if (type === 'reasoning') {
      const text = String(part.text || part.content || '').trim();
      if (!text) return null;
      return { type: 'reasoning', text, collapsed: true, lines: messageLineCount(text) };
    }
    if (type === 'tool' || type === 'tool_use' || type === 'tool-invocation') {
      const name = part.name || part.tool || 'tool';
      let summary = name;
      const args = part.input || part.args || part.arguments;
      if (args && typeof args === 'object') {
        for (const key of ['command', 'filePath', 'path', 'file', 'url', 'query', 'prompt']) {
          const value = args[key];
          if (typeof value === 'string' && value.trim()) {
            summary = `${name} · ${value.trim().slice(0, 160)}`;
            break;
          }
        }
      }
      const detail = args ? JSON.stringify(args, null, 2) : '';
      return { type: 'tool', name, summary, detail, collapsed: true };
    }
    if (type === 'tool_result' || type === 'tool-result') {
      const name = part.name || part.tool || 'tool';
      const output = part.output ?? part.content;
      const text =
        typeof output === 'string'
          ? output.trim()
          : output
            ? JSON.stringify(output, null, 2)
            : '';
      if (!text) {
        return { type: 'tool-result', name, summary: `${name} · (no output)`, text: '', collapsed: true, lines: 0 };
      }
      const firstLine = text.split('\n').map((line) => line.trim()).find(Boolean) || '';
      const summary = firstLine ? `${name} · ${firstLine.slice(0, 120)}` : name;
      return {
        type: 'tool-result',
        name,
        summary,
        text,
        collapsed: shouldCollapseText(text) || text.length > 240,
        lines: messageLineCount(text),
      };
    }
    if (type === 'step-start' || type === 'step-finish') return null;
    return null;
  }

  function buildMessageBlocks(message) {
    if (!message) return [];
    if (typeof message.text === 'string' && message.text.trim()) {
      const text = message.text.trim();
      return [{ type: 'text', text, collapsed: shouldCollapseText(text), lines: messageLineCount(text) }];
    }
    const parts = getMessageParts(message);
    if (parts.length > 0) {
      return parts.map(partToBlock).filter(Boolean);
    }
    const info = getMessageInfo(message);
    if (info) {
      const summary = pickFirst(info, ['summary', 'text', 'content']);
      if (summary) {
        const text = String(summary).trim();
        return [{ type: 'text', text, collapsed: shouldCollapseText(text), lines: messageLineCount(text) }];
      }
    }
    return [];
  }

  function foldLabel(block) {
    if (block.type === 'reasoning') return `Thinking · ${block.lines} lines`;
    if (block.type === 'tool' || block.type === 'tool-result') {
      if (block.lines > 0) return `${block.summary} · ${block.lines} lines`;
      return block.summary;
    }
    if (isLogLike(block.text)) return `Log · ${block.lines} lines`;
    return `Text · ${block.lines} lines`;
  }

  function messageEntryId(message, index) {
    const info = getMessageInfo(message);
    return info?.id || info?.messageID || `msg-${index}`;
  }

  function blockExpandKey(entryId, blockIndex) {
    return `${entryId}:${blockIndex}`;
  }

  function isBlockExpanded(key) {
    return drawerMsgExpanded.has(key);
  }

  function toggleMsgBlock(key) {
    const next = new Set(drawerMsgExpanded);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    drawerMsgExpanded = next;
  }

  function messageRole(message) {
    const info = message?.info;
    if (info && typeof info === 'object' && info.role) return String(info.role).toLowerCase();
    return (message?.role || message?.type || 'message').toLowerCase();
  }

  function messageTimestamp(message) {
    const info = getMessageInfo(message);
    const t = pickFirst(info, ['createdAt', 'created_at', 'time_created']);
    if (typeof t === 'number' && t > 0) {
      const ms = t > 1e12 ? t : t * 1000;
      return new Date(ms).toLocaleTimeString();
    }
    return null;
  }

  function formatTimestamp(value) {
    if (value === undefined || value === null || value === '') return '—';
    if (typeof value === 'number' && value > 0) {
      const ms = value > 1e12 ? value : value * 1000;
      const d = new Date(ms);
      if (Number.isNaN(d.getTime())) return String(value);
      return d.toLocaleString();
    }
    return String(value);
  }

  function getTimeBlock(session) {
    if (session?.time && typeof session.time === 'object') return session.time;
    if (session?.timestamps && typeof session.timestamps === 'object') return session.timestamps;
    return {};
  }

  function metaFields(session) {
    if (!session) return {};
    const t = getTimeBlock(session);
    const fields = {
      ID: session.id || '—',
      Title: session.title || '—',
      Directory: session.directory || session.cwd || '—',
      Status: sessionStatus(session) || '—',
      Created: formatTimestamp(t.created ?? session.created ?? session.createdAt),
      Updated: formatTimestamp(t.updated ?? session.updated ?? session.updatedAt),
    };
    if (typeof session.cost === 'number') fields.Cost = `$${session.cost.toFixed(4)}`;
    return fields;
  }

  async function refresh() {
    try {
      error = '';
      const response = await fetch('/api/state');
      if (!response.ok) throw new Error(await extractError(response));
      state = await response.json();
      const tasks = state.tasks || [];
      if (drawerTaskId && !tasks.some((task) => task.id === drawerTaskId)) {
        drawerTaskId = '';
      }
      if (drawerSessionId && !state.sessions.some((s) => s.id === drawerSessionId)) {
        drawerSessionId = '';
      }
      lastRefreshed = Date.now();
    } catch (err) {
      error = err instanceof Error ? err.message : String(err);
    } finally {
      loading = false;
    }
  }

  let taskStepExpanded = $state(new Set());

  function emptyPlanStep() {
    return { title: '', acceptance: [] };
  }

  function emptyPlan() {
    return {
      title: '',
      goal: '',
      steps: [emptyPlanStep()],
      globalAcceptance: [],
    };
  }

  function derivePlanName(plan) {
    const goalLine = plan.goal.trim().split('\n').map((line) => line.trim()).find(Boolean) || '';
    if (goalLine) {
      return goalLine.length > 60 ? `${goalLine.slice(0, 59)}…` : goalLine;
    }
    const firstStep = plan.steps.find((step) => step.title.trim())?.title.trim() || '';
    if (firstStep) {
      return firstStep.length > 60 ? `${firstStep.slice(0, 59)}…` : firstStep;
    }
    return 'Untitled task';
  }

  function resolvePlanName(plan) {
    const explicit = plan.title?.trim() || '';
    return explicit || derivePlanName(plan);
  }

  function addPlanStep() {
    taskPlan = { ...taskPlan, steps: [...taskPlan.steps, emptyPlanStep()] };
  }

  function removePlanStep(index) {
    const steps = taskPlan.steps.filter((_, i) => i !== index);
    taskPlan = { ...taskPlan, steps: steps.length ? steps : [emptyPlanStep()] };
  }

  function addStepAcceptance(stepIndex) {
    const steps = taskPlan.steps.map((step, i) =>
      i === stepIndex ? { ...step, acceptance: [...step.acceptance, ''] } : step,
    );
    taskPlan = { ...taskPlan, steps };
  }

  function removeStepAcceptance(stepIndex, accIndex) {
    const steps = taskPlan.steps.map((step, i) => {
      if (i !== stepIndex) return step;
      return { ...step, acceptance: step.acceptance.filter((_, j) => j !== accIndex) };
    });
    taskPlan = { ...taskPlan, steps };
  }

  function toggleStepExpanded(index) {
    const next = new Set(taskStepExpanded);
    if (next.has(index)) next.delete(index);
    else next.add(index);
    taskStepExpanded = next;
  }

  function openStepChecks(index) {
    if (!taskStepExpanded.has(index)) {
      taskStepExpanded = new Set([...taskStepExpanded, index]);
    }
    if (taskPlan.steps[index].acceptance.length === 0) {
      addStepAcceptance(index);
    }
  }

  function stepAcceptanceFilledCount(step) {
    return step.acceptance.filter((item) => item.trim()).length;
  }

  const taskIntervalIsCustom = $derived(
    !intervalPresets.some((preset) => preset.minutes === Number(taskCheckInterval)),
  );

  const taskStartSummary = $derived.by(() => {
    const steps = planStepCount(taskPlan);
    const checks = planAcceptanceCount(taskPlan);
    const parts = [];
    if (steps) parts.push(`${steps} step${steps === 1 ? '' : 's'}`);
    if (checks) parts.push(`${checks} check${checks === 1 ? '' : 's'}`);
    return parts.join(' · ');
  });

  function addGlobalAcceptance() {
    taskPlan = { ...taskPlan, globalAcceptance: [...taskPlan.globalAcceptance, ''] };
  }

  function removeGlobalAcceptance(index) {
    taskPlan = {
      ...taskPlan,
      globalAcceptance: taskPlan.globalAcceptance.filter((_, i) => i !== index),
    };
  }

  function planStepCount(plan) {
    return plan.steps.filter((step) => step.title.trim()).length;
  }

  function planAcceptanceCount(plan) {
    const perStep = plan.steps.reduce(
      (sum, step) => sum + step.acceptance.filter((item) => item.trim()).length,
      0,
    );
    const global = plan.globalAcceptance.filter((item) => item.trim()).length;
    return perStep + global;
  }

  function serializePlan(plan) {
    const steps = plan.steps
      .map((step) => ({
        title: step.title.trim(),
        acceptance: step.acceptance.map((item) => item.trim()).filter(Boolean),
      }))
      .filter((step) => step.title);
    const name = resolvePlanName(plan);
    const goal = plan.goal.trim() || name;
    return {
      name,
      goal,
      steps,
      global_acceptance: plan.globalAcceptance.map((item) => item.trim()).filter(Boolean),
    };
  }

  async function createTask({ workspace, checkIntervalMinutes, sessionId = null, target = null } = {}) {
    taskError = '';
    if (!planStepCount(taskPlan)) {
      taskError = 'Plan needs at least one step';
      return;
    }
    const mode = target ?? taskTarget;
    let resolvedSessionId = sessionId;
    if (mode === 'session') {
      resolvedSessionId = sessionId ?? selectedSessionId;
      if (!resolvedSessionId) {
        taskError = 'Select a session';
        return;
      }
    } else if (!(workspace ?? taskWorkspace).trim()) {
      taskError = 'Workspace is required';
      return;
    }
    taskSubmitting = true;
    try {
      const plan = serializePlan(taskPlan);
      const body = {
        plan,
        checkIntervalMinutes: Math.max(5, Number(checkIntervalMinutes ?? taskCheckInterval)),
        workspace: (workspace ?? taskWorkspace).trim() || undefined,
        autoAcceptPermissions: taskAutoAcceptPermissions,
      };
      saveAutoAcceptPref(taskAutoAcceptPermissions);
      if (resolvedSessionId) body.sessionId = resolvedSessionId;
      const response = await fetch('/api/tasks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || response.statusText);
      selectedTaskId = result.taskId;
      taskPlan = emptyPlan();
      taskStepExpanded = new Set();
      taskTarget = 'workspace';
      selectedSessionId = '';
      openTaskDrawer(result.taskId);
      await refresh();
    } catch (err) {
      taskError = err instanceof Error ? err.message : String(err);
    } finally {
      taskSubmitting = false;
    }
  }

  function selectProjectDir(dir) {
    selectedProjectDir = dir;
    const sessions = state.sessionsByDirectory[dir] || [];
    selectedSessionId = sessions[0]?.id || '';
  }

  function useSessionAsTaskTarget(sessionId) {
    if (!sessionId) return;
    const session = state.sessions.find((item) => item.id === sessionId);
    taskTarget = 'session';
    selectedProjectDir = session?.directory || selectedProjectDir || '(unknown)';
    selectedSessionId = sessionId;
    if (session?.directory) taskWorkspace = session.directory;
    closeSessionDrawer();
  }

  function projectName(path) {
    if (!path || path === '(unknown)') return '(unknown)';
    const parts = shortPath(path).split('/').filter(Boolean);
    return parts[parts.length - 1] || path;
  }

  function projectDirLabel(dir) {
    const count = state.sessionsByDirectory[dir]?.length ?? 0;
    if (dir === '(unknown)') return `Unknown (${count})`;
    return `${projectName(dir)} (${count})`;
  }

  function sessionOptionLabel(session) {
    const status = sessionStatus(session);
    const age = updatedAgeSeconds(session);
    const bits = [session.title || session.id];
    if (status !== 'idle') bits.push(status);
    if (age) bits.push(age);
    return bits.join(' · ');
  }

  async function taskAction(action, taskId = null) {
    const id = taskId ?? drawerTask?.id ?? selectedTaskId;
    if (!id) return;
    try {
      const response = await fetch(`/api/tasks/${id}/${action}`, { method: 'POST' });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || response.statusText);
      await refresh();
    } catch (err) {
      error = err instanceof Error ? err.message : String(err);
    }
  }

  async function deleteArchivedTask(taskId = null) {
    const id = taskId ?? drawerTask?.id ?? selectedTaskId;
    if (!id) return;
    const ok = await askConfirm({
      title: 'Delete archived task',
      message: 'Remove this task record permanently? This cannot be undone.',
      confirmLabel: 'Delete',
      danger: true,
    });
    if (!ok) return;
    try {
      const response = await fetch(`/api/tasks/${id}`, { method: 'DELETE' });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || body.error || response.statusText);
      if (drawerTaskId === id) closeTaskDrawer();
      await refresh();
    } catch (err) {
      error = err instanceof Error ? err.message : String(err);
    }
  }

  function openTaskDrawer(taskId) {
    drawerTaskId = taskId;
    selectedTaskId = taskId;
    drawerTaskTab = 'overview';
    closeSessionDrawer();
  }

  function closeTaskDrawer() {
    drawerTaskId = '';
  }

  function closeSessionDrawer() {
    drawerSessionId = '';
  }

  function viewTaskSession(task) {
    const sessionId = task?.active_session_id;
    if (!sessionId) return;
    closeTaskDrawer();
    openSessionDrawer(sessionId);
  }

  function shortPath(path) {
    if (!path) return '';
    if (path === '/') return '/';
    return path.replace(/\/$/, '');
  }

  function selectRecentWorkspace(path) {
    taskWorkspace = path;
  }

  async function removeRecentWorkspace(path, event) {
    event?.stopPropagation?.();
    event?.preventDefault?.();
    try {
      const response = await fetch(`/api/recent-workspaces?path=${encodeURIComponent(path)}`, {
        method: 'DELETE'
      });
      if (!response.ok) throw new Error(await extractError(response));
      await refresh();
    } catch (err) {
      error = err instanceof Error ? err.message : String(err);
    }
  }

  async function loadBrowse(path) {
    folderPickerLoading = true;
    folderPickerError = '';
    try {
      const query = path ? `?path=${encodeURIComponent(path)}` : '';
      const response = await fetch(`/api/browse${query}`);
      if (!response.ok) throw new Error(await extractError(response));
      const body = await response.json();
      folderBrowsePath = body.path;
      folderBrowseParent = body.parent || null;
      folderBrowseChildren = body.children || [];
    } catch (err) {
      folderPickerError = err instanceof Error ? err.message : String(err);
    } finally {
      folderPickerLoading = false;
    }
  }

  async function openFolderBrowser(startPath) {
    folderPickerOpen = true;
    folderPickerError = '';
    await loadBrowse(startPath || taskWorkspace);
  }

  function closeFolderPicker() {
    folderPickerOpen = false;
    folderPickerError = '';
  }

  function confirmFolderPick() {
    if (!folderBrowsePath) return;
    taskWorkspace = folderBrowsePath;
    closeFolderPicker();
  }

  function extractError(res) {
    return res.text().then((text) => {
      try {
        const data = JSON.parse(text);
        if (data && typeof data.detail === 'string') return data.detail;
      } catch {
        // not JSON
      }
      return text || `HTTP ${res.status}`;
    });
  }

  function refreshPollDelayMs() {
    if ((state.permissions || []).length > 0) return 2000;
    const statuses = Object.values(state.sessionStatus || {});
    const hasActive = statuses.some((status) => {
      const value = String(typeof status === 'string' ? status : status?.type || status?.status || '').toLowerCase();
      return value === 'busy' || value === 'retry' || value.includes('running') || value.includes('wait');
    });
    return hasActive ? 5000 : 15000;
  }

  onMount(() => {
    refresh();
    let pollTimer;
    const scheduleRefresh = () => {
      pollTimer = setTimeout(async () => {
        await refresh();
        scheduleRefresh();
      }, refreshPollDelayMs());
    };
    scheduleRefresh();
    const tick = setInterval(() => (now = Date.now()), 1000);
    return () => {
      clearTimeout(pollTimer);
      clearInterval(tick);
    };
  });
</script>

<div class="app">
  <aside class="sidebar">
    <div class="brand">
      <div class="brand-mark"><Icon name="loom" size={12} /></div>
      <div class="brand-copy">
        <span class="brand-name">OpenLoom</span>
        <span class="brand-sub">OpenCode observer</span>
      </div>
    </div>

    <div class="conn-card" class:conn-off={!state.server.ok}>
      <div class="conn-card-top">
        <span class:off={!state.server.ok} class="conn-dot"></span>
        <span>{state.server.ok ? 'Connected' : 'Offline'}</span>
      </div>
      <div class="conn-url mono" title={state.server.url}>{state.server.url.replace(/^https?:\/\//, '') || '—'}</div>
      {#if !state.server.ok}
        <div class="conn-hint">
          <p>Start OpenCode: <code>opencode serve</code></p>
          {#if state.server.message && state.server.message !== 'loading'}
            <p class="conn-hint-detail mono">{state.server.message}</p>
          {/if}
        </div>
      {:else}
        <div class="conn-meta">Updated {refreshAgeSeconds === null ? '—' : `${refreshAgeSeconds}s ago`}</div>
      {/if}
    </div>

    <div class="sidebar-section sidebar-recent">
      <div class="nav-label">Recent Workspaces</div>
      <div class="recent-list">
        {#if state.recentWorkspaces.length === 0}
          <div class="dim empty-mini">Used paths will appear here.</div>
        {:else}
          {#each state.recentWorkspaces as workspace, wsIdx}
            <div class="recent-row" class:active={taskWorkspace === workspace}>
              <button class="recent-item" type="button" title={workspace} onclick={() => selectRecentWorkspace(workspace)}>
                <span class="recent-idx mono">{String(wsIdx + 1).padStart(2, '0')}</span>
                <span class="recent-path mono">{projectName(workspace)}</span>
              </button>
              <button class="recent-remove" type="button" aria-label="Remove from recents" title="Remove" onclick={(event) => removeRecentWorkspace(workspace, event)}>
                <Icon name="x" size={12} />
              </button>
            </div>
          {/each}
        {/if}
      </div>
    </div>

    <div class="sidebar-section sidebar-foot">
      <button class="status-line status-button" type="button" onclick={() => (archivedPopoverOpen = !archivedPopoverOpen)} aria-expanded={archivedPopoverOpen}>
        <span class="dim">Archived sessions</span>
        <span class="mono archived-count">{(state.archivedSessions || []).length}<Icon name="chevron-right" size={10} class="inline-icon" /></span>
      </button>
      {#if archivedPopoverOpen}
        <div class="hidden-popover" role="dialog" aria-label="Archived sessions">
          <div class="hidden-popover-head">
            <span>Archived sessions</span>
            <button class="btn btn-ghost btn-sm" type="button" onclick={() => (archivedPopoverOpen = false)}>Close</button>
          </div>
          {#if archivedRecords.length === 0}
            <div class="dim empty-mini">No archived sessions.</div>
          {:else}
            <ul class="hidden-list">
              {#each archivedRecords as record}
                <li>
                  <div class="hidden-title">{record.title}</div>
                  <div class="mono hidden-id">{record.directory || record.id}</div>
                  <div class="row-actions">
                    <button class="btn btn-ghost btn-sm" type="button" onclick={() => unarchiveSessionFromPopover(record.id)}>Unarchive</button>
                    <button class="btn btn-ghost btn-sm btn-danger" type="button" onclick={() => deleteSessionHard(record.id)}>Delete</button>
                  </div>
                </li>
              {/each}
            </ul>
          {/if}
        </div>
      {/if}
    </div>
  </aside>

  <main class="main">
    <header class="status-bar">
      <div class="status-left">
        <h1>{mainView === 'dashboard' ? 'Dashboard' : 'Activity'}</h1>
        <span class={`pill ${state.server.ok ? 'pill-ok' : 'pill-fail'}`}>
          <span class="pill-dot"></span>{state.server.ok ? 'Healthy' : 'Unavailable'}
        </span>
        {#if state.staleBusy?.sessionIds?.length}
          <span
            class="pill pill-warn"
            title={`Stuck for >{state.staleBusy.thresholdChecks} checks: ${state.staleBusy.sessionIds.join(', ')}`}
          >
            <span class="pill-dot"></span>{state.staleBusy.sessionIds.length} stuck
          </span>
        {/if}
      </div>
      <div class="main-tabs">
        <button type="button" class="main-tab" class:active={mainView === 'dashboard'} onclick={() => (mainView = 'dashboard')}>Dashboard</button>
        <button type="button" class="main-tab" class:active={mainView === 'activity'} onclick={() => (mainView = 'activity')}>Activity</button>
      </div>
    </header>

    {#if error}
      <div class="error" style="padding: 10px 20px;">{error}</div>
    {/if}

    {#if !loading && !state.server.ok}
      <div class="opencode-offline-banner" role="alert">
        <strong>OpenCode is not reachable</strong>
        <p>
          Session monitoring, new tasks, and dispatch need OpenCode on
          <span class="mono">{state.server.url || 'http://127.0.0.1:4096'}</span>.
        </p>
        <p>In another terminal run <code>opencode serve</code>, then refresh this page.</p>
        {#if state.server.message && state.server.message !== 'loading'}
          <p class="mono dim">{state.server.message}</p>
        {/if}
      </div>
    {/if}

    {#if (state.permissions || []).length > 0}
      <div class="permission-banner" role="status">
        <div class="permission-banner-copy">
          <strong>{state.permissions.length} permission request{state.permissions.length === 1 ? '' : 's'} waiting</strong>
          <span class="dim">Review below or open the session drawer to approve.</span>
        </div>
      </div>
      <div class="permission-list-global">
        {#each state.permissions as perm (perm.id)}
          <div class="permission-card">
            <div class="permission-card-main">
              <span class="permission-card-title">{permissionLabel(perm)}</span>
              <span class="mono dim permission-card-session">{sessionTitle(perm.sessionId)}</span>
            </div>
            <div class="permission-card-actions">
              <button
                class="btn btn-ghost btn-sm"
                type="button"
                disabled={permissionResponding === `${perm.sessionId}:${perm.id}`}
                onclick={() => respondPermission(perm.sessionId, perm.id, 'once')}
              >Once</button>
              <button
                class="btn btn-ghost btn-sm"
                type="button"
                disabled={permissionResponding === `${perm.sessionId}:${perm.id}`}
                onclick={() => respondPermission(perm.sessionId, perm.id, 'always')}
              >Always</button>
              <button
                class="btn btn-ghost btn-sm btn-danger"
                type="button"
                disabled={permissionResponding === `${perm.sessionId}:${perm.id}`}
                onclick={() => respondPermission(perm.sessionId, perm.id, 'reject')}
              >Deny</button>
              <button class="btn btn-ghost btn-sm" type="button" onclick={() => openSessionDrawerForPermission(perm.sessionId)}>Session</button>
            </div>
          </div>
        {/each}
      </div>
    {/if}

    {#if mainView === 'dashboard'}
      <section class="dashboard">
        {#if loading}
          <div class="empty">Loading usage data…</div>
        {:else}
          <div class="dash-overview">
            <div class="nav-label">Overview</div>
            <div class="stat-grid dash-stat-grid">
              <div class="stat-card stripe-sessions">
                <span class="stat-label">Sessions</span>
                <span class="stat-value mono">{state.sessions.length}</span>
                <span class="stat-sub">{state.metrics.sessionsBusy || 0} busy · {state.metrics.sessionsRetry || 0} wait</span>
              </div>
              <div class="stat-card stripe-tasks">
                <span class="stat-label">Tasks</span>
                <span class="stat-value mono">{activeTasks.length}</span>
                <span class="stat-sub">{state.metrics.running || 0} running</span>
              </div>
              <div class="stat-card stat-card-accent stripe-tokens">
                <span class="stat-label">{periodLabel(usagePeriod)} tokens</span>
                <span class="stat-value mono">{formatTokens(usage.tokenTotal)}</span>
                <span class="stat-sub">
                  {formatPercent(usage.cacheEfficiency)} cache · {usage.sessionsWithUsage}/{usage.sessionCount} sessions
                </span>
                <span class="stat-est dim mono">Est. {formatCost(usage.totalCost)}</span>
              </div>
              <div class="stat-card stripe-cache">
                <span class="stat-label">Cache read</span>
                <span class="stat-value mono">{formatTokens(usage.totalTokens?.cacheRead || 0)}</span>
                <span class="stat-sub">{formatPercent(usage.cacheEfficiency)} of input+ cache</span>
              </div>
            </div>
          </div>

          <div class="period-summary">
            {#each usagePeriods as period}
              {@const slice = periodUsage(period.id)}
              <button
                type="button"
                class="period-summary-card"
                class:active={usagePeriod === period.id}
                onclick={() => setUsagePeriod(period.id)}
              >
                <span class="period-summary-label">{period.label}</span>
                <span class="period-summary-value mono">{formatTokens(slice.tokenTotal)}</span>
                <span class="period-summary-meta dim mono">
                  Est. {formatCost(slice.totalCost)} · {slice.sessionsWithUsage}/{slice.sessionCount} sessions
                </span>
              </button>
            {/each}
          </div>

          <div class="dash-card dash-token-card">
            <div class="dash-card-head">
              <span class="dash-title">Token breakdown · {periodLabel(usagePeriod)}</span>
              <span class="dash-meta dim">Session totals for active sessions in this window</span>
            </div>
            {#if tokenBreakdown.hasData}
              <div class="token-bar" aria-hidden="true">
                {#each tokenBreakdown.parts as part}
                  {#if part.value > 0}
                    <span class={`token-bar-seg token-${part.tone}`} style={`width: ${part.pct}%`} title={`${part.label}: ${formatTokens(part.value)}`}></span>
                  {/if}
                {/each}
              </div>
              <div class="token-legend">
                {#each tokenBreakdown.parts as part}
                  <div class="token-legend-item">
                    <span class={`token-swatch token-${part.tone}`}></span>
                    <span>{part.label}</span>
                    <span class="mono">{formatTokens(part.value)}</span>
                  </div>
                {/each}
              </div>
            {:else}
              <div class="token-empty dim">No token usage in this window yet.</div>
            {/if}
          </div>

          {#if usage.byModel?.length}
            <div class="group dash-group">
              <div class="group-head">
                <span class="group-title">By model</span>
                <span class="group-count">{usage.byModel.length}</span>
              </div>
              <table>
                <thead>
                  <tr>
                    <th>Model</th>
                    <th>Sessions</th>
                    <th>Input</th>
                    <th>Output</th>
                    <th>Cache read</th>
                    <th>Total tokens</th>
                    <th class="col-est">Est. cost</th>
                  </tr>
                </thead>
                <tbody>
                  {#each usage.byModel as row}
                    <tr>
                      <td class="mono">
                        {#if isUnknownModel(row.model)}
                          <span class="model-unknown" title="OpenCode did not record a model for these sessions">Unknown</span>
                        {:else}
                          {row.model}
                        {/if}
                      </td>
                      <td class="mono">{row.sessionCount}</td>
                      <td class="mono">{formatTokens(row.tokens?.input)}</td>
                      <td class="mono">{formatTokens(row.tokens?.output)}</td>
                      <td class="mono">{formatTokens(row.tokens?.cacheRead)}</td>
                      <td class="mono">{formatTokens(row.totalTokens)}</td>
                      <td class="mono col-est dim">{formatCost(row.cost)}</td>
                    </tr>
                  {/each}
                </tbody>
              </table>
            </div>
          {/if}

          {#if usage.topSessions?.length}
            <div class="group dash-group">
              <div class="group-head"><span class="group-title">Top sessions by tokens</span><span class="group-count">{usage.topSessions.length}</span></div>
              <table>
                <thead>
                  <tr>
                    <th>Title</th>
                    <th>Model</th>
                    <th>Tokens</th>
                    <th class="col-est">Est. cost</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {#each usage.topSessions as row}
                    <tr class="clickable" onclick={() => openSessionDrawer(row.id, 'usage')}>
                      <td><span class="task-title">{row.title}</span><div class="dim mono dash-sub">{shortPath(row.directory)}</div></td>
                      <td class="mono">
                        {#if isUnknownModel(row.model)}
                          <span class="model-unknown" title="OpenCode did not record a model for this session">Unknown</span>
                        {:else}
                          {row.model}
                        {/if}
                      </td>
                      <td class="mono">{formatTokens(row.totalTokens)}</td>
                      <td class="mono col-est dim">{formatCost(row.cost)}</td>
                      <td><button class="btn btn-ghost btn-sm row-btn" type="button" onclick={(e) => { e.stopPropagation(); openSessionDrawer(row.id, 'usage'); }}>Usage</button></td>
                    </tr>
                  {/each}
                </tbody>
              </table>
            </div>
          {/if}

          {#if !usage.byModel?.length && !usage.topSessions?.length}
            <div class="empty dash-empty">
              {#if usage.sessionCount > 0}
                {usage.sessionCount} session{usage.sessionCount === 1 ? '' : 's'} active in {periodLabel(usagePeriod).toLowerCase()}, but no token usage recorded yet.
              {:else}
                No sessions active in {periodLabel(usagePeriod).toLowerCase()}.
              {/if}
            </div>
          {/if}
        {/if}
      </section>
    {:else}
    <section class="table-section">
      {#if loading}
        <div class="empty">Loading OpenLoom state…</div>
      {:else if state.tasks.length === 0 && state.sessions.length === 0}
        <div class="empty">No sessions or tasks yet. Use New Task in the Actions panel.</div>
      {:else}
        <div class="config-summary" role="group" aria-label="Active input and notification channels">
          <div class="config-summary-item" class:config-summary-off={!inboxSummary.enabled}>
            <span class="config-summary-icon" class:config-summary-icon-on={inboxSummary.enabled}><Icon name="inbox" size={14} /></span>
            <div class="config-summary-text">
              <span class="config-summary-label">Inbox <span class="config-summary-status">{inboxSummary.enabled ? 'on' : 'off'}</span></span>
              <span class="config-summary-detail">{inboxSummary.detail}</span>
            </div>
          </div>
          <div class="config-summary-item" class:config-summary-off={!webhookSummary.enabled}>
            <span class="config-summary-icon" class:config-summary-icon-on={webhookSummary.enabled}><Icon name="webhook" size={14} /></span>
            <div class="config-summary-text">
              <span class="config-summary-label">Webhook <span class="config-summary-status">{webhookSummary.enabled ? 'on' : 'off'}</span></span>
              <span class="config-summary-detail">{webhookSummary.detail}</span>
            </div>
          </div>
          <div class="config-summary-item" class:config-summary-off={!fileNotifySummary.enabled}>
            <span class="config-summary-icon" class:config-summary-icon-on={fileNotifySummary.enabled}><Icon name="file-text" size={14} /></span>
            <div class="config-summary-text">
              <span class="config-summary-label">File notify <span class="config-summary-status">{fileNotifySummary.enabled ? 'on' : 'off'}</span></span>
              <span class="config-summary-detail">{fileNotifySummary.detail}</span>
            </div>
          </div>
        </div>
        {#if activeTasks.length > 0}
          <div class="group">
            <div class="group-head"><span class="group-title">Tasks</span><span class="group-count">{activeTasks.length}</span></div>
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Interval</th>
                  <th>Steps</th>
                  <th>Workspace</th>
                  <th>Status</th>
                  <th>Checked</th>
                </tr>
              </thead>
              <tbody>
                {#each activeTasks as task}
                  <tr class:highlight={drawerTaskId === task.id} onclick={() => openTaskDrawer(task.id)}>
                    <td><span class="task-title" title={task.name}>{task.name}</span></td>
                    <td class="mono">{taskIntervalLabel(task)}</td>
                    <td class="mono">{stepProgressLabel(task)}</td>
                    <td class="mono">{shortPath(task.workspace)}</td>
                    <td><span class={`pill ${statusClass(task.status)}`}><span class="pill-dot"></span>{task.status}</span></td>
                    <td class="mono">{updatedAgeSeconds(task) ?? '—'}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
        {/if}

        {#if archivedTasks.length > 0}
          <div class="group">
            <div class="group-head"><span class="group-title">Archived Tasks</span><span class="group-count">{archivedTasks.length}</span></div>
            <table>
              <thead>
                <tr><th>Name</th><th>Interval</th><th>Workspace</th><th>Status</th><th></th></tr>
              </thead>
              <tbody>
                {#each archivedTasks as task}
                  <tr class:highlight={drawerTaskId === task.id} onclick={() => openTaskDrawer(task.id)}>
                    <td><span class="task-title" title={task.name}>{task.name}</span></td>
                    <td class="mono">{taskIntervalLabel(task)}</td>
                    <td class="mono">{shortPath(task.workspace)}</td>
                    <td><span class={`pill ${statusClass(task.status)}`}><span class="pill-dot"></span>{task.status}</span></td>
                    <td>
                      <div class="row-actions">
                        {#if task.active_session_id}
                          <button class="btn btn-ghost btn-sm row-btn" type="button" onclick={(e) => { e.stopPropagation(); viewTaskSession(task); }}>View</button>
                        {/if}
                        <button class="btn btn-ghost btn-sm row-btn btn-danger" type="button" onclick={(e) => { e.stopPropagation(); deleteArchivedTask(task.id); }}>Delete</button>
                      </div>
                    </td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
        {/if}

        {#if sortedDirectories.length > 0}
          <div class="group">
            <div class="group-head">
              <span class="group-title">Sessions</span>
              <span class="group-count">{state.sessions.length} across {sortedDirectories.length} dir{sortedDirectories.length === 1 ? '' : 's'}</span>
            </div>
            {#each sortedDirectories as dir}
              {@const sessions = state.sessionsByDirectory[dir]}
              {@const isCollapsed = collapsedDirs.has(dir)}
              {@const dirStatus = directoryStatus(sessions)}
              <div class="dir-block" class:dir-block-active={dirStatus !== 'idle'}>
                <button class="dir-head" class:dir-head-active={dirStatus !== 'idle'} type="button" onclick={() => toggleDir(dir)} aria-expanded={!isCollapsed}>
                  <span class="dir-chevron" class:collapsed={isCollapsed} aria-hidden="true"></span>
                  <span class="dir-path mono">{shortPath(dir)}</span>
                  {#if dirStatus !== 'idle'}
                    <span class={`pill pill-compact ${statusClass(dirStatus)}`}>
                      {#if statusShowsDot(dirStatus)}<span class="pill-dot"></span>{/if}
                      {dirStatus}
                    </span>
                  {/if}
                  <span class="dir-count">{sessions.length}</span>
                </button>
                {#if !isCollapsed}
                  <table>
                    <thead>
                      <tr><th>Title</th><th>Status</th><th>Updated</th><th></th></tr>
                    </thead>
                    <tbody>
                      {#each sessions as session}
                        <tr class:highlight={drawerSessionId === session.id} onclick={() => openSessionDrawer(session.id)}>
                          <td><span class="task-title">{session.title}</span></td>
                          <td>
                            <span class={`pill ${statusClass(sessionStatus(session))}`}>
                              {#if statusShowsDot(sessionStatus(session))}<span class="pill-dot"></span>{/if}
                              {sessionStatus(session)}
                            </span>
                          </td>
                          <td class="mono">{updatedAgeSeconds(session) ?? '—'}</td>
                          <td class="row-action">
                            <button class="btn btn-ghost btn-sm row-btn" type="button" title="Archive" onclick={(e) => { e.stopPropagation(); archiveSessionInline(session.id); }}>Archive</button>
                          </td>
                        </tr>
                      {/each}
                    </tbody>
                  </table>
                {/if}
              </div>
            {/each}
          </div>
        {/if}
      {/if}
    </section>
    {/if}
  </main>

  <aside class="dispatch">
    <div class="dispatch-head">
      <h2>New Task</h2>
    </div>
    <div class="dispatch-body">
      <div class="task-composer">
        <section class="composer-section">
          <div class="segmented segmented-compact composer-target-tabs">
            <button type="button" class:active={taskTarget === 'workspace'} onclick={() => (taskTarget = 'workspace')}>Workspace</button>
            <button type="button" class:active={taskTarget === 'session'} onclick={() => (taskTarget = 'session')}>Session</button>
          </div>
          {#if taskTarget === 'workspace'}
            <div class="path-row composer-path">
              <input id="task-workspace" class="mono" type="text" bind:value={taskWorkspace} placeholder="/path/to/project" />
              <button class="btn btn-ghost btn-sm path-pick" type="button" title="Choose folder" aria-label="Choose folder" onclick={() => openFolderBrowser(taskWorkspace)}>
                <Icon name="folder" size={14} />
              </button>
            </div>
            {#if state.recentWorkspaces.length}
              <div class="composer-recents">
                {#each state.recentWorkspaces.slice(0, 3) as workspace, idx}
                  <button
                    type="button"
                    class="template-chip"
                    class:active={taskWorkspace === workspace}
                    title={workspace}
                    data-index={String(idx + 1).padStart(2, '0')}
                    onclick={() => selectRecentWorkspace(workspace)}
                  >{projectName(workspace)}</button>
                {/each}
              </div>
            {/if}
          {:else}
            <div class="composer-session-fields">
              <select id="task-project" value={selectedProjectDir} onchange={(e) => selectProjectDir(e.currentTarget.value)}>
                {#each sortedDirectories as dir}
                  {@const sessions = state.sessionsByDirectory[dir]}
                  {#if sessions?.length}
                    <option value={dir}>{projectDirLabel(dir)}</option>
                  {/if}
                {/each}
              </select>
              <select id="task-session" bind:value={selectedSessionId} disabled={sessionsInSelectedProject.length === 0}>
                {#each sessionsInSelectedProject as session}
                  <option value={session.id}>{sessionOptionLabel(session)}</option>
                {/each}
              </select>
            </div>
          {/if}
        </section>

        <div class="composer-divider" role="separator"></div>

        <section class="composer-section composer-plan">
          <input
            id="plan-title"
            class="composer-title"
            type="text"
            bind:value={taskPlan.title}
            placeholder="Title (optional — list label; falls back to goal)"
          />
          <textarea
            id="plan-goal"
            class="composer-goal"
            bind:value={taskPlan.goal}
            placeholder="Goal — what should be true when done?"
            rows="3"
          ></textarea>

          <div class="composer-steps-head">
            <span class="composer-label">Steps</span>
            <button class="btn btn-ghost btn-sm composer-add" type="button" onclick={addPlanStep}>
              <Icon name="plus" size={12} />
              Add
            </button>
          </div>

          <div class="task-step-list">
            {#each taskPlan.steps as step, i}
              {@const expanded = taskStepExpanded.has(i)}
              {@const checkCount = stepAcceptanceFilledCount(step)}
              <div class="task-step" class:expanded>
                <div class="task-step-main">
                  <span class="task-step-num">{String(i + 1).padStart(2, '0')}.</span>
                  <input
                    type="text"
                    bind:value={taskPlan.steps[i].title}
                    placeholder="Describe this step"
                    aria-label="Step {i + 1} description"
                  />
                  <button
                    type="button"
                    class="task-step-checks-btn"
                    class:active={expanded || checkCount > 0}
                    aria-expanded={expanded}
                    onclick={() => (expanded ? toggleStepExpanded(i) : openStepChecks(i))}
                  >
                    {#if checkCount}
                      {checkCount} check{checkCount === 1 ? '' : 's'}
                    {:else}
                      Checks
                    {/if}
                    <Icon name="chevron-down" size={10} class={`task-step-chevron${expanded ? ' open' : ''}`} />
                  </button>
                  <button class="list-remove" type="button" aria-label="Remove step" onclick={() => removePlanStep(i)}>
                    <Icon name="x" size={12} />
                  </button>
                </div>
                {#if expanded}
                  <div class="task-step-checks">
                    {#each step.acceptance as _item, j}
                      <div class="task-check-row">
                        <Icon name="check" size={10} class="task-check-icon" />
                        <input
                          type="text"
                          bind:value={taskPlan.steps[i].acceptance[j]}
                          placeholder="Done when…"
                          aria-label="Step {i + 1} check {j + 1}"
                        />
                        <button
                          class="list-remove"
                          type="button"
                          aria-label="Remove check"
                          onclick={() => removeStepAcceptance(i, j)}
                        ><Icon name="x" size={12} /></button>
                      </div>
                    {/each}
                    <button class="btn btn-ghost btn-sm task-check-add" type="button" onclick={() => addStepAcceptance(i)}>
                      <Icon name="plus" size={10} />
                      Add check
                    </button>
                  </div>
                {/if}
              </div>
            {/each}
          </div>

          <details class="task-global-checks">
            <summary>
              Final checks
              {#if taskPlan.globalAcceptance.filter((item) => item.trim()).length}
                <span class="composer-badge">{taskPlan.globalAcceptance.filter((item) => item.trim()).length}</span>
              {/if}
            </summary>
            <div class="task-global-body">
              {#each taskPlan.globalAcceptance as _item, i}
                <div class="task-check-row">
                  <Icon name="check" size={10} class="task-check-icon" />
                  <input
                    type="text"
                    bind:value={taskPlan.globalAcceptance[i]}
                    placeholder="Whole-task check (e.g. pytest passes)"
                  />
                  <button
                    class="list-remove"
                    type="button"
                    aria-label="Remove check"
                    onclick={() => removeGlobalAcceptance(i)}
                  ><Icon name="x" size={12} /></button>
                </div>
              {/each}
              <button class="btn btn-ghost btn-sm task-check-add" type="button" onclick={addGlobalAcceptance}>
                <Icon name="plus" size={10} />
                Add check
              </button>
            </div>
          </details>
        </section>
      </div>
    </div>
    <div class="dispatch-foot">
      <div class="composer-auto-accept">
        <label class="composer-auto-accept-toggle">
          <input
            type="checkbox"
            class="composer-auto-accept-input"
            bind:checked={taskAutoAcceptPermissions}
            onchange={() => saveAutoAcceptPref(taskAutoAcceptPermissions)}
          />
          <span class="composer-auto-accept-box" aria-hidden="true">
            {#if taskAutoAcceptPermissions}
              <Icon name="check" size={10} class="composer-auto-accept-check" />
            {/if}
          </span>
          <span class="composer-auto-accept-title">Auto-accept permissions</span>
        </label>
        <button
          type="button"
          class="composer-help"
          aria-label="Auto-accept permissions: approve each request once for this session (same as OpenCode Desktop). Turn off to review manually."
        >
          ?
          <span class="composer-help-tip" role="tooltip">
            Approve each request once for this session (same as OpenCode Desktop). Turn off to review manually.
          </span>
        </button>
      </div>
      <div class="composer-foot-interval">
        <span class="composer-label">Watch</span>
        <div class="interval-presets composer-interval">
          {#each intervalPresets as preset}
            <button
              type="button"
              class="interval-preset"
              class:active={Number(taskCheckInterval) === preset.minutes}
              onclick={() => (taskCheckInterval = preset.minutes)}
            >{preset.label}</button>
          {/each}
          <button
            type="button"
            class="interval-preset"
            class:active={taskIntervalIsCustom}
            onclick={() => {
              if (!taskIntervalIsCustom) taskCheckInterval = 20;
            }}
          >Custom</button>
        </div>
        {#if taskIntervalIsCustom}
          <div class="interval-custom composer-interval-custom">
            <input id="task-interval" type="number" min="5" max="120" bind:value={taskCheckInterval} aria-label="Custom interval minutes" />
            <span class="interval-unit">min</span>
          </div>
        {/if}
      </div>
      {#if taskStartSummary}
        <div class="composer-summary dim mono">{taskStartSummary}</div>
      {/if}
      <button
        class="btn btn-primary btn-block"
        type="button"
        disabled={taskSubmitting || !planStepCount(taskPlan)}
        onclick={() => createTask()}
      >
        {taskSubmitting ? 'Starting…' : 'Start Task'}
      </button>
      {#if taskError}
        <div class="error">{taskError}</div>
      {/if}
    </div>
  </aside>
</div>

{#if drawerTask}
  <div class="drawer-mask" onclick={closeTaskDrawer} role="presentation"></div>
  <div class="drawer" role="dialog" aria-label="Task detail">
    <header class="drawer-head">
      <div class="drawer-head-main">
        <div class="drawer-eyebrow">Task</div>
        <h3>{drawerTask.name}</h3>
        <div class="mono drawer-dir">{shortPath(drawerTask.workspace) || '—'}</div>
        <div class="drawer-status-row">
          <span class={`pill ${statusClass(drawerTask.status)}`}>
            {#if statusShowsDot(drawerTask.status)}<span class="pill-dot"></span>{/if}
            {drawerTask.status}
          </span>
          <span class="mono dim">{taskIntervalLabel(drawerTask)}</span>
          <span class="dim">· {stepProgressLabel(drawerTask)} steps</span>
        </div>
      </div>
      <div class="drawer-head-actions">
        <button class="btn btn-ghost btn-sm" type="button" onclick={closeTaskDrawer}>Close</button>
      </div>
    </header>
    <div class="drawer-tabs">
      <button class:active={drawerTaskTab === 'overview'} type="button" onclick={() => (drawerTaskTab = 'overview')}>Overview</button>
      <button class:active={drawerTaskTab === 'log'} type="button" onclick={() => (drawerTaskTab = 'log')}>Check log</button>
      <button class:active={drawerTaskTab === 'spec'} type="button" onclick={() => (drawerTaskTab = 'spec')}>Spec</button>
    </div>
    <div class="drawer-body">
      {#if drawerTaskTab === 'overview'}
        {#if drawerTask.last_summary}
          <div class="meta-row"><div class="meta-label">Last check</div><div class="meta-value">{drawerTask.last_summary}</div></div>
        {/if}
        {#if drawerTask.error}
          <div class="error">{drawerTask.error}</div>
        {/if}
        {#if drawerTask.active_session_id}
          <div class="meta-row">
            <div class="meta-label">Session</div>
            <div class="meta-value mono">{drawerTask.active_session_id}</div>
          </div>
        {/if}
        {#if drawerTask.status === 'waiting' && drawerTask.active_session_id}
          <div class="permission-task-hint">
            <span class="dim">Waiting on OpenCode permission.</span>
            <button class="btn btn-ghost btn-sm" type="button" onclick={() => viewTaskSession(drawerTask)}>Open session</button>
          </div>
        {/if}
        {#if drawerTask.spec?.auto_accept_permissions === false}
          <div class="meta-row">
            <div class="meta-label">Auto-accept</div>
            <div class="meta-value">Off for this task</div>
          </div>
        {/if}
        <div class="meta-row">
          <div class="meta-label">Progress</div>
          <div class="meta-value">{Math.round((drawerTask.progress || 0) * 100)}%</div>
        </div>
        {#if !drawerTask.last_summary && !drawerTask.error}
          <div class="empty">No activity yet.</div>
        {/if}
      {:else if drawerTaskTab === 'log'}
        {#if drawerTask.check_log?.length}
          <ol class="check-log-list">
            {#each drawerTask.check_log.slice().reverse() as entry}
              <li>
                <span class="mono dim">{updatedAgeSeconds({ updated: entry.at }) ?? '—'}</span>
                <span class={`pill ${statusClass(entry.status)}`}>{entry.status}</span>
                <span>{entry.summary}</span>
              </li>
            {/each}
          </ol>
        {:else}
          <div class="empty">No check log entries.</div>
        {/if}
      {:else}
        {#if drawerTask.spec?.goal}
          <div class="meta-row"><div class="meta-label">Goal</div><div class="meta-value">{drawerTask.spec.goal}</div></div>
        {/if}
        {#if drawerTask.spec?.steps?.length}
          <div class="meta-label" style="margin-top: 12px;">Steps</div>
          <ol class="spec-steps">
            {#each drawerTask.spec.steps as step, i}
              {@const stepAcc = drawerTask.spec.step_acceptance?.[i] || []}
              <li>
                {i + 1}. {step}
                {#if stepAcc.length}
                  <ul class="spec-sublist">
                    {#each stepAcc as item}
                      <li>{item}</li>
                    {/each}
                  </ul>
                {/if}
              </li>
            {/each}
          </ol>
        {/if}
        {#if drawerTask.spec?.acceptance?.length}
          <div class="meta-label" style="margin-top: 12px;">Global acceptance</div>
          <ul class="spec-steps">
            {#each drawerTask.spec.acceptance as item}
              <li>{item}</li>
            {/each}
          </ul>
        {/if}
        {#if !drawerTask.spec?.goal && !drawerTask.spec?.steps?.length && !drawerTask.spec?.acceptance?.length}
          <div class="empty">Prompt-only task — no structured spec yet.</div>
        {/if}
      {/if}
    </div>
    <footer class="drawer-foot">
      {#if drawerTask.active_session_id}
        <button class="btn btn-ghost" type="button" onclick={() => viewTaskSession(drawerTask)}>View session</button>
      {/if}
      {#if drawerTask.status === 'paused'}
        <button class="btn btn-ghost" type="button" onclick={() => taskAction('resume', drawerTask.id)}>Resume</button>
      {:else if !['completed', 'failed', 'archived'].includes(drawerTask.status)}
        <button class="btn btn-ghost" type="button" onclick={() => taskAction('pause', drawerTask.id)}>Pause</button>
      {/if}
      {#if !['completed', 'archived'].includes(drawerTask.status)}
        <button class="btn btn-ghost" type="button" onclick={() => taskAction('complete', drawerTask.id)}>Complete</button>
      {/if}
      {#if drawerTask.status !== 'archived'}
        <button class="btn btn-ghost" type="button" onclick={() => taskAction('archive', drawerTask.id)}>Archive</button>
      {:else}
        <button class="btn btn-ghost btn-danger" type="button" onclick={() => deleteArchivedTask(drawerTask.id)}>Delete</button>
      {/if}
    </footer>
  </div>
{/if}

{#if drawerSession}
  <div class="drawer-mask" onclick={closeDrawer} role="presentation"></div>
  <div class="drawer" role="dialog" aria-label="Session detail">
    <header class="drawer-head drawer-head-compact drawer-head-stacked">
      <div class="drawer-head-top">
        <h3 class="drawer-head-title" title={drawerSession.title}>{drawerSession.title}</h3>
        <div class="drawer-head-actions drawer-head-toolbar">
          <span class={`pill ${statusClass(sessionStatus(drawerSession))}`}>
            {#if statusShowsDot(sessionStatus(drawerSession))}<span class="pill-dot"></span>{/if}
            {sessionStatus(drawerSession)}
          </span>
          {#if drawerSessionArchived}
            <span class="pill pill-fail"><span class="pill-dot"></span>archived</span>
          {/if}
          <span class="drawer-head-toolbar-sep" aria-hidden="true"></span>
          <button
            class="btn btn-ghost btn-sm drawer-refresh"
            class:drawer-refresh-busy={drawerRefreshing}
            type="button"
            onclick={refreshDrawer}
            disabled={drawerRefreshing}
            aria-label="Refresh session"
          >
            <Icon name="refresh" size={15} class="drawer-refresh-icon" />
          </button>
          <button class="btn btn-ghost btn-sm" type="button" onclick={closeDrawer}>Close</button>
        </div>
      </div>
      <div class="mono drawer-dir" title={drawerSession.directory || ''}>{shortPath(drawerSession.directory) || '(unknown directory)'}</div>
    </header>
    <div class="drawer-tabs">
      <button class:active={drawerTab === 'messages'} type="button" onclick={() => (drawerTab = 'messages')}>Messages</button>
      <button class:active={drawerTab === 'usage'} type="button" onclick={() => (drawerTab = 'usage')}>Usage</button>
      <button class:active={drawerTab === 'diff'} type="button" onclick={() => (drawerTab = 'diff')}>Diff</button>
      <button class:active={drawerTab === 'meta'} type="button" onclick={() => (drawerTab = 'meta')}>Meta</button>
    </div>
    <div class="drawer-body">
      {#if sessionPendingPermissions(drawerSessionId).length > 0}
        <div class="permission-drawer-block">
          <div class="permission-drawer-head">Pending permissions</div>
          {#each sessionPendingPermissions(drawerSessionId) as perm (perm.id)}
            <div class="permission-card permission-card-drawer">
              <div class="permission-card-main">
                <span class="permission-card-title">{permissionLabel(perm)}</span>
              </div>
              <div class="permission-card-actions">
                <button
                  class="btn btn-ghost btn-sm"
                  type="button"
                  disabled={permissionResponding === `${perm.sessionId}:${perm.id}`}
                  onclick={() => respondPermission(perm.sessionId, perm.id, 'once')}
                >Once</button>
                <button
                  class="btn btn-ghost btn-sm"
                  type="button"
                  disabled={permissionResponding === `${perm.sessionId}:${perm.id}`}
                  onclick={() => respondPermission(perm.sessionId, perm.id, 'always')}
                >Always</button>
                <button
                  class="btn btn-ghost btn-sm btn-danger"
                  type="button"
                  disabled={permissionResponding === `${perm.sessionId}:${perm.id}`}
                  onclick={() => respondPermission(perm.sessionId, perm.id, 'reject')}
                >Deny</button>
              </div>
            </div>
          {/each}
        </div>
      {/if}
      {#if drawerTab === 'usage'}
        {@const usageInfo = sessionUsage(drawerSession)}
        {#if !usageInfo || (usageInfo.totalTokens === 0 && usageInfo.cost === 0)}
          <div class="empty">No usage recorded for this session yet.</div>
        {:else}
          <div class="usage-panel">
            <div class="usage-kpi-row">
              <div class="usage-kpi usage-kpi-primary">
                <span class="usage-kpi-label">Total tokens</span>
                <span class="usage-kpi-value mono">{formatTokens(usageInfo.totalTokens)}</span>
              </div>
              <div class="usage-kpi">
                <span class="usage-kpi-label">Est. cost</span>
                <span class="usage-kpi-value mono dim">{formatCost(usageInfo.cost)}</span>
              </div>
            </div>
            <div class="field">
              <div class="meta-label">Model</div>
              <div class="mono">
                {#if isUnknownModel(usageInfo.model)}
                  <span class="model-unknown" title="OpenCode did not record a model for this session">Unknown</span>
                {:else}
                  {usageInfo.model}
                {/if}
              </div>
            </div>
            <div class="field">
              <div class="meta-label">Token breakdown</div>
              <div class="usage-breakdown">
                {#each [
                  { label: 'Input', value: usageInfo.tokens.input, tone: 'input' },
                  { label: 'Output', value: usageInfo.tokens.output, tone: 'output' },
                  { label: 'Reasoning', value: usageInfo.tokens.reasoning, tone: 'reasoning' },
                  { label: 'Cache read', value: usageInfo.tokens.cacheRead, tone: 'cache-read' },
                  { label: 'Cache write', value: usageInfo.tokens.cacheWrite, tone: 'cache-write' },
                ] as row}
                  <div class="usage-row">
                    <span class={`token-swatch token-${row.tone}`}></span>
                    <span>{row.label}</span>
                    <span class="mono usage-row-value">{formatTokens(row.value)}</span>
                  </div>
                {/each}
              </div>
            </div>
            {#if usageInfo.tokens.cacheRead + usageInfo.tokens.input > 0}
              <div class="field">
                <div class="meta-label">Cache efficiency</div>
                <div class="mono">{formatPercent(usageInfo.tokens.cacheRead / (usageInfo.tokens.cacheRead + usageInfo.tokens.input))}</div>
              </div>
            {/if}
          </div>
        {/if}
      {:else if drawerLoading && drawerMessages.length === 0 && drawerTab !== 'usage'}
        <div class="empty">Loading…</div>
      {:else if drawerError}
        <div class="error">{drawerError}</div>
      {:else if drawerTab === 'messages'}
        {#if drawerMessages.length === 0}
          <div class="empty">No messages yet.</div>
        {:else}
          <ol class="msg-thread">
            {#each drawerMessages as message, msgIndex}
              {@const blocks = buildMessageBlocks(message)}
              {#if blocks.length > 0}
                {@const role = messageRole(message)}
                {@const entryId = messageEntryId(message, msgIndex)}
                {@const ts = messageTimestamp(message)}
                <li class={`msg-bubble msg-bubble-${role}`}>
                  <div class="msg-bubble-head">
                    <span class="msg-bubble-role">{role}</span>
                    {#if ts}<time class="msg-bubble-ts">{ts}</time>{/if}
                  </div>
                  <div class="msg-bubble-body">
                    {#each blocks as block, blockIndex}
                      {@const expandKey = blockExpandKey(entryId, blockIndex)}
                      {@const expanded = isBlockExpanded(expandKey)}
                      {#if block.type === 'text'}
                        {#if block.collapsed && !expanded}
                          <button type="button" class="msg-fold" onclick={() => toggleMsgBlock(expandKey)}>
                            {foldLabel(block)}
                          </button>
                        {:else}
                          <div class={`msg-text${isLogLike(block.text) ? ' msg-log' : ''}`}>{block.text}</div>
                          {#if block.collapsed}
                            <button type="button" class="msg-fold msg-fold-inline" onclick={() => toggleMsgBlock(expandKey)}>Show less</button>
                          {/if}
                        {/if}
                      {:else if block.type === 'reasoning'}
                        {#if !expanded}
                          <button type="button" class="msg-fold msg-fold-muted" onclick={() => toggleMsgBlock(expandKey)}>
                            {foldLabel(block)}
                          </button>
                        {:else}
                          <div class="msg-text msg-reasoning">{block.text}</div>
                          <button type="button" class="msg-fold msg-fold-inline" onclick={() => toggleMsgBlock(expandKey)}>Hide thinking</button>
                        {/if}
                      {:else if block.type === 'tool'}
                        {#if !expanded}
                          <button type="button" class="msg-fold msg-fold-tool" onclick={() => toggleMsgBlock(expandKey)}>
                            {block.summary}
                          </button>
                        {:else}
                          {#if block.detail}
                            <pre class="msg-pre">{block.detail}</pre>
                          {:else}
                            <div class="msg-text dim">{block.summary}</div>
                          {/if}
                          <button type="button" class="msg-fold msg-fold-inline" onclick={() => toggleMsgBlock(expandKey)}>Hide tool</button>
                        {/if}
                      {:else if block.type === 'tool-result'}
                        {#if block.collapsed && !expanded}
                          <button type="button" class="msg-fold msg-fold-tool" onclick={() => toggleMsgBlock(expandKey)}>
                            {foldLabel(block)}
                          </button>
                        {:else if block.text}
                          <pre class="msg-pre">{block.text}</pre>
                          {#if block.collapsed}
                            <button type="button" class="msg-fold msg-fold-inline" onclick={() => toggleMsgBlock(expandKey)}>Show less</button>
                          {/if}
                        {:else}
                          <div class="msg-text dim">{block.summary}</div>
                        {/if}
                      {/if}
                    {/each}
                  </div>
                </li>
              {/if}
            {/each}
          </ol>
        {/if}
      {:else if drawerTab === 'diff'}
        {#if drawerDiff.length === 0}
          <div class="empty">No diff recorded.</div>
        {:else}
          <ul class="diff-list">
            {#each drawerDiff as file}
              {@const filePath = file.file || file.path || file.filename || '(unnamed file)'}
              <li>
                <div class="diff-file-head"><span class="mono diff-file-path">{filePath}</span></div>
                {#if file.patch || file.diff}<pre class="diff-block">{file.patch || file.diff}</pre>{/if}
              </li>
            {/each}
          </ul>
        {/if}
      {:else}
        <div class="meta-grid">
          {#each Object.entries(metaFields(drawerSession)) as [label, value]}
            <div class="meta-row">
              <div class="meta-label">{label}</div>
              <div class="meta-value mono">{value}</div>
            </div>
          {/each}
        </div>
      {/if}
    </div>
    <footer class="drawer-foot">
      <button class="btn btn-ghost" type="button" onclick={() => useSessionAsTaskTarget(drawerSessionId)}>Use for Task</button>
      {#if drawerSessionArchived}
        <button class="btn btn-ghost" type="button" onclick={() => unarchiveSessionFromPopover(drawerSessionId)}>Unarchive</button>
      {:else}
        <button class="btn btn-ghost" type="button" onclick={archiveSessionFromDrawer}>Archive</button>
      {/if}
    </footer>
  </div>
{/if}

{#if folderPickerOpen}
  <div class="modal-mask" onclick={closeFolderPicker} role="presentation"></div>
  <div class="modal folder-picker-modal" role="dialog" aria-modal="true" aria-label="Select workspace folder">
    <h3 class="modal-title">Select workspace</h3>
    <div class="folder-picker-path mono" title={folderBrowsePath}>{folderBrowsePath || '—'}</div>
    {#if folderPickerError}<div class="error">{folderPickerError}</div>{/if}
    {#if folderPickerLoading}
      <div class="dim folder-picker-status">Loading…</div>
    {:else}
      <ul class="folder-picker-list">
        {#if folderBrowseParent}
          <li>
            <button class="folder-picker-item" type="button" onclick={() => loadBrowse(folderBrowseParent)}>
              <Icon name="corner-up-left" size={14} class="folder-picker-icon" /><span>Parent folder</span>
            </button>
          </li>
        {/if}
        {#each folderBrowseChildren as child}
          <li>
            <button class="folder-picker-item" type="button" onclick={() => loadBrowse(child.path)}>
              <Icon name="folder" size={14} class="folder-picker-icon" /><span>{child.name}</span>
            </button>
          </li>
        {/each}
      </ul>
    {/if}
    <div class="folder-picker-foot">
      <button class="btn btn-primary folder-picker-select" type="button" onclick={confirmFolderPick} disabled={!folderBrowsePath}>Select this folder</button>
      <button class="btn btn-ghost folder-picker-cancel" type="button" onclick={closeFolderPicker}>Cancel</button>
    </div>
  </div>
{/if}

{#if confirmDialog}
  <div class="modal-mask" onclick={() => resolveConfirm(false)} role="presentation"></div>
  <div class="modal" role="dialog" aria-modal="true" aria-label={confirmDialog.title}>
    <h3 class="modal-title">{confirmDialog.title}</h3>
    <p class="modal-message">{confirmDialog.message}</p>
    <div class="modal-actions">
      <button class="btn btn-ghost" type="button" onclick={() => resolveConfirm(false)}>Cancel</button>
      <button class={`btn ${confirmDialog.danger ? 'btn-danger-solid' : 'btn-primary'}`} type="button" onclick={() => resolveConfirm(true)}>{confirmDialog.confirmLabel}</button>
    </div>
  </div>
{/if}

<style>
  .check-log-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .check-log-list li {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
  }
  .spec-steps {
    margin: 6px 0 0;
    padding-left: 18px;
    color: var(--text, #ddd);
  }
  .spec-sublist {
    margin: 4px 0 0;
    padding-left: 16px;
    list-style: disc;
    color: var(--muted, #aaa);
    font-size: 13px;
  }
</style>
