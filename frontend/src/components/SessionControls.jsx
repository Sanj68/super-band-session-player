const SCALES = [
  "major",
  "natural_minor",
  "harmonic_minor",
  "melodic_minor",
  "dorian",
  "mixolydian",
  "pentatonic_major",
  "pentatonic_minor",
  "blues",
];

const KEYS = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"];

const LEAD_STYLES = [
  { value: "melodic", label: "Melodic (default)" },
  { value: "sparse", label: "Sparse" },
  { value: "sparse_emotional", label: "Sparse emotional" },
  { value: "rhythmic", label: "Rhythmic" },
  { value: "bluesy", label: "Bluesy" },
  { value: "fusion", label: "Fusion" },
];

const LEAD_PLAYERS = [
  { value: "", label: "None (style only)" },
  { value: "coltrane", label: "Coltrane-style intensity arc" },
  { value: "cal_tjader", label: "Cal Tjader-style lyrical sync" },
  { value: "soul_sparse", label: "Soul sparse restraint" },
  { value: "funk_phrasing", label: "Funk hook phrasing" },
];

const BASS_STYLES = [
  { value: "supportive", label: "Supportive (default)" },
  { value: "melodic", label: "Melodic" },
  { value: "rhythmic", label: "Rhythmic" },
  { value: "slap", label: "Slap" },
  { value: "fusion", label: "Fusion" },
];

const BASS_ENGINES = [
  { value: "baseline", label: "Baseline" },
  { value: "phrase_v2", label: "Phrase Engine v2" },
];

const CHORD_STYLES = [
  { value: "simple", label: "Simple (default)" },
  { value: "jazzy", label: "Jazzy" },
  { value: "wide", label: "Wide" },
  { value: "dense", label: "Dense" },
  { value: "stabs", label: "Stabs" },
  { value: "warm_broken", label: "Warm broken" },
];

const CHORD_PLAYERS = [
  { value: "", label: "None (style only)" },
  { value: "herbie", label: "Herbie-style color comp" },
  { value: "barry_miles", label: "Barry Miles-style modal bed" },
  { value: "soul_keys", label: "Soul keys warmth" },
  { value: "funk_stabs", label: "Funk stab punctuation" },
];

const DRUM_STYLES = [
  { value: "straight", label: "Straight (default)" },
  { value: "broken", label: "Broken" },
  { value: "shuffle", label: "Shuffle" },
  { value: "funk", label: "Funk" },
  { value: "latin", label: "Latin" },
  { value: "laid_back_soul", label: "Laid-back soul" },
];

const LEAD_INSTRUMENTS = [
  { value: "flute", label: "Flute (default)" },
  { value: "vibes", label: "Vibes" },
  { value: "guitar", label: "Guitar" },
  { value: "synth_lead", label: "Synth lead" },
];

const BASS_INSTRUMENTS = [
  { value: "finger_bass", label: "Finger bass (default)" },
  { value: "slap_bass", label: "Slap bass" },
  { value: "synth_bass", label: "Synth bass" },
];

const BASS_PLAYERS = [
  { value: "", label: "None (style only)" },
  { value: "bootsy", label: "Bootsy-style pocket" },
  { value: "marcus", label: "Marcus-style fusion line" },
  { value: "pino", label: "Pino-style soul contour" },
];

const CHORD_INSTRUMENTS = [
  { value: "piano", label: "Piano (default)" },
  { value: "rhodes", label: "Rhodes" },
  { value: "organ", label: "Organ" },
  { value: "pad", label: "Pad" },
];

const DRUM_KITS = [
  { value: "standard", label: "Standard kit (default)" },
  { value: "dry", label: "Dry kit" },
  { value: "percussion", label: "Percussion kit" },
];

const DRUM_PLAYERS = [
  { value: "", label: "None (style only)" },
  { value: "stubblefield", label: "Stubblefield-style pocket" },
  { value: "questlove", label: "Questlove-style laid-back" },
  { value: "dilla", label: "Dilla-style swung loop" },
];

/** When user picks a preset, lane dropdowns sync to these defaults (explicit picks still override on create). */
const SESSION_PRESET_DEFAULTS = {
  latin_jazz: { drum: "latin", bass: "supportive", chord: "jazzy", lead: "melodic" },
  fusion: { drum: "funk", bass: "fusion", chord: "wide", lead: "fusion" },
  cool_modal: { drum: "shuffle", bass: "melodic", chord: "wide", lead: "sparse" },
  dusty_broken: { drum: "broken", bass: "supportive", chord: "stabs", lead: "bluesy" },
  soulful_funk: { drum: "funk", bass: "slap", chord: "dense", lead: "rhythmic" },
  rare_groove_soul: {
    drum: "laid_back_soul",
    bass: "supportive",
    chord: "warm_broken",
    lead: "sparse_emotional",
  },
};

const SESSION_PRESETS = [
  { value: "", label: "None (default lane styles)" },
  { value: "latin_jazz", label: "Latin jazz" },
  { value: "fusion", label: "Fusion" },
  { value: "cool_modal", label: "Cool / modal" },
  { value: "dusty_broken", label: "Dusty broken" },
  { value: "soulful_funk", label: "Soulful funk" },
  { value: "rare_groove_soul", label: "Rare groove soul" },
];

export default function SessionControls({
  tempo,
  setTempo,
  keyNote,
  setKeyNote,
  scale,
  setScale,
  bars,
  setBars,
  leadStyle,
  setLeadStyle,
  leadPlayer,
  setLeadPlayer,
  bassStyle,
  setBassStyle,
  bassEngine,
  setBassEngine,
  chordStyle,
  setChordStyle,
  chordPlayer,
  setChordPlayer,
  drumStyle,
  setDrumStyle,
  leadInstrument,
  setLeadInstrument,
  bassInstrument,
  setBassInstrument,
  bassPlayer,
  setBassPlayer,
  chordInstrument,
  setChordInstrument,
  drumKit,
  setDrumKit,
  drumPlayer,
  setDrumPlayer,
  sessionPreset,
  setSessionPreset,
  anchorLane,
  onAnchorLaneChange,
  busy,
  onGenerate,
}) {
  const onSessionPresetChange = (value) => {
    setSessionPreset(value);
    const d = SESSION_PRESET_DEFAULTS[value];
    if (d) {
      setDrumStyle(d.drum);
      setBassStyle(d.bass);
      setChordStyle(d.chord);
      setLeadStyle(d.lead);
    }
  };

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
      <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>Session</h2>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
          gap: "0.75rem",
          alignItems: "end",
        }}
      >
        <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 14 }}>
          Tempo (BPM)
          <input
            type="number"
            min={40}
            max={240}
            value={tempo}
            onChange={(e) => setTempo(Number(e.target.value))}
          />
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 14 }}>
          Key
          <select value={keyNote} onChange={(e) => setKeyNote(e.target.value)}>
            {KEYS.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 14 }}>
          Scale
          <select value={scale} onChange={(e) => setScale(e.target.value)}>
            {SCALES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 14 }}>
          Bars
          <input
            type="number"
            min={1}
            max={128}
            value={bars}
            onChange={(e) => setBars(Number(e.target.value))}
          />
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 14 }}>
          Session preset
          <select
            value={sessionPreset}
            onChange={(e) => onSessionPresetChange(e.target.value)}
          >
            {SESSION_PRESETS.map((p) => (
              <option key={p.value || "none"} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 14 }}>
          Lead style
          <select value={leadStyle} onChange={(e) => setLeadStyle(e.target.value)}>
            {LEAD_STYLES.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 14 }}>
          Lead player
          <select value={leadPlayer} onChange={(e) => setLeadPlayer(e.target.value)}>
            {LEAD_PLAYERS.map((s) => (
              <option key={s.value || "none"} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 14 }}>
          Lead instrument
          <select value={leadInstrument} onChange={(e) => setLeadInstrument(e.target.value)}>
            {LEAD_INSTRUMENTS.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 14 }}>
          Bass style
          <select value={bassStyle} onChange={(e) => setBassStyle(e.target.value)}>
            {BASS_STYLES.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 14 }}>
          Bass engine
          <select value={bassEngine} onChange={(e) => setBassEngine(e.target.value)}>
            {BASS_ENGINES.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 14 }}>
          Bass instrument
          <select value={bassInstrument} onChange={(e) => setBassInstrument(e.target.value)}>
            {BASS_INSTRUMENTS.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 14 }}>
          Bass player
          <select value={bassPlayer} onChange={(e) => setBassPlayer(e.target.value)}>
            {BASS_PLAYERS.map((s) => (
              <option key={s.value || "none"} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 14 }}>
          Chord style
          <select value={chordStyle} onChange={(e) => setChordStyle(e.target.value)}>
            {CHORD_STYLES.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 14 }}>
          Chord player
          <select value={chordPlayer} onChange={(e) => setChordPlayer(e.target.value)}>
            {CHORD_PLAYERS.map((s) => (
              <option key={s.value || "none"} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 14 }}>
          Chord instrument
          <select value={chordInstrument} onChange={(e) => setChordInstrument(e.target.value)}>
            {CHORD_INSTRUMENTS.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 14 }}>
          Drum style
          <select value={drumStyle} onChange={(e) => setDrumStyle(e.target.value)}>
            {DRUM_STYLES.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 14 }}>
          Drum player
          <select value={drumPlayer} onChange={(e) => setDrumPlayer(e.target.value)}>
            {DRUM_PLAYERS.map((s) => (
              <option key={s.value || "none"} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 14 }}>
          Drum kit
          <select value={drumKit} onChange={(e) => setDrumKit(e.target.value)}>
            {DRUM_KITS.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 14 }}>
          Start from
          <select value={anchorLane} onChange={(e) => onAnchorLaneChange(e.target.value)} disabled={busy}>
            <option value="">Default (full session order)</option>
            <option value="drums">Drums</option>
            <option value="bass">Bass</option>
            <option value="chords">Chords</option>
            <option value="lead">Lead</option>
          </select>
        </label>
      </div>
      <div style={{ marginTop: "1rem" }}>
        <button type="button" onClick={onGenerate} disabled={busy} style={{ padding: "0.5rem 1rem" }}>
          {busy ? "Working…" : "Generate Session"}
        </button>
      </div>
    </section>
  );
}
