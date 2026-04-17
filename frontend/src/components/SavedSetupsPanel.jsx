export default function SavedSetupsPanel({
  setups,
  saveName,
  setSaveName,
  busy,
  onRefresh,
  onSave,
  onLoad,
  onApplyToSession,
  activeSessionId,
  onDelete,
}) {
  return (
    <section
      style={{
        background: "#fff",
        border: "1px solid #e2e8f0",
        borderRadius: 12,
        padding: "1rem 1.25rem",
        marginBottom: "1.25rem",
        maxWidth: 720,
      }}
    >
      <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>Saved Setups</h2>
      <p style={{ margin: "0 0 0.75rem", fontSize: 13, color: "#64748b" }}>
        Local favorites stored in <code style={{ fontSize: 12 }}>backend/data/band_setups.json</code>.{" "}
        <strong>Load</strong> fills the new-session and draft controls only (no server update).{" "}
        <strong>Apply to Current Session</strong> PATCHes the active session from this setup (requires a
        session); regenerate lanes afterward to rebuild MIDI.
      </p>
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: "0.5rem",
          alignItems: "flex-end",
          marginBottom: "0.75rem",
        }}
      >
        <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 14, flex: "1 1 180px" }}>
          Name
          <input
            type="text"
            value={saveName}
            onChange={(e) => setSaveName(e.target.value)}
            placeholder="e.g. Late-night fusion"
            maxLength={120}
            disabled={busy}
          />
        </label>
        <button type="button" onClick={onSave} disabled={busy || !saveName.trim()} style={{ padding: "0.45rem 0.85rem" }}>
          Save current styles
        </button>
        <button type="button" onClick={onRefresh} disabled={busy} style={{ padding: "0.45rem 0.85rem" }}>
          Refresh list
        </button>
      </div>
      {setups.length === 0 ? (
        <p style={{ fontSize: 14, color: "#64748b", margin: 0 }}>No saved setups yet.</p>
      ) : (
        <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
          {setups.map((s) => (
            <li
              key={s.name}
              style={{
                display: "flex",
                flexWrap: "wrap",
                alignItems: "center",
                gap: "0.5rem",
                padding: "0.5rem 0",
                borderTop: "1px solid #f1f5f9",
                fontSize: 14,
              }}
            >
              <strong style={{ minWidth: "8rem" }}>{s.name}</strong>
              <span style={{ color: "#64748b", fontSize: 12 }}>
                {s.session_preset ? `preset: ${s.session_preset}` : "no preset"}
                {" · "}
                {s.drum_style}/{s.bass_style}/{s.chord_style}/{s.lead_style}
                {s.bass_player ? ` · bass player ${s.bass_player}` : ""}
                {s.drum_player ? ` · drum player ${s.drum_player}` : ""}
                {s.chord_player ? ` · chord player ${s.chord_player}` : ""}
                {s.lead_player ? ` · lead player ${s.lead_player}` : ""}
                {" · "}
                kit {s.drum_kit ?? "standard"} / bass {s.bass_instrument ?? "finger_bass"} / chords{" "}
                {s.chord_instrument ?? "piano"} / lead {s.lead_instrument ?? "flute"}
                {s.tempo != null ? ` · ${s.tempo} bpm` : ""}
                {s.key ? ` · ${s.key}` : ""}
                {s.scale ? ` ${s.scale}` : ""}
              </span>
              <span style={{ flex: 1 }} />
              <button type="button" onClick={() => onLoad(s)} disabled={busy} style={{ padding: "0.25rem 0.6rem" }}>
                Load
              </button>
              <button
                type="button"
                onClick={() => onApplyToSession(s)}
                disabled={busy || !activeSessionId}
                title={!activeSessionId ? "Create or open a session first" : undefined}
                style={{ padding: "0.25rem 0.6rem" }}
              >
                Apply to Current Session
              </button>
              <button
                type="button"
                onClick={() => onDelete(s.name)}
                disabled={busy}
                style={{ padding: "0.25rem 0.6rem", color: "#b91c1c" }}
              >
                Delete
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
