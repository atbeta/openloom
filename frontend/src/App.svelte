<script>
  import { onMount } from 'svelte';

  let tasks = $state([]);
  let sessions = $state([]);
  let byDirectory = $state({});
  let sessionStatus = $state({});
  let metrics = $state({ running: 0, waiting: 0, failed: 0, completed: 0 });
  let storeVersion = $state(0);
  let connStatus = $state('connecting');

  let selectedTaskId = $state(null);
  let selectedSessionId = $state(null);
  let drawerOpen = $state(false);
  let drawerMessages = $state([]);
  let drawerDiff = $state([]);
  let drawerLoading = $state(false);
  let drawerTab = $state('messages');

  let activePanel = $state('tasks');
  let dispatchCwd = $state('');
  let dispatchPrompt = $state('');
  let dispatchAgent = $state('opencode');
  let dispatchError = $state('');
  let dispatchLoading = $state(false);

  let polling = $state(null);

  const API = '';

  function statusClass(s) {
    const map = { running: 'busy', waiting: 'warn', completed: 'success', failed: 'danger', pending: 'muted', archived: 'muted' };
    return map[s] || 'muted';
  }

  function sessionStatusClass(s) {
    if (s === 'busy') return 'busy';
    if (s === 'retry') return 'warn';
    if (s === 'idle') return 'muted';
    return 'muted';
  }

  function updatedAge(item) {
    const t = item?.updated || item?.updated_at || item?.last_check_at || 0;
    if (!t) return '';
    const s = Math.floor(Date.now() / 1000 - t);
    if (s < 60) return 'just now';
    if (s < 3600) return Math.floor(s / 60) + 'm ago';
    if (s < 86400) return Math.floor(s / 3600) + 'h ago';
    return Math.floor(s / 86400) + 'd ago';
  }

  function dirLabel(dir) {
    if (!dir || dir === '(unknown)') return 'unknown';
    const parts = dir.split('/');
    return parts[parts.length - 1] || dir;
  }

  function compactPath(p) {
    if (!p) return '';
    if (p.startsWith('/Users/')) {
      const parts = p.split('/');
      if (parts.length >= 4) return '~/.../' + parts.slice(-3).join('/');
    }
    return p;
  }

  function messageSummary(msg) {
    const text = msg?.text || '';
    const parts = msg?.parts || [];
    if (text) return text.slice(0, 200);
    for (const part of parts) {
      if (typeof part === 'string') return part.slice(0, 200);
      if (part?.text) return part.text.slice(0, 200);
      if (part?.content) return part.content.slice(0, 200);
    }
    return '';
  }

  function messageRole(msg) {
    const info = msg?.info || msg;
    return info?.role || msg?.role || 'message';
  }

  async function refresh() {
    try {
      const res = await fetch(API + '/api/tasks');
      const data = await res.json();
      tasks = data.tasks || [];
      storeVersion = data.store_version || 0;
      metrics.running = tasks.filter(t => t.status === 'running' || t.status === 'pending').length;
      metrics.waiting = tasks.filter(t => t.status === 'waiting').length;
      metrics.failed = tasks.filter(t => t.status === 'failed').length;
      metrics.completed = tasks.filter(t => t.status === 'completed' || t.status === 'archived').length;
    } catch (e) { /* ignore */ }

    try {
      const res = await fetch(API + '/api/sessions');
      const data = await res.json();
      sessions = data.sessions || [];
      byDirectory = data.byDirectory || {};
      sessionStatus = data.status || {};
    } catch (e) { /* ignore */ }
  }

  function connectSSE() {
    const evt = new EventSource(API + '/api/events');
    evt.addEventListener('snapshot', e => {
      const d = JSON.parse(e.data);
      if (d.tasks) tasks = d.tasks;
      storeVersion = d.store_version || 0;
      connStatus = 'live';
    });
    evt.addEventListener('task', e => {
      const d = JSON.parse(e.data);
      if (d.store_version <= storeVersion) return;
      storeVersion = d.store_version;
      const idx = tasks.findIndex(t => t.id === d.task_id);
      if (idx >= 0) Object.assign(tasks[idx], d.data);
      else tasks.push({ id: d.task_id, ...d.data });
      tasks = [...tasks];
      connStatus = 'live';
    });
    evt.addEventListener('heartbeat', () => {
      connStatus = 'live';
    });
    evt.onerror = () => {
      evt.close();
      connStatus = 'reconnecting';
      refresh();
      setTimeout(connectSSE, 3000);
    };
  }

  async function openDrawer(sessionId) {
    selectedSessionId = sessionId;
    drawerOpen = true;
    drawerLoading = true;
    drawerTab = 'messages';
    try {
      const [mr, dr] = await Promise.all([
        fetch(API + '/api/sessions/' + sessionId + '/messages'),
        fetch(API + '/api/sessions/' + sessionId + '/diff')
      ]);
      drawerMessages = ((await mr.json()).messages || []).reverse();
      drawerDiff = (await dr.json()).diff || [];
    } catch (e) {
      drawerMessages = [];
      drawerDiff = [];
    }
    drawerLoading = false;
  }

  function closeDrawer() {
    drawerOpen = false;
    selectedSessionId = null;
    drawerMessages = [];
    drawerDiff = [];
  }

  async function doDispatch() {
    if (!dispatchPrompt.trim()) return;
    dispatchLoading = true;
    dispatchError = '';
    try {
      const res = await fetch(API + '/api/dispatch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cwd: dispatchCwd, prompt: dispatchPrompt, agent: dispatchAgent === 'opencode' ? null : dispatchAgent })
      });
      const data = await res.json();
      if (!data.ok) { dispatchError = data.error || 'Failed'; return; }
      dispatchPrompt = '';
      await refresh();
    } catch (e) {
      dispatchError = e.message;
    } finally {
      dispatchLoading = false;
    }
  }

  async function doArchive(taskId) {
    await fetch(API + '/api/tasks/' + taskId + '/archive', { method: 'POST' });
    await refresh();
  }

  function selectTask(id) {
    selectedTaskId = id === selectedTaskId ? null : id;
  }

  function pollLoop() {
    polling = setInterval(refresh, 15000);
  }

  onMount(() => {
    refresh();
    connectSSE();
    pollLoop();
    return () => { if (polling) clearInterval(polling); };
  });
</script>

<div class="app-shell">
  <aside class="sidebar">
    <div class="brand"><span class="brand-mark">&#x2318;</span><span>OpenLoom</span></div>
    <div class="conn"><span class="dot" class:live={connStatus === 'live'} class:reconnecting={connStatus !== 'live'}></span>{connStatus}</div>

    <div class="sidebar-section">
      <div class="nav-label">Status</div>
      <div class="stat-row"><span class="dim">Tasks</span><span class="mono">{tasks.length}</span></div>
      <div class="stat-row"><span class="dim">Running</span><span class="mono">{metrics.running}</span></div>
      <div class="stat-row"><span class="dim">Waiting</span><span class="mono">{metrics.waiting}</span></div>
      <div class="stat-row"><span class="dim">Failed</span><span class="mono">{metrics.failed}</span></div>
      <div class="stat-row"><span class="dim">Completed</span><span class="mono">{metrics.completed}</span></div>
    </div>

    <div class="sidebar-section">
      <div class="nav-label">Sessions</div>
      <div class="stat-row"><span class="dim">Visible</span><span class="mono">{sessions.length}</span></div>
    </div>

    <nav class="sidebar-nav">
      <button class:active={activePanel === 'tasks'} onclick={() => activePanel = 'tasks'}>Tasks</button>
      <button class:active={activePanel === 'sessions'} onclick={() => activePanel = 'sessions'}>Sessions</button>
      <button class:active={activePanel === 'dispatch'} onclick={() => activePanel = 'dispatch'}>Dispatch</button>
    </nav>
  </aside>

  <main class="main">
    {#if activePanel === 'tasks'}
      <div class="panel">
        <div class="panel-header"><h2>Tasks</h2><span class="muted">{tasks.length} total</span></div>
        {#each tasks as task (task.id)}
           <div class="card" class:selected={selectedTaskId === task.id} role="button" tabindex="0" onclick={() => selectTask(task.id)} onkeydown={(e) => e.key === 'Enter' && selectTask(task.id)}>
            <div class="card-row">
              <span class="card-title">{task.name || task.id}</span>
              <span class="badge {statusClass(task.status)}">{task.status}</span>
            </div>
            <div class="card-meta">
              <span>{compactPath(task.workspace)}</span>
              <span>{Math.round((task.progress || 0) * 100)}%</span>
              <span>{updatedAge(task)}</span>
            </div>
            <div class="progress-bar"><div class="progress-fill" style="width:{(task.progress || 0) * 100}%"></div></div>
            <div class="card-summary">{task.last_summary || ''}</div>
            {#if selectedTaskId === task.id}
              <div class="card-actions">
                <span class="muted">{task.id}</span>
                {#if task.status !== 'archived' && task.status !== 'completed'}
                  <button onclick={(e) => { e.stopPropagation(); doArchive(task.id); }}>Archive</button>
                {/if}
              </div>
            {/if}
          </div>
        {:else}
          <div class="empty">No tasks yet. Run openloom watch or openloom serve to get started.</div>
        {/each}
      </div>
    {/if}

    {#if activePanel === 'sessions'}
      <div class="panel">
        <div class="panel-header"><h2>Sessions</h2><span class="muted">{sessions.length} visible</span></div>
        {#each Object.entries(byDirectory) as [dir, dirSessions] (dir)}
          <div class="section-block">
            <div class="section-label">{dirLabel(dir)} <span class="tag">{compactPath(dir)}</span></div>
            {#each dirSessions as session (session.id)}
               <div class="card" role="button" tabindex="0" ondblclick={() => openDrawer(session.id)} onkeydown={(e) => e.key === 'Enter' && openDrawer(session.id)}>
                <div class="card-row">
                  <span class="card-title">{session.title || session.id}</span>
                  <span class="dot" class:busy={sessionStatus[session.id] === 'busy'} class:retry={sessionStatus[session.id] === 'retry'}></span>
                </div>
                <div class="card-meta">
                  <span>{updatedAge(session)}</span>
                  <span class="muted">{(sessionStatus[session.id] || 'idle').toUpperCase()}</span>
                </div>
              </div>
            {/each}
          </div>
        {:else}
          <div class="empty">No sessions found. Start the OpenCode server and dispatch tasks to see them here.</div>
        {/each}
      </div>
    {/if}

    {#if activePanel === 'dispatch'}
      <div class="panel">
        <div class="panel-header"><h2>Dispatch</h2></div>
        <div class="form-group">
          <label for="disp-cwd">Workspace (cwd)</label>
          <input id="disp-cwd" type="text" bind:value={dispatchCwd} placeholder="/path/to/project" />
        </div>
        <div class="form-group">
          <label for="disp-agent">Agent</label>
          <select id="disp-agent" bind:value={dispatchAgent}>
            <option value="opencode">opencode (default)</option>
          </select>
        </div>
        <div class="form-group">
          <label for="disp-prompt">Prompt</label>
          <textarea id="disp-prompt" bind:value={dispatchPrompt} rows="4" placeholder="What should the agent do?"></textarea>
        </div>
        {#if dispatchError}
          <div class="error-msg">{dispatchError}</div>
        {/if}
        <button class="btn-primary" onclick={doDispatch} disabled={dispatchLoading || !dispatchPrompt.trim()}>
          {dispatchLoading ? 'Sending...' : 'Send'}
        </button>
      </div>
    {/if}
  </main>
</div>

{#if drawerOpen}
  <div class="drawer-overlay" role="button" tabindex="0" onclick={closeDrawer} onkeydown={(e) => e.key === 'Escape' && closeDrawer()}></div>
  <aside class="drawer">
    <div class="drawer-header">
      <h3>Session {selectedSessionId?.slice(0, 12)}</h3>
      <button class="close-btn" onclick={closeDrawer}>&times;</button>
    </div>
    <div class="drawer-tabs">
      <button class:active={drawerTab === 'messages'} onclick={() => drawerTab = 'messages'}>Messages</button>
      <button class:active={drawerTab === 'diff'} onclick={() => drawerTab = 'diff'}>Diff ({drawerDiff.length})</button>
    </div>
    <div class="drawer-body">
      {#if drawerLoading}
        <div class="empty">Loading...</div>
      {:else if drawerTab === 'messages'}
        {#each drawerMessages as msg}
          <div class="msg-block">
            <div class="msg-role {messageRole(msg)}">{messageRole(msg)}</div>
            <div class="msg-text">{messageSummary(msg)}</div>
          </div>
        {:else}
          <div class="msg-block"><div class="msg-text muted">No messages</div></div>
        {/each}
        {#each drawerDiff as file}
          <div class="diff-file">
            <div class="diff-path">{file.path || file.file || 'unknown'}</div>
            <div class="diff-stat">+{file.additions || 0} / -{file.deletions || 0}</div>
          </div>
        {:else}
          <div class="diff-file"><div class="diff-path muted">No changes</div></div>
        {/each}
      {:else}
        <div class="empty">No content</div>
      {/if}
    </div>
  </aside>
{/if}

<style>
  .app-shell { display: flex; height: 100vh; }
  .sidebar { width: 220px; background: var(--surface); border-right: 1px solid var(--border); padding: 16px; display: flex; flex-direction: column; gap: 16px; overflow-y: auto; }
  .brand { font-size: 16px; font-weight: 700; color: var(--accent); display: flex; align-items: center; gap: 8px; }
  .brand-mark { font-size: 20px; }
  .conn { font-size: 11px; color: var(--meta); display: flex; align-items: center; gap: 6px; }
  .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--danger); flex-shrink: 0; }
  .dot.live { background: var(--success); }
  .dot.reconnecting { background: var(--warn); }
  .dot.busy { background: var(--accent); }
  .dot.retry { background: var(--warn); }
  .sidebar-section { display: flex; flex-direction: column; gap: 4px; }
  .nav-label { font-size: 10px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--meta); margin-bottom: 4px; }
  .stat-row { display: flex; justify-content: space-between; font-size: 12px; }
  .dim { color: var(--muted); }
  .mono { font-family: var(--font-mono); font-size: 12px; }
  .sidebar-nav { display: flex; flex-direction: column; gap: 4px; margin-top: auto; }
  .sidebar-nav button { background: none; border: none; color: var(--muted); padding: 6px 10px; text-align: left; cursor: pointer; border-radius: var(--radius-sm); font-size: 13px; }
  .sidebar-nav button:hover { background: var(--surface-2); color: var(--fg); }
  .sidebar-nav button.active { background: var(--accent-subtle); color: var(--accent); font-weight: 600; }
  .main { flex: 1; overflow-y: auto; padding: 24px; }
  .panel { max-width: 800px; }
  .panel-header { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 16px; }
  .panel-header h2 { font-size: 18px; font-weight: 600; color: var(--fg); }
  .card { background: var(--surface-2); border: 1px solid var(--border); border-radius: var(--radius-md); padding: 14px; margin-bottom: 8px; cursor: pointer; transition: border-color 0.15s; }
  .card:hover { border-color: var(--accent-ring); }
  .card.selected { border-color: var(--accent); background: var(--accent-subtle); }
  .card-row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
  .card-title { font-weight: 600; font-size: 14px; color: var(--fg); }
  .card-meta { display: flex; gap: 16px; font-size: 12px; color: var(--muted); margin-bottom: 8px; }
  .card-summary { font-size: 12px; color: var(--meta); }
  .card-actions { display: flex; justify-content: space-between; align-items: center; margin-top: 8px; padding-top: 8px; border-top: 1px solid var(--border-soft); font-size: 11px; }
  .card-actions button { background: var(--surface); border: 1px solid var(--border); color: var(--muted); padding: 2px 10px; border-radius: var(--radius-sm); cursor: pointer; font-size: 11px; }
  .card-actions button:hover { border-color: var(--accent-ring); color: var(--fg); }
  .badge { font-size: 11px; padding: 1px 8px; border-radius: 10px; font-weight: 500; }
  .badge.busy { background: color-mix(in oklch, var(--accent) 18%, var(--surface-2)); color: var(--accent); }
  .badge.warn { background: color-mix(in oklch, var(--warn) 18%, var(--surface-2)); color: var(--warn); }
  .badge.success { background: color-mix(in oklch, var(--success) 18%, var(--surface-2)); color: var(--success); }
  .badge.danger { background: color-mix(in oklch, var(--danger) 18%, var(--surface-2)); color: var(--danger); }
  .badge.muted { background: color-mix(in oklch, var(--muted) 18%, var(--surface-2)); color: var(--muted); }
  .progress-bar { height: 3px; background: var(--border); border-radius: 2px; overflow: hidden; margin-bottom: 6px; }
  .progress-fill { height: 100%; background: var(--accent); transition: width 0.3s; }
  .section-block { margin-bottom: 16px; }
  .section-label { font-size: 12px; color: var(--muted); margin-bottom: 6px; display: flex; align-items: center; gap: 8px; font-weight: 600; }
  .tag { font-weight: 400; color: var(--meta); font-family: var(--font-mono); font-size: 11px; }
  .empty { text-align: center; padding: 48px; color: var(--muted); font-size: 14px; }
  .form-group { margin-bottom: 14px; }
  .form-group label { display: block; font-size: 12px; color: var(--muted); margin-bottom: 4px; }
  .form-group input, .form-group select, .form-group textarea { width: 100%; padding: 8px 10px; background: var(--surface-2); border: 1px solid var(--border); border-radius: var(--radius-sm); color: var(--fg); font-size: 13px; font-family: var(--font-mono); }
  .form-group textarea { resize: vertical; }
  .btn-primary { background: var(--accent); color: var(--accent-on); border: none; padding: 8px 20px; border-radius: var(--radius-sm); font-size: 13px; font-weight: 600; cursor: pointer; }
  .btn-primary:disabled { opacity: 0.5; cursor: default; }
  .error-msg { color: var(--danger); font-size: 12px; margin-bottom: 8px; }
  .drawer-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.4); z-index: 10; }
  .drawer { position: fixed; right: 0; top: 0; bottom: 0; width: 480px; background: var(--surface); border-left: 1px solid var(--border); z-index: 11; display: flex; flex-direction: column; }
  .drawer-header { display: flex; justify-content: space-between; align-items: center; padding: 16px; border-bottom: 1px solid var(--border); }
  .drawer-header h3 { font-size: 15px; font-weight: 600; }
  .close-btn { background: none; border: none; color: var(--muted); font-size: 20px; cursor: pointer; }
  .drawer-tabs { display: flex; border-bottom: 1px solid var(--border); }
  .drawer-tabs button { flex: 1; padding: 10px; background: none; border: none; border-bottom: 2px solid transparent; color: var(--muted); cursor: pointer; font-size: 13px; }
  .drawer-tabs button.active { border-bottom-color: var(--accent); color: var(--fg); }
  .drawer-body { flex: 1; overflow-y: auto; padding: 12px; }
  .msg-block { padding: 8px 0; border-bottom: 1px solid var(--border-soft); }
  .msg-role { font-size: 10px; text-transform: uppercase; color: var(--accent); margin-bottom: 4px; font-weight: 600; }
  .msg-role.user { color: var(--success); }
  .msg-text { font-size: 12px; color: var(--fg-2); white-space: pre-wrap; word-break: break-word; }
  .diff-file { display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid var(--border-soft); font-size: 12px; }
  .diff-path { color: var(--fg-2); font-family: var(--font-mono); }
  .diff-stat { color: var(--muted); font-family: var(--font-mono); white-space: nowrap; }
  .muted { color: var(--muted); }
</style>
