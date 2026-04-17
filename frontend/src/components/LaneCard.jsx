import PianoRollPreview from "./PianoRollPreview.jsx";

const LANE_ACCENTS = {
  drums: "#0ea5e9",
  bass: "#8b5cf6",
  chords: "#f59e0b",
  lead: "#10b981",
};

export default function LaneCard({
  title,
  laneKey,
  preview,
  generated,
  busy,
  onRegenerate,
  onGenerateAround,
  onSetLocked,
  locked,
  notes,
  barCount,
  tempoBpm,
  /** @type {{ mode: string, onModeChange: (m: string) => void, onSubmit: (m: string) => void } | undefined} */
  suitPart,
}) {
  const accent = LANE_ACCENTS[laneKey] ?? "#64748b";
  const isLocked = !!locked;

  return (
    <article
      style={{
        background: isLocked ? "#fffbeb" : "#fff",
        border: `1px solid ${isLocked ? "#fcd34d" : "#e2e8f0"}`,
        borderLeft: isLocked ? "4px solid #d97706" : "1px solid #e2e8f0",
        borderRadius: 12,
        padding: "1rem 1.25rem",
        minHeight: 140,
        boxShadow: isLocked ? "inset 0 0 0 1px rgba(217, 119, 6, 0.12)" : "none",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <h3 style={{ margin: 0, fontSize: "1rem", textTransform: "capitalize" }}>{title}</h3>
        <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
          <button
            type="button"
            onClick={() => onSetLocked(laneKey, !isLocked)}
            disabled={busy}
            aria-pressed={isLocked}
            title={isLocked ? "Unlock lane (multi-lane regen will include it)" : "Lock lane (multi-lane regen will skip)"}
            style={{
              padding: "0.25rem 0.55rem",
              fontSize: 12,
              fontWeight: 600,
              borderRadius: 6,
              border: `1px solid ${isLocked ? "#d97706" : "#cbd5e1"}`,
              background: isLocked ? "#fef3c7" : "#f8fafc",
              color: isLocked ? "#92400e" : "#475569",
            }}
          >
            {isLocked ? "Locked" : "Lock"}
          </button>
          <button type="button" onClick={() => onRegenerate(laneKey)} disabled={busy || !generated}>
            Regenerate
          </button>
          {typeof onGenerateAround === "function" && (
            <button
              type="button"
              onClick={() => onGenerateAround(laneKey)}
              disabled={busy || !generated}
              title="Sets this lane as anchor and regenerates the other unlocked lanes to fit its rhythm"
              style={{ fontSize: 12 }}
            >
              Generate around this
            </button>
          )}
        </div>
      </div>
      <p style={{ color: "#475569", fontSize: 14, marginTop: "0.75rem", marginBottom: 0 }}>
        {preview || "—"}
      </p>
      <PianoRollPreview
        notes={notes}
        barCount={barCount}
        tempoBpm={tempoBpm}
        generated={generated}
        accent={accent}
      />
      {suitPart && (
        <div
          style={{
            marginTop: "0.65rem",
            paddingTop: "0.65rem",
            borderTop: "1px dashed #e2e8f0",
            display: "flex",
            flexWrap: "wrap",
            alignItems: "center",
            gap: "0.5rem",
          }}
        >
          <span style={{ fontSize: 12, color: "#64748b", fontWeight: 600 }}>Add part to suit</span>
          <select
            value={suitPart.mode}
            onChange={(e) => suitPart.onModeChange(e.target.value)}
            disabled={busy}
            style={{ fontSize: 13 }}
          >
            <option value="solo">solo</option>
            <option value="counter">counter</option>
            <option value="sparse_fill">sparse_fill</option>
          </select>
          <button
            type="button"
            onClick={() => suitPart.onSubmit(suitPart.mode)}
            disabled={busy}
            style={{ padding: "0.3rem 0.65rem", fontSize: 13 }}
          >
            Add Part to Suit
          </button>
        </div>
      )}
    </article>
  );
}
