import PianoRollPreview from "./PianoRollPreview.jsx";

const LANE_KEYS = ["drums", "bass", "chords", "lead"];

const LANE_ACCENTS = {
  drums: "#0ea5e9",
  bass: "#8b5cf6",
  chords: "#f59e0b",
  lead: "#10b981",
};

const PRESET_LABELS = {
  latin_jazz: "Latin jazz",
  fusion: "Fusion",
  cool_modal: "Cool / modal",
  dusty_broken: "Dusty broken",
  soulful_funk: "Soulful funk",
  rare_groove_soul: "Rare groove soul",
};

function humanize(value) {
  if (value == null || value === "") return "—";
  if (PRESET_LABELS[value]) return PRESET_LABELS[value];
  return String(value).replace(/_/g, " ");
}

function SessionSummaryBlock({ label, session }) {
  if (!session) {
    return (
      <div style={{ fontSize: 13, color: "#94a3b8" }}>
        <div style={{ fontWeight: 700, fontSize: 11, letterSpacing: "0.04em", color: "#64748b", marginBottom: 6 }}>
          {label}
        </div>
        —
      </div>
    );
  }
  return (
    <div style={{ fontSize: 13, color: "#334155" }}>
      <div style={{ fontWeight: 700, fontSize: 11, letterSpacing: "0.04em", color: "#64748b", marginBottom: 6 }}>
        {label}
      </div>
      <div style={{ display: "grid", gap: 4 }}>
        <div>
          <span style={{ color: "#64748b" }}>ID</span> <code style={{ fontSize: 12 }}>{session.id}</code>
        </div>
        <div>
          <span style={{ color: "#64748b" }}>Tempo</span> {session.tempo} bpm · <span style={{ color: "#64748b" }}>Key</span>{" "}
          {session.key} {session.scale} · <span style={{ color: "#64748b" }}>Bars</span> {session.bar_count}
        </div>
        <div>
          <span style={{ color: "#64748b" }}>Preset</span> {humanize(session.session_preset)}
        </div>
        <div style={{ fontSize: 12, lineHeight: 1.45 }}>
          <span style={{ color: "#64748b" }}>Styles</span> lead {humanize(session.lead_style)}, bass{" "}
          {humanize(session.bass_style)}, chords {humanize(session.chord_style)}, drums {humanize(session.drum_style)}
        </div>
        <div style={{ fontSize: 12, lineHeight: 1.45 }}>
          <span style={{ color: "#64748b" }}>Instruments</span> lead {humanize(session.lead_instrument)}, bass{" "}
          {humanize(session.bass_instrument)}, chords {humanize(session.chord_instrument)}, kit{" "}
          {humanize(session.drum_kit)}
        </div>
      </div>
    </div>
  );
}

function laneSettingsLine(laneKey, session) {
  if (!session) return "—";
  if (laneKey === "drums") {
    const dp = session.drum_player ? ` · player ${humanize(session.drum_player)}` : "";
    return `${humanize(session.drum_style)} · kit ${humanize(session.drum_kit)}${dp}`;
  }
  if (laneKey === "bass") {
    const pl = session.bass_player ? ` · player ${humanize(session.bass_player)}` : "";
    return `${humanize(session.bass_style)} · ${humanize(session.bass_instrument)}${pl}`;
  }
  if (laneKey === "chords") {
    const cp = session.chord_player ? ` · player ${humanize(session.chord_player)}` : "";
    return `${humanize(session.chord_style)} · ${humanize(session.chord_instrument)}${cp}`;
  }
  if (laneKey === "lead") {
    const lp = session.lead_player ? ` · player ${humanize(session.lead_player)}` : "";
    return `${humanize(session.lead_style)} · ${humanize(session.lead_instrument)}${lp}`;
  }
  return "—";
}

function LaneComparePair({ laneKey, sessionA, sessionB }) {
  const title = laneKey.charAt(0).toUpperCase() + laneKey.slice(1);
  const accent = LANE_ACCENTS[laneKey] ?? "#64748b";
  const laneA = sessionA?.lanes?.[laneKey];
  const laneB = sessionB?.lanes?.[laneKey];

  const col = (lane, s) => (
    <div style={{ minWidth: 0 }}>
      <p style={{ color: "#475569", fontSize: 13, margin: "0 0 0.35rem", lineHeight: 1.35 }}>{lane?.preview || "—"}</p>
      <PianoRollPreview
        notes={lane?.notes}
        barCount={s?.bar_count ?? 8}
        tempoBpm={s?.tempo ?? 120}
        generated={!!lane?.generated}
        accent={accent}
      />
    </div>
  );

  return (
    <div
      style={{
        marginTop: "0.75rem",
        paddingTop: "0.75rem",
        borderTop: "1px solid #e2e8f0",
      }}
    >
      <div style={{ fontWeight: 600, fontSize: 14, marginBottom: "0.35rem", color: "#0f172a" }}>{title}</div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: "0.75rem",
          fontSize: 12,
          color: "#475569",
          lineHeight: 1.4,
          marginBottom: "0.35rem",
        }}
      >
        <div>
          <span style={{ fontWeight: 700, color: "#0369a1" }}>A</span> {laneSettingsLine(laneKey, sessionA)}
        </div>
        <div>
          <span style={{ fontWeight: 700, color: "#047857" }}>Current</span> {laneSettingsLine(laneKey, sessionB)}
        </div>
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: "0.75rem",
          fontSize: 11,
          color: "#64748b",
          marginBottom: "0.35rem",
        }}
      >
        <div>Lock: {laneA?.locked ? "on" : "off"}</div>
        <div>Lock: {laneB?.locked ? "on" : "off"}</div>
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: "0.75rem",
        }}
      >
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: "#0369a1", marginBottom: 6 }}>A</div>
          {col(laneA, sessionA)}
        </div>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: "#047857", marginBottom: 6 }}>Current</div>
          {col(laneB, sessionB)}
        </div>
      </div>
    </div>
  );
}

/**
 * Read-only side-by-side view of snapshot A vs the active session.
 */
export default function SessionComparePanel({ sessionA, sessionCurrent, onClose }) {
  return (
    <section
      style={{
        border: "2px solid #cbd5e1",
        borderRadius: 12,
        padding: "1rem 1.1rem",
        marginBottom: "1rem",
        background: "#f8fafc",
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: "0.75rem",
          flexWrap: "wrap",
          marginBottom: "0.75rem",
        }}
      >
        <h2 style={{ margin: 0, fontSize: "1.05rem", color: "#0f172a" }}>A / Current compare</h2>
        <button type="button" onClick={onClose} style={{ padding: "0.4rem 0.85rem", fontSize: 14 }}>
          Close compare
        </button>
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: "1rem",
          marginBottom: "0.25rem",
        }}
      >
        <SessionSummaryBlock label="A" session={sessionA} />
        <SessionSummaryBlock label="Current" session={sessionCurrent} />
      </div>

      <div style={{ marginTop: "0.5rem" }}>
        {LANE_KEYS.map((k) => (
          <LaneComparePair key={k} laneKey={k} sessionA={sessionA} sessionB={sessionCurrent} />
        ))}
      </div>
    </section>
  );
}
