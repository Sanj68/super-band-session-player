const STYLE_OPTIONS = [
  { value: "supportive", label: "Supportive" },
  { value: "melodic", label: "Melodic" },
  { value: "rhythmic", label: "Rhythmic" },
  { value: "slap", label: "Slap" },
  { value: "fusion", label: "Fusion" },
];

const TOUCH_OPTIONS = [
  {
    value: "natural",
    label: "Natural",
    description: "The player chooses from the available techniques.",
  },
  {
    value: "clean",
    label: "Clean",
    description: "No added ghost, muted, or connected-note intent.",
  },
  {
    value: "ghosted",
    label: "Ghosted",
    description: "Favour short, low ghost-note punctuation.",
  },
  {
    value: "muted",
    label: "Muted",
    description: "Favour percussive dead-note punctuation.",
  },
  {
    value: "connected",
    label: "Connected",
    description: "Favour grace, hammer-on, and legato arrivals.",
  },
];

const SUPPORTED_TOUCHES = {
  finger_bass: new Set(["natural", "clean", "ghosted", "muted", "connected"]),
  fretless_bass: new Set(["natural", "clean", "connected"]),
  upright_bass: new Set(["natural", "clean", "ghosted", "connected"]),
  sub_bass: new Set(["natural", "clean"]),
};

export const DEFAULT_BASS_PERFORMANCE_CONTROLS = Object.freeze({
  ghost: 0.2,
  mute: 0.05,
  slide: 0.25,
  legato: 0.25,
  timing_humanize: 0.5,
  velocity_humanize: 0.5,
});

const PERFORMANCE_PRESETS = [
  {
    key: "clean",
    label: "Clean",
    touch: "clean",
    controls: {
      ghost: 0,
      mute: 0,
      slide: 0,
      legato: 0,
      timing_humanize: 0.15,
      velocity_humanize: 0.2,
    },
  },
  {
    key: "natural",
    label: "Natural",
    touch: "natural",
    controls: DEFAULT_BASS_PERFORMANCE_CONTROLS,
  },
  {
    key: "expressive_fusion",
    label: "Expressive Fusion",
    style: "fusion",
    touch: "natural",
    controls: {
      ghost: 0.62,
      mute: 0.24,
      slide: 0.8,
      legato: 0.68,
      timing_humanize: 0.58,
      velocity_humanize: 0.72,
    },
  },
];

const PERFORMANCE_CONTROL_OPTIONS = [
  {
    key: "ghost",
    label: "Ghosts",
    description: "Quiet offbeat punctuation between the main notes.",
  },
  {
    key: "mute",
    label: "Mutes",
    description: "Short percussive dead-note attacks.",
  },
  {
    key: "slide",
    label: "Slides",
    description: "Pitch movement into selected target notes.",
  },
  {
    key: "legato",
    label: "Legato",
    description: "Connected hammer-on style arrivals.",
  },
  {
    key: "timing_humanize",
    label: "Timing Feel",
    description: "Natural variation around the grid.",
  },
  {
    key: "velocity_humanize",
    label: "Dynamics",
    description: "Natural variation in note strength.",
  },
];

const PERFORMANCE_CAPS = {
  finger_bass: { ghost: 1, mute: 1, slide: 1, legato: 1 },
  fretless_bass: { ghost: 0, mute: 0, slide: 1, legato: 1 },
  upright_bass: { ghost: 0.75, mute: 0, slide: 0.7, legato: 0.7 },
  sub_bass: { ghost: 0, mute: 0, slide: 0, legato: 0 },
};

function clampUnit(value, fallback) {
  const number = Number(value);
  if (!Number.isFinite(number)) return fallback;
  return Math.max(0, Math.min(1, number));
}

export function normalizeBassPerformanceControls(controls) {
  const source = controls && typeof controls === "object" ? controls : {};
  return Object.fromEntries(
    PERFORMANCE_CONTROL_OPTIONS.map(({ key }) => [
      key,
      clampUnit(source[key], DEFAULT_BASS_PERFORMANCE_CONTROLS[key]),
    ]),
  );
}

function normalizedFamily(instrument) {
  if (instrument === "slap_bass") return "finger_bass";
  if (instrument === "synth_bass") return "sub_bass";
  return instrument in SUPPORTED_TOUCHES ? instrument : "finger_bass";
}

function activityLabel(value) {
  if (value <= -0.75) return "Minimal";
  if (value <= -0.25) return "Sparse";
  if (value < 0.25) return "Balanced";
  if (value < 0.75) return "Busy";
  return "Lead";
}

function characterLabel(value) {
  if (value < 0.125) return "Restrained";
  if (value < 0.375) return "Subtle";
  if (value < 0.625) return "Natural";
  if (value < 0.875) return "Expressive";
  return "Bold";
}

export default function BassPreferencesPanel({
  session,
  busy,
  style,
  setStyle,
  touch,
  setTouch,
  activity,
  setActivity,
  character,
  setCharacter,
  performanceControls,
  setPerformanceControls,
  effectivePerformanceControls,
  performanceControlsNotice,
  instrument,
  onGenerate,
  onResetForNewBeat,
}) {
  const family = normalizedFamily(instrument);
  const supported = SUPPORTED_TOUCHES[family];
  const touchOption = TOUCH_OPTIONS.find((option) => option.value === touch) ?? TOUCH_OPTIONS[0];
  const unavailable = !supported.has(touch);
  const styleLabel = STYLE_OPTIONS.find((option) => option.value === style)?.label ?? style;
  const requestedControls = normalizeBassPerformanceControls(performanceControls);
  const sessionRequestedControls = normalizeBassPerformanceControls(
    session?.bass_performance_controls,
  );
  const draftMatchesSession = PERFORMANCE_CONTROL_OPTIONS.every(
    ({ key }) => Math.abs(requestedControls[key] - sessionRequestedControls[key]) <= 0.005,
  );
  const predictedCaps = PERFORMANCE_CAPS[family] ?? PERFORMANCE_CAPS.finger_bass;
  const predictedControls = Object.fromEntries(
    PERFORMANCE_CONTROL_OPTIONS.map(({ key }) => [
      key,
      Math.min(requestedControls[key], predictedCaps[key] ?? 1),
    ]),
  );
  const effectiveControls =
    draftMatchesSession && effectivePerformanceControls
      ? normalizeBassPerformanceControls(effectivePerformanceControls)
      : predictedControls;
  const cappedLabels = PERFORMANCE_CONTROL_OPTIONS.filter(
    ({ key }) => Math.abs(requestedControls[key] - effectiveControls[key]) > 0.005,
  ).map(({ label }) => label);
  const displayedPerformanceNotice = draftMatchesSession
    ? performanceControlsNotice
    : cappedLabels.length > 0
      ? `${instrument?.replaceAll("_", " ") ?? "This instrument"} will limit ${cappedLabels.join(", ")} when you generate.`
      : null;
  const grooveSourceReadinessAvailable =
    typeof session?.groove_source_ready === "boolean";
  const grooveSourceReady = session?.groove_source_ready === true;
  const grooveSourceFrameCount = Number.isFinite(
    Number(session?.groove_source_frame_count),
  )
    ? Math.max(0, Number(session.groove_source_frame_count))
    : null;
  const newBeatStatus = grooveSourceReadinessAvailable
    ? grooveSourceReady
      ? `Captured beat source ready${
          grooveSourceFrameCount === null
            ? ""
            : ` · ${grooveSourceFrameCount} groove frame${
                grooveSourceFrameCount === 1 ? "" : "s"
              }`
        }.`
      : session?.groove_source_notice ??
        "Beat source not connected. Reset can make a fresh phrase, but it cannot analyse the beat."
    : "Reset always creates a fresh phrase. Groove analysis is only used when a beat source is connected.";

  const applyPreset = (preset) => {
    if (preset.style) setStyle(preset.style);
    if (preset.touch) setTouch(preset.touch);
    setPerformanceControls({ ...preset.controls });
  };

  const updatePerformanceControl = (key, value) => {
    setPerformanceControls({
      ...requestedControls,
      [key]: clampUnit(value, requestedControls[key]),
    });
  };

  return (
    <div
      style={{
        marginBottom: "0.85rem",
        padding: "0.8rem",
        border: "1px solid var(--border-strong)",
        borderRadius: 10,
        background: "var(--panel-2)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          gap: "0.75rem",
          marginBottom: "0.65rem",
        }}
      >
        <strong>Player intent</strong>
        <span style={{ color: "var(--text-faint)", fontSize: 12 }}>
          Changes apply only when you generate.
        </span>
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
          gap: "0.7rem",
          alignItems: "end",
        }}
      >
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          Style
          <select
            value={style}
            onChange={(event) => setStyle(event.target.value)}
            disabled={busy}
          >
            {STYLE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>

        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          Touch
          <select
            value={touch}
            onChange={(event) => setTouch(event.target.value)}
            disabled={busy}
            title={touchOption.description}
          >
            {TOUCH_OPTIONS.map((option) => (
              <option
                key={option.value}
                value={option.value}
                disabled={!supported.has(option.value)}
              >
                {option.label}
              </option>
            ))}
          </select>
        </label>

        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span>
            Activity <strong style={{ color: "var(--text)" }}>{activityLabel(activity)}</strong>
          </span>
          <input
            type="range"
            min="-1"
            max="1"
            step="0.5"
            value={activity}
            onChange={(event) => setActivity(Number(event.target.value))}
            disabled={busy}
            aria-valuetext={activityLabel(activity)}
            title="Five musical gears: Minimal, Sparse, Balanced, Busy, and Lead."
          />
        </label>

        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span>
            Character{" "}
            <strong style={{ color: "var(--text)" }}>{characterLabel(character)}</strong>
          </span>
          <input
            type="range"
            min="0"
            max="1"
            step="0.25"
            value={character}
            onChange={(event) => setCharacter(Number(event.target.value))}
            disabled={busy}
            aria-valuetext={characterLabel(character)}
            title="Five contour gears: Restrained, Subtle, Natural, Expressive, and Bold."
          />
        </label>
      </div>

      <div
        style={{
          marginTop: "0.85rem",
          paddingTop: "0.8rem",
          borderTop: "1px solid var(--border)",
        }}
      >
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            alignItems: "center",
            justifyContent: "space-between",
            gap: "0.55rem",
            marginBottom: "0.7rem",
          }}
        >
          <span>
            <strong>Performance</strong>{" "}
            <span style={{ color: "var(--text-faint)", fontSize: 12 }}>
              Blend techniques independently.
            </span>
          </span>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "0.4rem" }}>
            {PERFORMANCE_PRESETS.map((preset) => (
              <button
                key={preset.key}
                type="button"
                className={preset.key === "expressive_fusion" ? "btn-primary" : "btn-ghost"}
                onClick={() => applyPreset(preset)}
                disabled={busy}
                style={{ padding: "0.32rem 0.58rem", fontSize: 12 }}
              >
                {preset.label}
              </button>
            ))}
          </div>
        </div>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(185px, 1fr))",
            gap: "0.65rem 0.9rem",
          }}
        >
          {PERFORMANCE_CONTROL_OPTIONS.map((option) => {
            const requested = requestedControls[option.key];
            const effective = effectiveControls[option.key];
            const capped = Math.abs(requested - effective) > 0.005;
            return (
              <label
                key={option.key}
                title={option.description}
                style={{ display: "flex", flexDirection: "column", gap: 4 }}
              >
                <span
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    gap: "0.5rem",
                    fontSize: 13,
                  }}
                >
                  <span>{option.label}</span>
                  <span style={{ color: capped ? "var(--warn)" : "var(--text-muted)" }}>
                    {Math.round(requested * 100)}%
                    {capped ? ` · plays ${Math.round(effective * 100)}%` : ""}
                  </span>
                </span>
                <input
                  type="range"
                  min="0"
                  max="1"
                  step="0.01"
                  value={requested}
                  onChange={(event) =>
                    updatePerformanceControl(option.key, Number(event.target.value))
                  }
                  disabled={busy}
                  aria-label={option.label}
                />
              </label>
            );
          })}
        </div>

        {displayedPerformanceNotice && (
          <div
            role="status"
            style={{
              marginTop: "0.65rem",
              padding: "0.5rem 0.6rem",
              borderRadius: 7,
              background: "color-mix(in srgb, var(--warn) 12%, transparent)",
              color: "var(--text-muted)",
              fontSize: 12,
            }}
          >
            {displayedPerformanceNotice}
          </div>
        )}
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "minmax(220px, 1fr) auto",
          alignItems: "end",
          gap: "0.65rem 1rem",
          marginTop: "0.75rem",
        }}
      >
        <div style={{ display: "flex", flexDirection: "column", gap: "0.22rem" }}>
          <span
            style={{
              color: unavailable ? "var(--warn)" : "var(--text-muted)",
              fontSize: 13,
            }}
          >
            {unavailable
              ? `${touchOption.label} is unavailable for this bass family; the engine will render Clean.`
              : `Next idea: ${styleLabel} · ${touchOption.label} · ${activityLabel(activity)} · ${characterLabel(character)}`}
          </span>
          <span style={{ color: "var(--text-faint)", fontSize: 12 }}>
            Generate can keep the phrase when only touch or performance changes. NEW BEAT
            always replaces its note pattern.
          </span>
          <span
            role="status"
            style={{
              color:
                grooveSourceReadinessAvailable && !grooveSourceReady
                  ? "var(--warn)"
                  : "var(--text-faint)",
              fontSize: 12,
            }}
          >
            {newBeatStatus}
          </span>
        </div>
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            justifyContent: "flex-end",
            gap: "0.5rem",
          }}
        >
          <button
            type="button"
            className="btn-primary"
            onClick={onGenerate}
            disabled={busy || !session?.id}
            title="Apply the selected intent; performance-only changes can keep the current phrase"
          >
            Generate Bass Idea
          </button>
          <button
            type="button"
            className="btn-ghost"
            onClick={onResetForNewBeat}
            disabled={busy || !session?.id}
            title="Persist these preferences and discard the current bass phrase before regenerating"
          >
            NEW BEAT / RESET
          </button>
        </div>
      </div>
    </div>
  );
}
