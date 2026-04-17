const MAX_DRAW_NOTES = 600;

/**
 * Compact read-only piano-roll strip: time on X (from bar_count + tempo), pitch on Y.
 */
export default function PianoRollPreview({
  notes = [],
  barCount = 8,
  tempoBpm = 120,
  generated = false,
  accent = "#64748b",
}) {
  const shell = {
    marginTop: "0.5rem",
    borderRadius: 8,
    border: "1px solid #e2e8f0",
    background: "#f8fafc",
    overflow: "hidden",
    height: 76,
    position: "relative",
  };

  if (!generated) {
    return (
      <div style={{ ...shell, display: "flex", alignItems: "center", justifyContent: "center" }}>
        <span style={{ fontSize: 12, color: "#94a3b8" }}>Piano roll after generation</span>
      </div>
    );
  }

  if (!notes.length) {
    return (
      <div style={{ ...shell, display: "flex", alignItems: "center", justifyContent: "center" }}>
        <span style={{ fontSize: 12, color: "#94a3b8" }}>No notes in this lane</span>
      </div>
    );
  }

  const axisSec =
    barCount > 0 && tempoBpm > 0 ? barCount * 4 * (60 / tempoBpm) : 1;
  const slice = notes.length > MAX_DRAW_NOTES ? notes.slice(0, MAX_DRAW_NOTES) : notes;
  const pitches = slice.map((n) => n.pitch);
  const minP = Math.min(...pitches) - 2;
  const maxP = Math.max(...pitches) + 2;
  const pSpan = Math.max(maxP - minP, 1);
  const viewW = Math.max(axisSec, 1e-6);
  const viewH = pSpan;

  const barLines = [];
  const bc = Math.max(barCount, 1);
  for (let b = 0; b <= bc; b += 1) {
    barLines.push((b / bc) * axisSec);
  }

  return (
    <div style={shell} aria-hidden>
      <svg
        width="100%"
        height="100%"
        viewBox={`0 0 ${viewW} ${viewH}`}
        preserveAspectRatio="none"
        style={{ display: "block" }}
      >
        {barLines.map((x, i) => (
          <line
            key={`bar-${i}`}
            x1={x}
            x2={x}
            y1={0}
            y2={viewH}
            stroke={i === 0 || i === bc ? "#cbd5e1" : "#e8eef4"}
            strokeWidth={viewW * 0.0015}
          />
        ))}
        {slice.map((n, i) => {
          const dur = Math.max(n.end - n.start, viewW * 0.0025);
          const x = n.start;
          const y = maxP - n.pitch - 0.45;
          const h = 0.9;
          const alpha = 0.28 + (n.velocity / 127) * 0.72;
          return (
            <rect
              key={`${n.start}-${n.pitch}-${i}`}
              x={x}
              y={y}
              width={dur}
              height={h}
              rx={viewW * 0.002}
              fill={accent}
              fillOpacity={alpha}
            />
          );
        })}
      </svg>
    </div>
  );
}
