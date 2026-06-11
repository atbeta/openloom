export const UNKNOWN_MODEL = 'Unknown';

export function emptyUsageSlice() {
  return {
    totalCost: 0,
    totalTokens: { input: 0, output: 0, reasoning: 0, cacheRead: 0, cacheWrite: 0 },
    tokenTotal: 0,
    sessionCount: 0,
    sessionsWithUsage: 0,
    cacheEfficiency: 0,
    byModel: [],
    topSessions: [],
  };
}

export function sessionUpdatedAt(session) {
  const updated = session?.updated;
  if (typeof updated === 'number' && updated > 0) {
    return updated > 1e12 ? updated / 1000 : updated;
  }
  const time = session?.time;
  if (time && typeof time === 'object') {
    for (const key of ['updated', 'created']) {
      const raw = time[key];
      if (typeof raw === 'number' && raw > 0) {
        return raw > 1e12 ? raw / 1000 : raw;
      }
    }
  }
  for (const key of ['updated_at', 'created', 'at']) {
    const raw = session?.[key];
    if (typeof raw === 'number' && raw > 0) {
      return raw > 1e12 ? raw / 1000 : raw;
    }
  }
  return 0;
}

export function parseSessionTokens(session) {
  const tokens = session?.tokens && typeof session.tokens === 'object' ? session.tokens : {};
  const cache = tokens.cache && typeof tokens.cache === 'object' ? tokens.cache : {};
  return {
    input: Number(tokens.input) || 0,
    output: Number(tokens.output) || 0,
    reasoning: Number(tokens.reasoning) || 0,
    cacheRead: Number(cache.read) || 0,
    cacheWrite: Number(cache.write) || 0,
  };
}

function sessionModelRef(session) {
  const model = session?.model && typeof session.model === 'object' ? session.model : {};
  return {
    providerID: String(model.providerID || '').trim(),
    modelID: String(model.id || model.modelID || '').trim(),
  };
}

export function sessionModelName(session) {
  const { providerID, modelID } = sessionModelRef(session);
  if (providerID && modelID) return `${providerID}/${modelID}`;
  if (modelID) return modelID;
  if (providerID) return providerID;
  const agent = String(session?.agent || '').trim();
  if (agent) return `agent:${agent}`;
  return UNKNOWN_MODEL;
}

export function isUnknownModel(model) {
  return model === UNKNOWN_MODEL || model === 'unknown' || model === 'Unlabeled';
}

function sessionHasUsage(session) {
  const tokens = parseSessionTokens(session);
  const cost = Number(session?.cost) || 0;
  return cost > 0 || Object.values(tokens).some((value) => value > 0);
}

function sessionUsageRow(session) {
  const tokens = parseSessionTokens(session);
  const { providerID, modelID } = sessionModelRef(session);
  const totalTokens = Object.values(tokens).reduce((sum, value) => sum + value, 0);
  return {
    id: session.id,
    title: session.title || session.id || 'Untitled',
    directory: session.directory || '',
    cost: Math.round((Number(session.cost) || 0) * 1e6) / 1e6,
    tokens,
    totalTokens,
    model: sessionModelName(session),
    providerID,
    modelID,
    updatedAt: sessionUpdatedAt(session),
  };
}

export function aggregateSessionUsage(sessions) {
  const totals = { input: 0, output: 0, reasoning: 0, cacheRead: 0, cacheWrite: 0 };
  let totalCost = 0;
  let sessionsWithUsage = 0;
  const byModel = new Map();
  const rows = [];

  for (const session of sessions) {
    if (!session || typeof session !== 'object') continue;
    if (!sessionHasUsage(session)) continue;
    const row = sessionUsageRow(session);
    sessionsWithUsage += 1;
    totalCost += row.cost;
    for (const [key, value] of Object.entries(row.tokens)) {
      totals[key] += value;
    }

    const modelKey = `${row.providerID}\0${row.modelID}`;
    const bucket = byModel.get(modelKey) || {
      providerID: row.providerID,
      modelID: row.modelID,
      model: row.model,
      cost: 0,
      sessionCount: 0,
      tokens: { input: 0, output: 0, reasoning: 0, cacheRead: 0, cacheWrite: 0 },
    };
    bucket.cost += row.cost;
    bucket.sessionCount += 1;
    for (const [key, value] of Object.entries(row.tokens)) {
      bucket.tokens[key] += value;
    }
    byModel.set(modelKey, bucket);
    rows.push(row);
  }

  const tokenTotal = Object.values(totals).reduce((sum, value) => sum + value, 0);
  const cacheEfficiency =
    totals.cacheRead + totals.input > 0 ? totals.cacheRead / (totals.cacheRead + totals.input) : 0;

  const byModelList = [...byModel.values()]
    .map((item) => ({
      ...item,
      cost: Math.round(item.cost * 1e6) / 1e6,
      totalTokens: Object.values(item.tokens).reduce((sum, value) => sum + value, 0),
    }))
    .sort((a, b) => b.cost - a.cost || b.totalTokens - a.totalTokens || a.model.localeCompare(b.model));

  const topSessions = [...rows]
    .sort((a, b) => b.totalTokens - a.totalTokens || b.cost - a.cost)
    .slice(0, 12);

  return {
    totalCost: Math.round(totalCost * 1e6) / 1e6,
    totalTokens: Object.fromEntries(Object.entries(totals).map(([key, value]) => [key, Math.round(value)])),
    tokenTotal: Math.round(tokenTotal),
    sessionCount: sessions.filter((session) => session && typeof session === 'object').length,
    sessionsWithUsage,
    cacheEfficiency: Math.round(cacheEfficiency * 10000) / 10000,
    byModel: byModelList,
    topSessions,
  };
}

function startOfDay(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime() / 1000;
}

function startOfWeek(date) {
  const day = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  day.setDate(day.getDate() - day.getDay());
  return day.getTime() / 1000;
}

function startOfMonth(date) {
  return new Date(date.getFullYear(), date.getMonth(), 1).getTime() / 1000;
}

export function buildUsagePeriods(sessions, nowSec = Date.now() / 1000) {
  const current = new Date(nowSec * 1000);
  const starts = {
    today: startOfDay(current),
    week: startOfWeek(current),
    month: startOfMonth(current),
    total: null,
  };
  const list = Array.isArray(sessions) ? sessions : [];
  const periods = {};
  for (const [key, since] of Object.entries(starts)) {
    const filtered =
      since == null ? list : list.filter((session) => sessionUpdatedAt(session) >= since);
    periods[key] = aggregateSessionUsage(filtered);
  }
  return { periods, now: nowSec };
}
