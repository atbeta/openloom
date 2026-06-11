<script>
  import { onMount } from 'svelte';

  let state = $state({
    server: { ok: false, url: '', message: 'loading' },
    recentWorkspaces: [],
    tasks: [],
    sessions: [],
    sessionsByDirectory: {},
    archivedSessions: [],
    sessionStatus: {},
    metrics: { running: 0, waiting: 0, failed: 0, completedToday: 0 }
  });

  let loading = $state(true);
  let error = $state('');
  let taskError = $state('');
  let taskSubmitting = $state(false);
  let lastRefreshed = $state(0);
  let now = $state(Date.now());
  let selectedTaskId = $state(null);

  let taskWorkspace = $state('');
  let taskCheckInterval = $state(0);
  let taskPlan = $state({
    name: '',
    goal: '',
    steps: [{ title: '', acceptance: [''] }],
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
  let drawerError = $state('');
  let drawerMessages = $state([]);
  let drawerDiff = $state([]);
  let drawerLoadedAt = $state(0);

  let drawerTaskId = $state('');
  let drawerTaskTab = $state('overview');

  const intervalPresets = [
    { label: 'Once', minutes: 0 },
    { label: '5m', minutes: 5 },
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

  const selectedTask = $derived(
    drawerTask || state.tasks.find((task) => task.id === selectedTaskId) || null
  );

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
    const sec = task?.check_interval_seconds || 0;
    if (sec <= 0) return 'once';
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

  async function openSessionDrawer(sessionId) {
    closeTaskDrawer();
    drawerSessionId = sessionId;
    drawerTab = 'messages';
    drawerError = '';
    drawerMessages = [];
    drawerDiff = [];
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
    if (!drawerSessionId) return;
    await loadDrawerData(drawerSessionId);
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

  function renderPart(part) {
    if (part == null) return null;
    if (typeof part === 'string') {
      const t = part.trim();
      return t ? t : null;
    }
    if (typeof part !== 'object') return String(part);
    const type = String(part.type || '').toLowerCase();
    if (type === 'text' || type === '') {
      const t = part.text || part.content;
      if (typeof t === 'string' && t.trim()) return t.trim();
      return null;
    }
    if (type === 'reasoning') {
      const t = part.text || part.content || '';
      const cleaned = typeof t === 'string' ? t.trim() : '';
      return cleaned ? `💭 ${cleaned.slice(0, 200)}` : null;
    }
    if (type === 'tool' || type === 'tool_use' || type === 'tool-invocation') {
      const name = part.name || part.tool || 'tool';
      const args = part.input || part.args || part.arguments;
      if (args && typeof args === 'object') {
        for (const key of ['command', 'filePath', 'path', 'file', 'url', 'query', 'prompt']) {
          const v = args[key];
          if (typeof v === 'string' && v.trim()) return `🔧 ${name} — ${v.slice(0, 120)}`;
        }
      }
      return `🔧 ${name}`;
    }
    if (type === 'tool_result' || type === 'tool-result') {
      const name = part.name || part.tool || 'tool';
      const output = part.output ?? part.content;
      if (typeof output === 'string') {
        const firstLine = output.split('\n').map((line) => line.trim()).find(Boolean);
        return `↪ ${name}${firstLine ? `: ${firstLine.slice(0, 140)}` : ''}`;
      }
      return `↪ ${name}`;
    }
    if (type === 'step-start' || type === 'step-finish') return null;
    const keys = Object.keys(part).filter((k) => k !== 'type');
    if (keys.length === 0) return null;
    return `· ${type || 'part'} (${keys.slice(0, 3).join(', ')})`;
  }

  function messageRole(message) {
    const info = message?.info;
    if (info && typeof info === 'object' && info.role) return String(info.role).toLowerCase();
    return (message?.role || message?.type || 'message').toLowerCase();
  }

  function summarizeMessage(message) {
    if (!message) return ['(empty)'];
    if (typeof message.text === 'string' && message.text.trim()) return [message.text.trim()];
    const parts = getMessageParts(message);
    if (Array.isArray(parts) && parts.length > 0) {
      const lines = parts.map(renderPart).filter(Boolean);
      if (lines.length === 0) return ['(no displayable content)'];
      if (lines.length > 5) return [...lines.slice(0, 5), `(+${lines.length - 5} more parts)`];
      return lines;
    }
    const info = getMessageInfo(message);
    if (info) {
      const summary = pickFirst(info, ['summary', 'text', 'content']);
      if (summary) return [String(summary).slice(0, 240)];
    }
    return ['(empty)'];
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

  function emptyPlanStep() {
    return { title: '', acceptance: [''] };
  }

  function emptyPlan() {
    return {
      name: '',
      goal: '',
      steps: [emptyPlanStep()],
      globalAcceptance: [],
    };
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
      const acceptance = step.acceptance.filter((_, j) => j !== accIndex);
      return { ...step, acceptance: acceptance.length ? acceptance : [''] };
    });
    taskPlan = { ...taskPlan, steps };
  }

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
    const name = plan.name.trim() || steps[0]?.title || 'Untitled task';
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
        checkIntervalMinutes: checkIntervalMinutes ?? taskCheckInterval,
        workspace: (workspace ?? taskWorkspace).trim() || undefined,
      };
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
    const name = projectName(dir);
    const path = compactPath(dir);
    if (path && path !== name) return `${name} — ${path} (${count})`;
    return `${name} (${count})`;
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

  function compactPath(path) {
    const normalized = shortPath(path);
    if (!normalized) return '';
    const parts = normalized.split('/').filter(Boolean);
    if (parts.length <= 2) return normalized.startsWith('/') ? `/${parts.join('/')}` : parts.join('/');
    return `…/${parts.slice(-2).join('/')}`;
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
      <div class="brand-mark">⌘</div>
      <span class="brand-name">OpenLoom</span>
    </div>
    <div class="conn">
      <span class:off={!state.server.ok} class="conn-dot"></span>
      {state.server.ok ? 'Online' : 'Offline'}
    </div>

    <div class="sidebar-section">
      <div class="nav-label">Status</div>
      <div class="status-line"><span class="dim">Server</span><span class="mono" title={state.server.url}>{state.server.url.replace(/^https?:\/\//, '')}</span></div>
      <div class="status-line"><span class="dim">Sessions</span><span class="mono">{state.sessions.length}</span></div>
      <div class="status-line"><span class="dim">Tasks</span><span class="mono">{state.tasks.length}</span></div>
      <button class="status-line status-button" type="button" onclick={() => (archivedPopoverOpen = !archivedPopoverOpen)} aria-expanded={archivedPopoverOpen}>
        <span class="dim">Archived</span>
        <span class="mono">{(state.archivedSessions || []).length} ▸</span>
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
      <div class="status-line"><span class="dim">Updated</span><span class="mono">{refreshAgeSeconds === null ? '—' : `${refreshAgeSeconds}s ago`}</span></div>
    </div>

    <div class="sidebar-section sidebar-recent">
      <div class="nav-label">Recent Workspaces</div>
      <div class="recent-list">
        {#if state.recentWorkspaces.length === 0}
          <div class="dim empty-mini">Used paths will appear here.</div>
        {:else}
          {#each state.recentWorkspaces as workspace}
            <div class="recent-row" class:active={taskWorkspace === workspace}>
              <button class="recent-item mono" type="button" title={workspace} onclick={() => selectRecentWorkspace(workspace)}>
                {compactPath(workspace)}
              </button>
              <button class="recent-remove" type="button" aria-label="Remove from recents" title="Remove" onclick={(event) => removeRecentWorkspace(workspace, event)}>×</button>
            </div>
          {/each}
        {/if}
      </div>
    </div>
  </aside>

  <main class="main">
    <header class="status-bar">
      <div class="status-left">
        <h1>OpenCode Server</h1>
        <span class={`pill ${state.server.ok ? 'pill-ok' : 'pill-fail'}`}>
          <span class="pill-dot"></span>{state.server.ok ? 'Healthy' : 'Unavailable'}
        </span>
      </div>
    </header>

    {#if error}
      <div class="error" style="padding: 10px 20px;">{error}</div>
    {/if}

    <section class="table-section">
      {#if loading}
        <div class="empty">Loading OpenLoom state…</div>
      {:else if state.tasks.length === 0 && state.sessions.length === 0}
        <div class="empty">No sessions or tasks yet. Use New Task in the Actions panel.</div>
      {:else}
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
                    <td><span class="task-title">{task.name}</span></td>
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
                    <td><span class="task-title">{task.name}</span></td>
                    <td class="mono">{taskIntervalLabel(task)}</td>
                    <td class="mono">{shortPath(task.workspace)}</td>
                    <td><span class={`pill ${statusClass(task.status)}`}><span class="pill-dot"></span>{task.status}</span></td>
                    <td>
                      {#if task.active_session_id}
                        <button class="btn btn-ghost btn-sm row-btn" type="button" onclick={(e) => { e.stopPropagation(); viewTaskSession(task); }}>View</button>
                      {/if}
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
  </main>

  <aside class="dispatch">
    <div class="dispatch-head"><h2>Actions</h2></div>
    <div class="dispatch-body">
      <section class="block">
        <div class="block-label">New Task</div>
        <div class="segmented">
          <button type="button" class:active={taskTarget === 'workspace'} onclick={() => (taskTarget = 'workspace')}>Workspace</button>
          <button type="button" class:active={taskTarget === 'session'} onclick={() => (taskTarget = 'session')}>Session</button>
        </div>
        {#if taskTarget === 'workspace'}
          <div class="field">
            <label for="task-workspace">Workspace</label>
            <div class="path-row">
              <input id="task-workspace" class="mono" type="text" bind:value={taskWorkspace} placeholder="/path/to/project" />
              <button class="btn btn-ghost btn-sm path-pick" type="button" title="Choose folder" aria-label="Choose folder" onclick={() => openFolderBrowser(taskWorkspace)}>…</button>
            </div>
          </div>
        {:else}
          <div class="field">
            <label for="task-project">Project</label>
            <select id="task-project" value={selectedProjectDir} onchange={(e) => selectProjectDir(e.currentTarget.value)}>
              {#each sortedDirectories as dir}
                {@const sessions = state.sessionsByDirectory[dir]}
                {#if sessions?.length}
                  <option value={dir}>{projectDirLabel(dir)}</option>
                {/if}
              {/each}
            </select>
          </div>
          <div class="field">
            <label for="task-session">Session</label>
            <select id="task-session" bind:value={selectedSessionId} disabled={sessionsInSelectedProject.length === 0}>
              {#each sessionsInSelectedProject as session}
                <option value={session.id}>{sessionOptionLabel(session)}</option>
              {/each}
            </select>
          </div>
        {/if}
        <div class="plan-preview">
          <div class="block-label">Plan</div>
          <div class="field">
            <label for="plan-name">Title</label>
            <input id="plan-name" type="text" bind:value={taskPlan.name} placeholder="Short task name" />
          </div>
          <div class="field">
            <label for="plan-goal">Goal</label>
            <textarea id="plan-goal" class="harness-goal" bind:value={taskPlan.goal} placeholder="What should be true when done?"></textarea>
          </div>
          <div class="field">
            <div class="field-head">
              <span class="field-label">Steps</span>
              <button class="btn btn-ghost btn-sm" type="button" onclick={addPlanStep}>+ Add step</button>
            </div>
            <div class="plan-step-list">
              {#each taskPlan.steps as step, i}
                <div class="plan-step-card">
                  <div class="harness-list-row">
                    <span class="harness-list-index">{i + 1}</span>
                    <input type="text" bind:value={taskPlan.steps[i].title} placeholder="Describe this step" />
                    <button class="list-remove" type="button" aria-label="Remove step" onclick={() => removePlanStep(i)}>×</button>
                  </div>
                  <div class="plan-step-acceptance">
                    <div class="field-head">
                      <span class="dim plan-step-label">Step acceptance</span>
                      <button class="btn btn-ghost btn-sm" type="button" onclick={() => addStepAcceptance(i)}>+ Add</button>
                    </div>
                    <div class="harness-list">
                      {#each step.acceptance as _item, j}
                        <div class="harness-list-row">
                          <span class="harness-list-index">✓</span>
                          <input
                            type="text"
                            bind:value={taskPlan.steps[i].acceptance[j]}
                            placeholder="Done when this step…"
                          />
                          <button
                            class="list-remove"
                            type="button"
                            aria-label="Remove criterion"
                            onclick={() => removeStepAcceptance(i, j)}
                          >×</button>
                        </div>
                      {/each}
                    </div>
                  </div>
                </div>
              {/each}
            </div>
          </div>
          <div class="field">
            <div class="field-head">
              <span class="field-label">Global acceptance <span class="dim">(optional)</span></span>
              <button class="btn btn-ghost btn-sm" type="button" onclick={addGlobalAcceptance}>+ Add</button>
            </div>
            {#if taskPlan.globalAcceptance.length}
              <div class="harness-list">
                {#each taskPlan.globalAcceptance as _item, i}
                  <div class="harness-list-row">
                    <span class="harness-list-index">✓</span>
                    <input
                      type="text"
                      bind:value={taskPlan.globalAcceptance[i]}
                      placeholder="Whole-task check (e.g. pytest passes)"
                    />
                    <button
                      class="list-remove"
                      type="button"
                      aria-label="Remove criterion"
                      onclick={() => removeGlobalAcceptance(i)}
                    >×</button>
                  </div>
                {/each}
              </div>
            {:else}
              <div class="dim plan-hint">Cross-cutting checks such as tests or lint.</div>
            {/if}
          </div>
          <div class="dim plan-hint">
            {planStepCount(taskPlan)} steps · {planAcceptanceCount(taskPlan)} acceptance criteria
          </div>
        </div>

        <div class="field">
          <label for="task-interval">Check interval</label>
          <div class="interval-picker">
            <div class="interval-presets">
              {#each intervalPresets as preset}
                <button
                  type="button"
                  class="interval-preset"
                  class:active={Number(taskCheckInterval) === preset.minutes}
                  onclick={() => (taskCheckInterval = preset.minutes)}
                >{preset.label}</button>
              {/each}
            </div>
            <div class="interval-custom">
              <input id="task-interval" type="number" min="0" max="120" bind:value={taskCheckInterval} />
              <span class="interval-unit">min</span>
            </div>
          </div>
          <div class="dim interval-hint">
            {Number(taskCheckInterval) <= 0
              ? 'Send once — no periodic checks.'
              : `Re-check every ${taskCheckInterval} min.`}
          </div>
        </div>
        <button
          class="btn btn-primary"
          type="button"
          disabled={taskSubmitting || !planStepCount(taskPlan)}
          onclick={() => createTask()}
        >
          {taskSubmitting ? 'Starting…' : 'Start Task'}
        </button>
        {#if taskError}
          <div class="error" style="margin-top: 8px;">{taskError}</div>
        {/if}
      </section>
    </div>
  </aside>
</div>

{#if drawerTask}
  <div class="drawer-mask" onclick={closeTaskDrawer} role="presentation"></div>
  <aside class="drawer" role="dialog" aria-label="Task detail">
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
          {#if (drawerTask.check_interval_seconds || 0) > 0}
            <span class="dim">· {stepProgressLabel(drawerTask)} steps</span>
          {/if}
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
      {:else if !['completed', 'failed', 'archived'].includes(drawerTask.status) && (drawerTask.check_interval_seconds || 0) > 0}
        <button class="btn btn-ghost" type="button" onclick={() => taskAction('pause', drawerTask.id)}>Pause</button>
      {/if}
      {#if !['completed', 'archived'].includes(drawerTask.status)}
        <button class="btn btn-ghost" type="button" onclick={() => taskAction('complete', drawerTask.id)}>Complete</button>
      {/if}
      {#if drawerTask.status !== 'archived'}
        <button class="btn btn-ghost" type="button" onclick={() => taskAction('archive', drawerTask.id)}>Archive</button>
      {/if}
    </footer>
  </aside>
{/if}

{#if drawerSession}
  <div class="drawer-mask" onclick={closeDrawer} role="presentation"></div>
  <aside class="drawer" role="dialog" aria-label="Session detail">
    <header class="drawer-head">
      <div class="drawer-head-main">
        <div class="drawer-eyebrow">Session</div>
        <h3>{drawerSession.title}</h3>
        <div class="mono drawer-dir">{shortPath(drawerSession.directory) || '(unknown directory)'}</div>
        <div class="drawer-status-row">
          <span class={`pill ${statusClass(sessionStatus(drawerSession))}`}>
            {#if statusShowsDot(sessionStatus(drawerSession))}<span class="pill-dot"></span>{/if}
            {sessionStatus(drawerSession)}
          </span>
          {#if drawerSessionArchived}
            <span class="pill pill-fail" style="display: inline-flex; margin-left: 6px;"><span class="pill-dot"></span>archived</span>
          {/if}
        </div>
      </div>
      <div class="drawer-head-actions">
        <button class="btn btn-ghost btn-sm drawer-refresh" class:drawer-refresh-busy={drawerLoading} type="button" onclick={refreshDrawer} disabled={drawerLoading}>
          <span class="drawer-refresh-icon" aria-hidden="true">↻</span>
        </button>
        <button class="btn btn-ghost btn-sm" type="button" onclick={closeDrawer}>Close</button>
      </div>
    </header>
    <div class="drawer-tabs">
      <button class:active={drawerTab === 'messages'} type="button" onclick={() => (drawerTab = 'messages')}>Messages</button>
      <button class:active={drawerTab === 'diff'} type="button" onclick={() => (drawerTab = 'diff')}>Diff</button>
      <button class:active={drawerTab === 'meta'} type="button" onclick={() => (drawerTab = 'meta')}>Meta</button>
    </div>
    <div class="drawer-body">
      {#if drawerLoading && drawerMessages.length === 0}
        <div class="empty">Loading…</div>
      {:else if drawerError}
        <div class="error">{drawerError}</div>
      {:else if drawerTab === 'messages'}
        {#if drawerMessages.length === 0}
          <div class="empty">No messages yet.</div>
        {:else}
          <ol class="msg-list">
            {#each drawerMessages as message}
              {@const ts = messageTimestamp(message)}
              {@const lines = summarizeMessage(message)}
              <li class={`msg msg-${messageRole(message)}`}>
                <div class="msg-meta">
                  <span class="mono msg-role">{messageRole(message)}</span>
                  {#if ts}<span class="mono msg-ts">{ts}</span>{/if}
                </div>
                <div class="msg-body">
                  {#each lines as line}<div class="msg-line">{line}</div>{/each}
                </div>
              </li>
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
  </aside>
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
              <span class="folder-picker-icon">↩</span><span>Parent folder</span>
            </button>
          </li>
        {/if}
        {#each folderBrowseChildren as child}
          <li>
            <button class="folder-picker-item" type="button" onclick={() => loadBrowse(child.path)}>
              <span class="folder-picker-icon">▸</span><span>{child.name}</span>
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
  .interval-hint {
    margin-top: 6px;
    font-size: 12px;
  }
  .plan-preview {
    margin-top: 4px;
  }
  .field-label {
    font-size: 13px;
    color: var(--muted, #aaa);
  }
  .plan-step-list {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .plan-step-card {
    padding: 10px;
    border: 1px solid var(--border, #2a2a2a);
    border-radius: 8px;
  }
  .plan-step-acceptance {
    margin-top: 8px;
    padding-left: 28px;
  }
  .plan-step-label {
    font-size: 12px;
  }
  .plan-hint {
    margin-top: 8px;
    font-size: 12px;
  }
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
