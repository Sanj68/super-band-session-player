/**
 * Helpers for v0.7 upload source groove maps (aligned with backend has_source_groove / slot shape).
 */

const EPS = 1e-6;

/**
 * @param {Record<string, unknown> | null | undefined} sourceAnalysis
 * @param {number} barCount
 */
export function hasSourceGroove(sourceAnalysis, barCount) {
  if (!sourceAnalysis || typeof barCount !== "number" || barCount < 1) return false;
  const rows = sourceAnalysis.source_slot_pressure;
  if (!Array.isArray(rows) || rows.length < barCount) return false;
  return rows.some(
    (row) => Array.isArray(row) && row.some((x) => Number.isFinite(Number(x)) && Number(x) > EPS),
  );
}

/**
 * @param {Record<string, unknown> | null | undefined} sourceAnalysis
 * @returns {number | null}
 */
export function averageGrooveConfidence(sourceAnalysis) {
  const arr = sourceAnalysis?.source_groove_confidence;
  if (!Array.isArray(arr) || arr.length === 0) return null;
  let sum = 0;
  for (const x of arr) {
    const n = Number(x);
    sum += Number.isFinite(n) ? n : 0;
  }
  return sum / arr.length;
}

/**
 * @param {unknown} matrix - list of per-bar length-16 rows
 * @param {{ threshold?: number, maxPerBar?: number, maxBars?: number }} [opts]
 */
export function summarizeStrongSlots(matrix, opts = {}) {
  const threshold = opts.threshold ?? 0.45;
  const maxPerBar = opts.maxPerBar ?? 2;
  const maxBars = opts.maxBars ?? 6;
  if (!Array.isArray(matrix)) return "—";
  const parts = [];
  matrix.forEach((row, bi) => {
    if (parts.length >= maxBars) return;
    if (!Array.isArray(row) || row.length === 0) return;
    const hits = row
      .map((v, i) => ({ i, v: Number(v) }))
      .filter(({ v }) => Number.isFinite(v) && v >= threshold)
      .sort((a, b) => b.v - a.v)
      .slice(0, maxPerBar)
      .map(({ i }) => i);
    if (hits.length) parts.push(`bar ${bi}: ${hits.join(", ")}`);
  });
  return parts.length ? parts.join(" · ") : "—";
}

/**
 * @param {Record<string, unknown> | null | undefined} session
 */
export function getSourceGrooveSummary(session) {
  const sa = session?.engine_data?.source_analysis ?? null;
  const barCount = Math.max(1, Number(session?.bar_count) || 8);
  const detected = hasSourceGroove(sa, barCount);
  const resolution = sa?.source_groove_resolution ?? 16;
  const rows = Array.isArray(sa?.source_slot_pressure) ? sa.source_slot_pressure : [];
  const barsMapped = rows.filter(
    (r) => Array.isArray(r) && r.some((x) => Number.isFinite(Number(x)) && Number(x) > EPS),
  ).length;
  const avgConf = averageGrooveConfidence(sa);
  const kickSummary = detected ? summarizeStrongSlots(sa?.source_kick_weight, { threshold: 0.45 }) : "—";
  const snareSummary = detected ? summarizeStrongSlots(sa?.source_snare_weight, { threshold: 0.45 }) : "—";
  return {
    detected,
    resolution,
    barsMapped,
    barCount,
    avgConfidence: avgConf,
    kickSummary,
    snareSummary,
  };
}

/**
 * One-line status for bass / candidates UI (informational).
 * @param {Record<string, unknown> | null | undefined} session
 */
export function getSourceAwareBassStatusLine(session) {
  if (!session) return "Source-aware bass: inactive";
  const anchor = String(session.anchor_lane || "").toLowerCase();
  if (anchor === "drums") {
    return "MIDI drum anchor takes priority";
  }
  const barCount = Math.max(1, Number(session.bar_count) || 8);
  const sa = session.engine_data?.source_analysis;
  if (hasSourceGroove(sa, barCount)) {
    return "Source-aware bass: active";
  }
  return "Source-aware bass: inactive";
}
