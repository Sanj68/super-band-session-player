/**
 * API client for Super Band Session Player backend.
 * In Vite dev, use same-origin `/api` (proxy). Override with `VITE_API_URL`.
 */
const API_BASE = (import.meta.env.VITE_API_URL ?? "").replace(/\/$/, "");

async function parseError(res) {
  let detail;
  try {
    detail = await res.json();
  } catch {
    detail = await res.text();
  }
  const err = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  err.status = res.status;
  err.detail = detail;
  throw err;
}

export async function createSession(payload) {
  const res = await fetch(`${API_BASE}/api/sessions/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) await parseError(res);
  return res.json();
}

export async function generateSession(sessionId) {
  const res = await fetch(`${API_BASE}/api/sessions/${sessionId}/generate`, {
    method: "POST",
  });
  if (!res.ok) await parseError(res);
  return res.json();
}

/** Regenerate non-anchor lanes using anchor lane timing/density context. Optional body sets anchor_lane first. */
export async function generateAroundAnchor(sessionId, body = {}) {
  const res = await fetch(`${API_BASE}/api/sessions/${sessionId}/generate-around-anchor`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body && Object.keys(body).length ? body : {}),
  });
  if (!res.ok) await parseError(res);
  return res.json();
}

/** Clone session (settings + lane MIDI). Returns full `SessionState`. */
export async function duplicateSession(sessionId) {
  const res = await fetch(`${API_BASE}/api/sessions/${sessionId}/duplicate`, {
    method: "POST",
  });
  if (!res.ok) await parseError(res);
  return res.json();
}

export async function regenerateLane(sessionId, lane) {
  const res = await fetch(`${API_BASE}/api/sessions/${sessionId}/lanes/${lane}/regenerate`, {
    method: "POST",
  });
  if (!res.ok) await parseError(res);
  return res.json();
}

/** @param {string[]} lanes e.g. `["bass", "chords"]` — returns full `SessionState` */
export async function regenerateSelectedLanes(sessionId, lanes) {
  const res = await fetch(`${API_BASE}/api/sessions/${sessionId}/regenerate-selected`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ lanes }),
  });
  if (!res.ok) await parseError(res);
  return res.json();
}

/** Regenerate all lanes that are not locked; returns full `SessionState`. */
export async function regenerateUnlockedLanes(sessionId) {
  const res = await fetch(`${API_BASE}/api/sessions/${sessionId}/regenerate-unlocked`, {
    method: "POST",
  });
  if (!res.ok) await parseError(res);
  return res.json();
}

/** Context-aware lead replacement (V1: `target_lane` must be `"lead"`). */
export async function addPartToSuit(sessionId, body) {
  const res = await fetch(`${API_BASE}/api/sessions/${sessionId}/add-part-to-suit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) await parseError(res);
  return res.json();
}

export async function getSession(sessionId) {
  const res = await fetch(`${API_BASE}/api/sessions/${sessionId}`);
  if (!res.ok) await parseError(res);
  return res.json();
}

/** PATCH session settings (e.g. `lead_style`). Returns full `SessionState`. */
export async function patchSession(sessionId, body) {
  const res = await fetch(`${API_BASE}/api/sessions/${sessionId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) await parseError(res);
  return res.json();
}

/** Partial lane lock update; returns full `SessionState`. */
export async function patchLaneLocks(sessionId, body) {
  const res = await fetch(`${API_BASE}/api/sessions/${sessionId}/lane-locks`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) await parseError(res);
  return res.json();
}

export function midiLaneUrl(sessionId, lane) {
  return `${API_BASE}/api/sessions/${sessionId}/midi/${lane}`;
}

export function exportZipUrl(sessionId) {
  return `${API_BASE}/api/sessions/${sessionId}/export`;
}

export async function downloadExportZip(sessionId) {
  const res = await fetch(exportZipUrl(sessionId));
  if (!res.ok) await parseError(res);
  return res.blob();
}

/** @returns {{ setups: Array<Record<string, unknown>> }} */
export async function listSetups() {
  const res = await fetch(`${API_BASE}/api/setups`);
  if (!res.ok) await parseError(res);
  return res.json();
}

/** @param {Record<string, unknown>} body */
export async function createSetup(body) {
  const res = await fetch(`${API_BASE}/api/setups`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) await parseError(res);
  return res.json();
}

/** @param {string} name setup name (URL-encoded internally) */
export async function deleteSetup(name) {
  const res = await fetch(`${API_BASE}/api/setups/${encodeURIComponent(name)}`, {
    method: "DELETE",
  });
  if (!res.ok) await parseError(res);
  return res.json();
}

/** @returns {{ patch: Record<string, unknown> }} validated PATCH body for PATCH /api/sessions/{id} */
export async function getSavedSetupAsSessionPatch(name) {
  const res = await fetch(
    `${API_BASE}/api/setups/${encodeURIComponent(name)}/as-session-patch`,
  );
  if (!res.ok) await parseError(res);
  return res.json();
}
