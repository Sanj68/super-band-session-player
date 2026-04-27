import { useCallback, useEffect, useState } from "react";
import {
  addPartToSuit,
  createSession,
  createTakeEvaluation,
  createSetup,
  deleteSetup,
  duplicateSession,
  downloadExportZip,
  generateAroundAnchor,
  generateSession,
  getEvaluationSummary,
  getSession,
  getSavedSetupAsSessionPatch,
  getClipEvaluation,
  listSetups,
  patchLaneLocks,
  patchSession,
  regenerateLane,
  regenerateSelectedLanes,
  regenerateUnlockedLanes,
  setClipReferenceNotes,
} from "./api/client.js";
import LaneCard from "./components/LaneCard.jsx";
import BassCandidatePanel from "./components/BassCandidatePanel.jsx";
import ReferenceAudioPanel from "./components/ReferenceAudioPanel.jsx";
import SavedSetupsPanel from "./components/SavedSetupsPanel.jsx";
import SessionComparePanel from "./components/SessionComparePanel.jsx";
import SessionControls from "./components/SessionControls.jsx";
import UploadFirstEntryPanel from "./components/UploadFirstEntryPanel.jsx";

const ACTIVE_LEAD_OPTIONS = [
  { value: "melodic", label: "Melodic" },
  { value: "sparse", label: "Sparse" },
  { value: "sparse_emotional", label: "Sparse emotional" },
  { value: "rhythmic", label: "Rhythmic" },
  { value: "bluesy", label: "Bluesy" },
  { value: "fusion", label: "Fusion" },
];

const ACTIVE_BASS_OPTIONS = [
  { value: "supportive", label: "Supportive" },
  { value: "melodic", label: "Melodic" },
  { value: "rhythmic", label: "Rhythmic" },
  { value: "slap", label: "Slap" },
  { value: "fusion", label: "Fusion" },
];

const ACTIVE_CHORD_OPTIONS = [
  { value: "simple", label: "Simple" },
  { value: "jazzy", label: "Jazzy" },
  { value: "wide", label: "Wide" },
  { value: "dense", label: "Dense" },
  { value: "stabs", label: "Stabs" },
  { value: "warm_broken", label: "Warm broken" },
];

const ACTIVE_DRUM_OPTIONS = [
  { value: "straight", label: "Straight" },
  { value: "broken", label: "Broken" },
  { value: "shuffle", label: "Shuffle" },
  { value: "funk", label: "Funk" },
  { value: "latin", label: "Latin" },
  { value: "laid_back_soul", label: "Laid-back soul" },
];

const ACTIVE_SESSION_PRESET_OPTIONS = [
  { value: "latin_jazz", label: "Latin jazz" },
  { value: "fusion", label: "Fusion" },
  { value: "cool_modal", label: "Cool / modal" },
  { value: "dusty_broken", label: "Dusty broken" },
  { value: "soulful_funk", label: "Soulful funk" },
  { value: "rare_groove_soul", label: "Rare groove soul" },
];

const ACTIVE_LEAD_INSTRUMENTS = [
  { value: "flute", label: "Flute" },
  { value: "vibes", label: "Vibes" },
  { value: "guitar", label: "Guitar" },
  { value: "synth_lead", label: "Synth lead" },
];

const ACTIVE_BASS_INSTRUMENTS = [
  { value: "finger_bass", label: "Finger bass" },
  { value: "slap_bass", label: "Slap bass" },
  { value: "synth_bass", label: "Synth bass" },
];

const ACTIVE_CHORD_INSTRUMENTS = [
  { value: "piano", label: "Piano" },
  { value: "rhodes", label: "Rhodes" },
  { value: "organ", label: "Organ" },
  { value: "pad", label: "Pad" },
];

const ACTIVE_DRUM_KITS = [
  { value: "standard", label: "Standard" },
  { value: "dry", label: "Dry" },
  { value: "percussion", label: "Percussion" },
];

const REGEN_LANE_KEYS = ["drums", "bass", "chords", "lead"];

export default function App() {
  const [tempo, setTempo] = useState(108);
  const [keyNote, setKeyNote] = useState("C");
  const [scale, setScale] = useState("major");
  const [bars, setBars] = useState(8);
  const [leadStyle, setLeadStyle] = useState("melodic");
  const [leadPlayer, setLeadPlayer] = useState("");
  const [bassStyle, setBassStyle] = useState("supportive");
  const [bassEngine, setBassEngine] = useState("baseline");
  const [chordStyle, setChordStyle] = useState("simple");
  const [chordPlayer, setChordPlayer] = useState("");
  const [drumStyle, setDrumStyle] = useState("straight");
  const [leadInstrument, setLeadInstrument] = useState("flute");
  const [bassInstrument, setBassInstrument] = useState("finger_bass");
  const [bassPlayer, setBassPlayer] = useState("");
  const [drumPlayer, setDrumPlayer] = useState("");
  const [chordInstrument, setChordInstrument] = useState("piano");
  const [drumKit, setDrumKit] = useState("standard");
  const [newSessionPreset, setNewSessionPreset] = useState("");
  const [anchorLane, setAnchorLane] = useState("");
  const [session, setSession] = useState(null);
  const [activeLeadDraft, setActiveLeadDraft] = useState("melodic");
  const [activeBassDraft, setActiveBassDraft] = useState("supportive");
  const [activeBassEngineDraft, setActiveBassEngineDraft] = useState("baseline");
  const [activeChordDraft, setActiveChordDraft] = useState("simple");
  const [activeDrumDraft, setActiveDrumDraft] = useState("straight");
  const [activeLeadPlayerDraft, setActiveLeadPlayerDraft] = useState("");
  const [activeLeadInstrumentDraft, setActiveLeadInstrumentDraft] = useState("flute");
  const [activeBassInstrumentDraft, setActiveBassInstrumentDraft] = useState("finger_bass");
  const [activeBassPlayerDraft, setActiveBassPlayerDraft] = useState("");
  const [activeDrumPlayerDraft, setActiveDrumPlayerDraft] = useState("");
  const [activeChordPlayerDraft, setActiveChordPlayerDraft] = useState("");
  const [activeChordInstrumentDraft, setActiveChordInstrumentDraft] = useState("piano");
  const [activeDrumKitDraft, setActiveDrumKitDraft] = useState("standard");
  const [activePresetDraft, setActivePresetDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [status, setStatus] = useState(null);
  const [setups, setSetups] = useState([]);
  const [saveSetupName, setSaveSetupName] = useState("");
  const [selectedRegenLanes, setSelectedRegenLanes] = useState({
    drums: false,
    bass: false,
    chords: false,
    lead: false,
  });
  /** Deep-copied SessionState for A/B compare (frontend only). */
  const [sessionSnapshotA, setSessionSnapshotA] = useState(null);
  const [compareModeOpen, setCompareModeOpen] = useState(false);
  const [abMessage, setAbMessage] = useState("");
  const [leadSuitMode, setLeadSuitMode] = useState("counter");
  const [evalClipId, setEvalClipId] = useState("");
  const [evalReferenceNotes, setEvalReferenceNotes] = useState("");
  const [evalTakeId, setEvalTakeId] = useState("");
  const [evalTakeNotes, setEvalTakeNotes] = useState("");
  const [evalScores, setEvalScores] = useState({
    groove_fit: 3,
    harmonic_fit: 3,
    phrase_feel: 3,
    articulation_feel: 3,
    usefulness: 3,
  });
  const [evalTakes, setEvalTakes] = useState([]);
  const [evalSummary, setEvalSummary] = useState(null);

  const refreshSetups = useCallback(async () => {
    try {
      const data = await listSetups();
      setSetups(Array.isArray(data.setups) ? data.setups : []);
    } catch (e) {
      setError(e.message || String(e));
    }
  }, []);

  useEffect(() => {
    refreshSetups();
  }, [refreshSetups]);

  useEffect(() => {
    setSelectedRegenLanes({ drums: false, bass: false, chords: false, lead: false });
  }, [session?.id]);

  useEffect(() => {
    if (!session?.id) return;
    const defaultClipId = session.id;
    setEvalClipId(defaultClipId);
    setEvalTakeId(`${defaultClipId}-take-${Date.now()}`);
  }, [session?.id]);

  useEffect(() => {
    if (!abMessage) return undefined;
    const t = setTimeout(() => setAbMessage(""), 3200);
    return () => clearTimeout(t);
  }, [abMessage]);

  useEffect(() => {
    if (session?.lead_style) {
      setActiveLeadDraft(session.lead_style);
    }
    if (session?.bass_style) {
      setActiveBassDraft(session.bass_style);
    }
    if (session?.chord_style) {
      setActiveChordDraft(session.chord_style);
    }
    if (session?.drum_style) {
      setActiveDrumDraft(session.drum_style);
    }
    if (session?.session_preset != null && session.session_preset !== "") {
      setActivePresetDraft(session.session_preset);
    } else {
      setActivePresetDraft("");
    }
    setActiveLeadPlayerDraft(session?.lead_player ?? "");
    setActiveLeadInstrumentDraft(session?.lead_instrument ?? "flute");
    setActiveBassInstrumentDraft(session?.bass_instrument ?? "finger_bass");
    setActiveBassPlayerDraft(session?.bass_player ?? "");
    setActiveBassEngineDraft(session?.bass_engine ?? "baseline");
    setActiveDrumPlayerDraft(session?.drum_player ?? "");
    setActiveChordPlayerDraft(session?.chord_player ?? "");
    setActiveChordInstrumentDraft(session?.chord_instrument ?? "piano");
    setActiveDrumKitDraft(session?.drum_kit ?? "standard");
    setAnchorLane(session?.anchor_lane ?? "");
  }, [
    session?.id,
    session?.anchor_lane,
    session?.lead_style,
    session?.bass_style,
    session?.chord_style,
    session?.drum_style,
    session?.session_preset,
    session?.lead_player,
    session?.lead_instrument,
    session?.bass_instrument,
    session?.bass_player,
    session?.bass_engine,
    session?.drum_player,
    session?.chord_player,
    session?.chord_instrument,
    session?.drum_kit,
  ]);

  const onGenerate = useCallback(async () => {
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      const body = {
        tempo,
        key: keyNote,
        scale,
        bar_count: bars,
        lead_style: leadStyle,
        bass_style: bassStyle,
        bass_engine: bassEngine,
        chord_style: chordStyle,
        drum_style: drumStyle,
        lead_instrument: leadInstrument,
        bass_instrument: bassInstrument,
        chord_instrument: chordInstrument,
        drum_kit: drumKit,
      };
      if (newSessionPreset) {
        body.session_preset = newSessionPreset;
      }
      if (bassPlayer) {
        body.bass_player = bassPlayer;
      }
      if (drumPlayer) {
        body.drum_player = drumPlayer;
      }
      if (chordPlayer) {
        body.chord_player = chordPlayer;
      }
      if (leadPlayer) {
        body.lead_player = leadPlayer;
      }
      if (anchorLane) {
        body.anchor_lane = anchorLane;
      }
      const created = await createSession(body);
      const gen = await generateSession(created.session.id);
      setSession(gen.session);
      setStatus(gen.session.message ?? "Ready.");
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }, [
    tempo,
    keyNote,
    scale,
    bars,
    leadStyle,
    bassStyle,
    bassEngine,
    chordStyle,
    drumStyle,
    leadInstrument,
    bassInstrument,
    chordInstrument,
    drumKit,
    newSessionPreset,
    bassPlayer,
    drumPlayer,
    chordPlayer,
    leadPlayer,
    anchorLane,
  ]);

  const onRegenerate = useCallback(
    async (lane) => {
      if (!session?.id) return;
      setBusy(true);
      setError(null);
      try {
        const res = await regenerateLane(session.id, lane);
        setSession(res.session);
        setStatus(res.session.message ?? `Regenerated ${lane}.`);
      } catch (e) {
        setError(e.message || String(e));
      } finally {
        setBusy(false);
      }
    },
    [session],
  );

  const anyRegenLaneSelected = REGEN_LANE_KEYS.some((k) => selectedRegenLanes[k]);

  const onRegenerateSelected = useCallback(async () => {
    if (!session?.id || !anyRegenLaneSelected) return;
    const lanes = REGEN_LANE_KEYS.filter((k) => selectedRegenLanes[k]);
    setBusy(true);
    setError(null);
    try {
      const updated = await regenerateSelectedLanes(session.id, lanes);
      setSession(updated);
      setSelectedRegenLanes({ drums: false, bass: false, chords: false, lead: false });
      setStatus(
        updated.message ??
          `Regenerated: ${lanes.join(", ")}.`,
      );
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }, [session, anyRegenLaneSelected, selectedRegenLanes]);

  const onSetLaneLock = useCallback(async (laneKey, nextLocked) => {
    if (!session?.id) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await patchLaneLocks(session.id, { [laneKey]: nextLocked });
      setSession(updated);
      setStatus(updated.message ?? "Lane locks updated.");
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }, [session]);

  const onLockAllLanes = useCallback(async () => {
    if (!session?.id) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await patchLaneLocks(session.id, {
        drums: true,
        bass: true,
        chords: true,
        lead: true,
      });
      setSession(updated);
      setStatus(updated.message ?? "Lane locks updated.");
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }, [session]);

  const onUnlockAllLanes = useCallback(async () => {
    if (!session?.id) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await patchLaneLocks(session.id, {
        drums: false,
        bass: false,
        chords: false,
        lead: false,
      });
      setSession(updated);
      setStatus(updated.message ?? "Lane locks updated.");
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }, [session]);

  const onRegenerateUnlockedLanes = useCallback(async () => {
    if (!session?.id) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await regenerateUnlockedLanes(session.id);
      setSession(updated);
      setStatus(updated.message ?? "Unlocked lanes regenerated.");
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }, [session]);

  const onAddPartToSuitLead = useCallback(
    async (mode) => {
      if (!session?.id) return;
      setBusy(true);
      setError(null);
      try {
        const updated = await addPartToSuit(session.id, { target_lane: "lead", mode });
        setSession(updated);
        setStatus(updated.message ?? "Lead updated.");
      } catch (e) {
        setError(e.message || String(e));
      } finally {
        setBusy(false);
      }
    },
    [session],
  );

  const onExport = useCallback(async () => {
    if (!session?.id) return;
    setBusy(true);
    setError(null);
    try {
      const blob = await downloadExportZip(session.id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${session.id}_super_band_lanes.zip`;
      a.click();
      URL.revokeObjectURL(url);
      setStatus("Export downloaded.");
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }, [session]);

  const refresh = useCallback(async () => {
    if (!session?.id) return;
    setBusy(true);
    setError(null);
    try {
      const s = await getSession(session.id);
      setSession(s);
      setStatus("Refreshed session state.");
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }, [session]);

  const onUpdateLeadStyle = useCallback(async () => {
    if (!session?.id) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await patchSession(session.id, { lead_style: activeLeadDraft });
      setSession(updated);
      setStatus(updated.message ?? "Styles updated. Regenerate affected lane(s) to rebuild MIDI.");
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }, [session, activeLeadDraft]);

  const onUpdateLeadPlayer = useCallback(async () => {
    if (!session?.id) return;
    setBusy(true);
    setError(null);
    try {
      const payload = { lead_player: activeLeadPlayerDraft || null };
      const updated = await patchSession(session.id, payload);
      setSession(updated);
      setStatus(updated.message ?? "Lead player updated. Regenerate lead to rebuild MIDI.");
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }, [session, activeLeadPlayerDraft]);

  const onUpdateBassPlayer = useCallback(async () => {
    if (!session?.id) return;
    setBusy(true);
    setError(null);
    try {
      const payload = { bass_player: activeBassPlayerDraft || null };
      const updated = await patchSession(session.id, payload);
      setSession(updated);
      setStatus(updated.message ?? "Bass player updated. Regenerate bass to rebuild MIDI.");
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }, [session, activeBassPlayerDraft]);

  const onUpdateDrumPlayer = useCallback(async () => {
    if (!session?.id) return;
    setBusy(true);
    setError(null);
    try {
      const payload = { drum_player: activeDrumPlayerDraft || null };
      const updated = await patchSession(session.id, payload);
      setSession(updated);
      setStatus(updated.message ?? "Drum player updated. Regenerate drums to rebuild MIDI.");
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }, [session, activeDrumPlayerDraft]);

  const onUpdateBassStyle = useCallback(async () => {
    if (!session?.id) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await patchSession(session.id, { bass_style: activeBassDraft });
      setSession(updated);
      setStatus(updated.message ?? "Styles updated. Regenerate affected lane(s) to rebuild MIDI.");
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }, [session, activeBassDraft]);

  const onUpdateBassEngine = useCallback(async () => {
    if (!session?.id) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await patchSession(session.id, { bass_engine: activeBassEngineDraft });
      setSession(updated);
      setStatus(updated.message ?? "Bass engine updated. Regenerate bass to rebuild MIDI.");
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }, [session, activeBassEngineDraft]);

  const onUpdateChordStyle = useCallback(async () => {
    if (!session?.id) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await patchSession(session.id, { chord_style: activeChordDraft });
      setSession(updated);
      setStatus(updated.message ?? "Styles updated. Regenerate affected lane(s) to rebuild MIDI.");
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }, [session, activeChordDraft]);

  const onUpdateChordPlayer = useCallback(async () => {
    if (!session?.id) return;
    setBusy(true);
    setError(null);
    try {
      const payload = { chord_player: activeChordPlayerDraft || null };
      const updated = await patchSession(session.id, payload);
      setSession(updated);
      setStatus(updated.message ?? "Chord player updated. Regenerate chords to rebuild MIDI.");
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }, [session, activeChordPlayerDraft]);

  const onUpdateDrumStyle = useCallback(async () => {
    if (!session?.id) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await patchSession(session.id, { drum_style: activeDrumDraft });
      setSession(updated);
      setStatus(updated.message ?? "Styles updated. Regenerate affected lane(s) to rebuild MIDI.");
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }, [session, activeDrumDraft]);

  const onUpdateSessionPreset = useCallback(async () => {
    if (!session?.id || !activePresetDraft) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await patchSession(session.id, { session_preset: activePresetDraft });
      setSession(updated);
      setStatus(updated.message ?? "Preset updated. Regenerate lane(s) to rebuild MIDI.");
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }, [session, activePresetDraft]);

  const onUpdateLeadInstrument = useCallback(async () => {
    if (!session?.id) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await patchSession(session.id, { lead_instrument: activeLeadInstrumentDraft });
      setSession(updated);
      setStatus(updated.message ?? "Instruments updated. Regenerate affected lane(s) to rebuild MIDI.");
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }, [session, activeLeadInstrumentDraft]);

  const onUpdateBassInstrument = useCallback(async () => {
    if (!session?.id) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await patchSession(session.id, { bass_instrument: activeBassInstrumentDraft });
      setSession(updated);
      setStatus(updated.message ?? "Instruments updated. Regenerate affected lane(s) to rebuild MIDI.");
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }, [session, activeBassInstrumentDraft]);

  const onUpdateChordInstrument = useCallback(async () => {
    if (!session?.id) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await patchSession(session.id, { chord_instrument: activeChordInstrumentDraft });
      setSession(updated);
      setStatus(updated.message ?? "Instruments updated. Regenerate affected lane(s) to rebuild MIDI.");
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }, [session, activeChordInstrumentDraft]);

  const onUpdateDrumKit = useCallback(async () => {
    if (!session?.id) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await patchSession(session.id, { drum_kit: activeDrumKitDraft });
      setSession(updated);
      setStatus(updated.message ?? "Instruments updated. Regenerate affected lane(s) to rebuild MIDI.");
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }, [session, activeDrumKitDraft]);

  const onSaveSetup = useCallback(async () => {
    const name = saveSetupName.trim();
    if (!name) return;
    const fromSession = Boolean(session?.id);
    const preset = fromSession ? activePresetDraft || null : newSessionPreset || null;
    const drum = fromSession ? activeDrumDraft : drumStyle;
    const bass = fromSession ? activeBassDraft : bassStyle;
    const bassEngineMode = fromSession ? activeBassEngineDraft : bassEngine;
    const chord = fromSession ? activeChordDraft : chordStyle;
    const lead = fromSession ? activeLeadDraft : leadStyle;
    const lp = fromSession ? activeLeadPlayerDraft : leadPlayer;
    const li = fromSession ? activeLeadInstrumentDraft : leadInstrument;
    const bi = fromSession ? activeBassInstrumentDraft : bassInstrument;
    const bp = fromSession ? activeBassPlayerDraft : bassPlayer;
    const dp = fromSession ? activeDrumPlayerDraft : drumPlayer;
    const cp = fromSession ? activeChordPlayerDraft : chordPlayer;
    const ci = fromSession ? activeChordInstrumentDraft : chordInstrument;
    const dk = fromSession ? activeDrumKitDraft : drumKit;
    setBusy(true);
    setError(null);
    try {
      await createSetup({
        name,
        session_preset: preset,
        drum_style: drum,
        bass_style: bass,
        bass_engine: bassEngineMode,
        chord_style: chord,
        lead_style: lead,
        lead_player: lp || null,
        lead_instrument: li,
        bass_instrument: bi,
        bass_player: bp || null,
        drum_player: dp || null,
        chord_player: cp || null,
        chord_instrument: ci,
        drum_kit: dk,
        tempo,
        key: keyNote,
        scale,
      });
      setSaveSetupName("");
      setStatus(`Saved setup "${name}".`);
      await refreshSetups();
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }, [
    saveSetupName,
    session?.id,
    activePresetDraft,
    newSessionPreset,
    activeDrumDraft,
    drumStyle,
    activeBassDraft,
    bassStyle,
    activeBassEngineDraft,
    bassEngine,
    activeChordDraft,
    chordStyle,
    activeLeadDraft,
    leadStyle,
    activeLeadPlayerDraft,
    leadPlayer,
    activeLeadInstrumentDraft,
    leadInstrument,
    activeBassInstrumentDraft,
    bassInstrument,
    activeBassPlayerDraft,
    bassPlayer,
    activeDrumPlayerDraft,
    drumPlayer,
    activeChordPlayerDraft,
    chordPlayer,
    activeChordInstrumentDraft,
    chordInstrument,
    activeDrumKitDraft,
    drumKit,
    tempo,
    keyNote,
    scale,
    refreshSetups,
  ]);

  const onLoadSetup = useCallback((s) => {
    setError(null);
    if (s.tempo != null) setTempo(s.tempo);
    if (s.key) setKeyNote(s.key);
    if (s.scale) setScale(s.scale);
    setDrumStyle(s.drum_style);
    setBassStyle(s.bass_style);
    setBassEngine(s.bass_engine ?? "baseline");
    setChordStyle(s.chord_style);
    setChordPlayer(s.chord_player ?? "");
    setLeadStyle(s.lead_style);
    setLeadPlayer(s.lead_player ?? "");
    setLeadInstrument(s.lead_instrument ?? "flute");
    setBassInstrument(s.bass_instrument ?? "finger_bass");
    setBassPlayer(s.bass_player ?? "");
    setDrumPlayer(s.drum_player ?? "");
    setChordInstrument(s.chord_instrument ?? "piano");
    setDrumKit(s.drum_kit ?? "standard");
    setNewSessionPreset(s.session_preset ?? "");
    setActiveDrumDraft(s.drum_style);
    setActiveBassDraft(s.bass_style);
    setActiveChordDraft(s.chord_style);
    setActiveChordPlayerDraft(s.chord_player ?? "");
    setActiveLeadDraft(s.lead_style);
    setActiveLeadPlayerDraft(s.lead_player ?? "");
    setActiveLeadInstrumentDraft(s.lead_instrument ?? "flute");
    setActiveBassInstrumentDraft(s.bass_instrument ?? "finger_bass");
    setActiveBassPlayerDraft(s.bass_player ?? "");
    setActiveDrumPlayerDraft(s.drum_player ?? "");
    setActiveChordInstrumentDraft(s.chord_instrument ?? "piano");
    setActiveDrumKitDraft(s.drum_kit ?? "standard");
    setActivePresetDraft(s.session_preset ?? "");
    setStatus(`Loaded setup "${s.name}" into controls (not sent to session yet).`);
  }, []);

  const onDeleteSetup = useCallback(
    async (name) => {
      if (!window.confirm(`Delete saved setup "${name}"?`)) return;
      setBusy(true);
      setError(null);
      try {
        await deleteSetup(name);
        setStatus(`Deleted setup "${name}".`);
        await refreshSetups();
      } catch (e) {
        setError(e.message || String(e));
      } finally {
        setBusy(false);
      }
    },
    [refreshSetups],
  );

  const onApplySavedSetupToSession = useCallback(
    async (setup) => {
      if (!session?.id) return;
      setBusy(true);
      setError(null);
      try {
        const { patch } = await getSavedSetupAsSessionPatch(setup.name);
        const updated = await patchSession(session.id, patch);
        setSession(updated);
        setStatus(
          `"${setup.name}" applied to this session. Regenerate lanes to rebuild MIDI.`,
        );
      } catch (e) {
        setError(e.message || String(e));
      } finally {
        setBusy(false);
      }
    },
    [session],
  );

  const syncFormFromSession = useCallback((s) => {
    if (!s) return;
    setTempo(s.tempo);
    setKeyNote(s.key);
    setScale(s.scale);
    setBars(s.bar_count);
    setLeadStyle(s.lead_style);
    setLeadPlayer(s.lead_player ?? "");
    setBassStyle(s.bass_style);
    setChordStyle(s.chord_style);
    setChordPlayer(s.chord_player ?? "");
    setDrumStyle(s.drum_style);
    setLeadInstrument(s.lead_instrument ?? "flute");
    setBassInstrument(s.bass_instrument ?? "finger_bass");
    setBassPlayer(s.bass_player ?? "");
    setDrumPlayer(s.drum_player ?? "");
    setChordInstrument(s.chord_instrument ?? "piano");
    setDrumKit(s.drum_kit ?? "standard");
    setNewSessionPreset(s.session_preset ?? "");
    setAnchorLane(s.anchor_lane ?? "");
  }, []);

  const onAnchorLaneChange = useCallback(
    async (value) => {
      setAnchorLane(value);
      if (!session?.id) return;
      setBusy(true);
      setError(null);
      try {
        const updated = await patchSession(session.id, {
          anchor_lane: value === "" ? null : value,
        });
        setSession(updated);
        setStatus(updated.message ?? "Anchor lane updated.");
      } catch (e) {
        setError(e.message || String(e));
      } finally {
        setBusy(false);
      }
    },
    [session],
  );

  const onGenerateAroundLane = useCallback(
    async (laneKey) => {
      if (!session?.id) return;
      setBusy(true);
      setError(null);
      try {
        const updated = await generateAroundAnchor(session.id, { anchor_lane: laneKey });
        setSession(updated);
        setAnchorLane(laneKey);
        setStatus(updated.message ?? "Generated around anchor.");
      } catch (e) {
        setError(e.message || String(e));
      } finally {
        setBusy(false);
      }
    },
    [session],
  );

  const loadClipEvaluation = useCallback(async () => {
    if (!evalClipId.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const res = await getClipEvaluation(evalClipId.trim());
      setEvalReferenceNotes(res.record?.reference_notes ?? "");
      setEvalTakes(Array.isArray(res.record?.takes) ? res.record.takes : []);
      setStatus("Loaded clip evaluation.");
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }, [evalClipId]);

  const loadEvaluationSummary = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const summary = await getEvaluationSummary();
      setEvalSummary(summary);
      setStatus("Loaded evaluation summary.");
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }, []);

  const onSaveReferenceNotes = useCallback(async () => {
    if (!evalClipId.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const res = await setClipReferenceNotes({
        clip_id: evalClipId.trim(),
        reference_notes: evalReferenceNotes,
      });
      setEvalReferenceNotes(res.record?.reference_notes ?? "");
      setEvalTakes(Array.isArray(res.record?.takes) ? res.record.takes : []);
      const summary = await getEvaluationSummary();
      setEvalSummary(summary);
      setStatus("Saved reference notes.");
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }, [evalClipId, evalReferenceNotes]);

  const onSaveTakeEvaluation = useCallback(async () => {
    if (!session?.id || !evalClipId.trim() || !evalTakeId.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const res = await createTakeEvaluation({
        clip_id: evalClipId.trim(),
        take_id: evalTakeId.trim(),
        session_id: session.id,
        bass_engine: session.bass_engine ?? "baseline",
        bass_style: session.bass_style ?? "supportive",
        bass_player: session.bass_player ?? null,
        bass_instrument: session.bass_instrument ?? "finger_bass",
        notes: evalTakeNotes,
        scores: evalScores,
      });
      setEvalTakes(Array.isArray(res.record?.takes) ? res.record.takes : []);
      const summary = await getEvaluationSummary();
      setEvalSummary(summary);
      setStatus("Saved take evaluation.");
      setEvalTakeId(`${evalClipId.trim()}-take-${Date.now()}`);
      setEvalTakeNotes("");
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }, [session, evalClipId, evalTakeId, evalTakeNotes, evalScores]);

  const onDuplicateSession = useCallback(async () => {
    if (!session?.id) return;
    setBusy(true);
    setError(null);
    try {
      const dup = await duplicateSession(session.id);
      setSession(dup);
      syncFormFromSession(dup);
      setStatus(dup.message ?? "Session duplicated.");
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }, [session, syncFormFromSession]);

  const onMarkSessionAsA = useCallback(() => {
    if (!session) return;
    setSessionSnapshotA(structuredClone(session));
    setAbMessage("Snapshot A saved from this session.");
  }, [session]);

  const allGenerated =
    session?.lanes &&
    session.lanes.drums?.generated &&
    session.lanes.bass?.generated &&
    session.lanes.chords?.generated &&
    session.lanes.lead?.generated;

  return (
    <div style={{ maxWidth: 960, margin: "0 auto" }}>
      <header style={{ marginBottom: "1.5rem" }}>
        <h1 style={{ margin: 0, fontSize: "1.75rem" }}>Super Band Session Player</h1>
        <p style={{ margin: "0.35rem 0 0", color: "#64748b" }}>
          Drums, bass, chords, and lead as separate MIDI lanes.
        </p>
      </header>

      <UploadFirstEntryPanel
        session={session}
        setSession={setSession}
        busy={busy}
        setBusy={setBusy}
        setError={setError}
        setStatus={setStatus}
        setTempo={setTempo}
        setKeyNote={setKeyNote}
        setScale={setScale}
        setBars={setBars}
      />

      <SessionControls
        tempo={tempo}
        setTempo={setTempo}
        keyNote={keyNote}
        setKeyNote={setKeyNote}
        scale={scale}
        setScale={setScale}
        bars={bars}
        setBars={setBars}
        leadStyle={leadStyle}
        setLeadStyle={setLeadStyle}
        leadPlayer={leadPlayer}
        setLeadPlayer={setLeadPlayer}
        bassStyle={bassStyle}
        setBassStyle={setBassStyle}
        bassEngine={bassEngine}
        setBassEngine={setBassEngine}
        chordStyle={chordStyle}
        setChordStyle={setChordStyle}
        chordPlayer={chordPlayer}
        setChordPlayer={setChordPlayer}
        drumStyle={drumStyle}
        setDrumStyle={setDrumStyle}
        leadInstrument={leadInstrument}
        setLeadInstrument={setLeadInstrument}
        bassInstrument={bassInstrument}
        setBassInstrument={setBassInstrument}
        bassPlayer={bassPlayer}
        setBassPlayer={setBassPlayer}
        drumPlayer={drumPlayer}
        setDrumPlayer={setDrumPlayer}
        chordInstrument={chordInstrument}
        setChordInstrument={setChordInstrument}
        drumKit={drumKit}
        setDrumKit={setDrumKit}
        sessionPreset={newSessionPreset}
        setSessionPreset={setNewSessionPreset}
        anchorLane={anchorLane}
        onAnchorLaneChange={onAnchorLaneChange}
        busy={busy}
        onGenerate={onGenerate}
      />

      <SavedSetupsPanel
        setups={setups}
        saveName={saveSetupName}
        setSaveName={setSaveSetupName}
        busy={busy}
        onRefresh={refreshSetups}
        onSave={onSaveSetup}
        onLoad={onLoadSetup}
        onApplyToSession={onApplySavedSetupToSession}
        activeSessionId={session?.id ?? null}
        onDelete={onDeleteSetup}
      />

      {error && (
        <pre
          style={{
            background: "#fef2f2",
            color: "#991b1b",
            padding: "0.75rem 1rem",
            borderRadius: 8,
            overflow: "auto",
          }}
        >
          {error}
        </pre>
      )}
      {status && !error && (
        <p style={{ color: "#15803d", fontSize: 14 }}>
          {status}{" "}
          {session?.id && (
            <button type="button" onClick={refresh} disabled={busy} style={{ marginLeft: 8 }}>
              Refresh state
            </button>
          )}
        </p>
      )}

      {session && (
        <>
          <div
            style={{
              marginBottom: "0.75rem",
              fontSize: 14,
              color: "#475569",
              display: "flex",
              flexWrap: "wrap",
              alignItems: "center",
              gap: "0.5rem",
            }}
          >
            <strong>Session ID:</strong> <code>{session.id}</code>
            {" "}
            · <strong>Current preset:</strong>{" "}
            <code style={{ background: "#f1f5f9", padding: "2px 6px", borderRadius: 4 }}>
              {session.session_preset ?? "—"}
            </code>
            {session.drum_style != null && (
              <>
                {" "}
                · <strong>Drums:</strong>{" "}
                <code style={{ background: "#f1f5f9", padding: "2px 6px", borderRadius: 4 }}>
                  {session.drum_style}
                </code>
                {session.drum_player ? (
                  <>
                    {" "}
                    · <strong>Drum player:</strong>{" "}
                    <code style={{ background: "#f1f5f9", padding: "2px 6px", borderRadius: 4 }}>
                      {session.drum_player}
                    </code>
                  </>
                ) : null}
              </>
            )}
            {session.lead_style != null && (
              <>
                {" "}
                · <strong>Lead:</strong>{" "}
                <code style={{ background: "#f1f5f9", padding: "2px 6px", borderRadius: 4 }}>
                  {session.lead_style}
                </code>
                {session.lead_player ? (
                  <>
                    {" "}
                    · <strong>Lead player:</strong>{" "}
                    <code style={{ background: "#f1f5f9", padding: "2px 6px", borderRadius: 4 }}>
                      {session.lead_player}
                    </code>
                  </>
                ) : null}
              </>
            )}
            {session.bass_style != null && (
              <>
                {" "}
                · <strong>Bass:</strong>{" "}
                <code style={{ background: "#f1f5f9", padding: "2px 6px", borderRadius: 4 }}>
                  {session.bass_style}
                </code>
                {" "}
                · <strong>Bass engine:</strong>{" "}
                <code style={{ background: "#f1f5f9", padding: "2px 6px", borderRadius: 4 }}>
                  {session.bass_engine ?? "baseline"}
                </code>
                {session.bass_player ? (
                  <>
                    {" "}
                    · <strong>Bass player:</strong>{" "}
                    <code style={{ background: "#f1f5f9", padding: "2px 6px", borderRadius: 4 }}>
                      {session.bass_player}
                    </code>
                  </>
                ) : null}
              </>
            )}
            {session.chord_style != null && (
              <>
                {" "}
                · <strong>Chords:</strong>{" "}
                <code style={{ background: "#f1f5f9", padding: "2px 6px", borderRadius: 4 }}>
                  {session.chord_style}
                </code>
                {session.chord_player ? (
                  <>
                    {" "}
                    · <strong>Chord player:</strong>{" "}
                    <code style={{ background: "#f1f5f9", padding: "2px 6px", borderRadius: 4 }}>
                      {session.chord_player}
                    </code>
                  </>
                ) : null}
              </>
            )}
            <span style={{ flex: 1, minWidth: 8 }} />
            <button
              type="button"
              onClick={onDuplicateSession}
              disabled={busy}
              style={{ padding: "0.35rem 0.75rem", fontSize: 13 }}
            >
              Duplicate Session
            </button>
            <button
              type="button"
              onClick={onMarkSessionAsA}
              disabled={busy}
              style={{ padding: "0.35rem 0.75rem", fontSize: 13 }}
            >
              Mark as A
            </button>
            <button
              type="button"
              onClick={() => setCompareModeOpen(true)}
              disabled={busy || !sessionSnapshotA}
              style={{ padding: "0.35rem 0.75rem", fontSize: 13 }}
            >
              Compare with A
            </button>
          </div>
          {session.engine_data && (
            <details
              style={{
                marginBottom: "1rem",
                padding: "0.65rem 0.85rem",
                background: "#fff",
                border: "1px solid #e2e8f0",
                borderRadius: 10,
              }}
            >
              <summary style={{ cursor: "pointer", fontWeight: 600 }}>Engine analysis (Phase 2)</summary>
              <div style={{ marginTop: 10, fontSize: 13, color: "#334155", lineHeight: 1.5 }}>
                <div>
                  <strong>Source lane:</strong> {session.engine_data.source_analysis?.source_lane ?? "none"}
                </div>
                <div>
                  <strong>Downbeat guess (bar):</strong> {session.engine_data.source_analysis?.downbeat_guess_bar_index ?? 0}
                </div>
                <div>
                  <strong>Beat-phase anchor:</strong>{" "}
                  {(session.engine_data.source_analysis?.beat_phase_offset_beats ?? 0)}
                  {" /4"}
                  {" · conf "}
                  {session.engine_data.source_analysis?.beat_phase_confidence ?? 0}
                </div>
                <div>
                  <strong>Generation timing:</strong>{" "}
                  phase {(session.engine_data.source_analysis?.phase_offset_used_for_generation_beats ?? 0)}
                  {" /4 · bar-start "}
                  {session.engine_data.source_analysis?.bar_start_anchor_used_seconds ?? 0}s
                  {" · aligned "}
                  {session.engine_data.source_analysis?.generation_aligned_to_anchor ? "yes" : "no"}
                </div>
                <div>
                  <strong>Sections:</strong>{" "}
                  {(session.engine_data.source_analysis?.sections ?? [])
                    .map((s) => `${s.label}[${s.start_bar}-${s.end_bar}]`)
                    .join(" · ") || "none"}
                </div>
                <div>
                  <strong>Groove:</strong> {session.engine_data.groove_profile?.pocket_feel ?? "unknown"} · sync{" "}
                  {session.engine_data.groove_profile?.syncopation_score ?? 0}
                </div>
                <div>
                  <strong>Harmony plan:</strong>{" "}
                  {session.engine_data.harmony_plan?.key_center ?? "C"}{" "}
                  {session.engine_data.harmony_plan?.scale ?? "major"}
                  {" · "}
                  {session.engine_data.harmony_plan?.source ?? "static_session_key_scale"}
                  {(session.engine_data.harmony_plan?.bars ?? []).length > 0
                    ? ` · ${(session.engine_data.harmony_plan?.bars ?? [])
                        .slice(0, 4)
                        .map((b) => `b${b.bar_index}:r${b.root_pc}[${(b.target_pcs ?? []).join(",")}]`)
                        .join(" | ")}`
                    : ""}
                </div>
              </div>
            </details>
          )}
          <ReferenceAudioPanel
            session={session}
            busy={busy}
            setBusy={setBusy}
            setError={setError}
            setStatus={setStatus}
            setSession={setSession}
          />
          <BassCandidatePanel
            session={session}
            setSession={setSession}
            busy={busy}
            setBusy={setBusy}
            setError={setError}
            setStatus={setStatus}
          />
          <details
            style={{
              marginBottom: "1rem",
              padding: "0.65rem 0.85rem",
              background: "#fff",
              border: "1px solid #e2e8f0",
              borderRadius: 10,
            }}
          >
            <summary style={{ cursor: "pointer", fontWeight: 600 }}>Bass Take Evaluation</summary>
            <div style={{ marginTop: 10, display: "grid", gap: "0.6rem", maxWidth: 840 }}>
              <label style={{ display: "grid", gap: 4, fontSize: 13 }}>
                Clip ID
                <input value={evalClipId} onChange={(e) => setEvalClipId(e.target.value)} />
              </label>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <button type="button" onClick={loadClipEvaluation} disabled={busy || !evalClipId.trim()}>
                  Load Clip Eval
                </button>
                <button type="button" onClick={loadEvaluationSummary} disabled={busy}>
                  Load Eval Summary
                </button>
              </div>
              <label style={{ display: "grid", gap: 4, fontSize: 13 }}>
                Reference Notes (what a good bassline should do)
                <textarea
                  rows={4}
                  value={evalReferenceNotes}
                  onChange={(e) => setEvalReferenceNotes(e.target.value)}
                />
              </label>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <button type="button" onClick={onSaveReferenceNotes} disabled={busy || !evalClipId.trim()}>
                  Save Reference Notes
                </button>
              </div>
              <label style={{ display: "grid", gap: 4, fontSize: 13 }}>
                Take ID
                <input value={evalTakeId} onChange={(e) => setEvalTakeId(e.target.value)} />
              </label>
              <label style={{ display: "grid", gap: 4, fontSize: 13 }}>
                Take Notes
                <textarea rows={3} value={evalTakeNotes} onChange={(e) => setEvalTakeNotes(e.target.value)} />
              </label>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 8 }}>
                {[
                  ["groove_fit", "Groove Fit"],
                  ["harmonic_fit", "Harmonic Fit"],
                  ["phrase_feel", "Phrase Feel"],
                  ["articulation_feel", "Articulation Feel"],
                  ["usefulness", "Usefulness"],
                ].map(([k, label]) => (
                  <label key={k} style={{ display: "grid", gap: 4, fontSize: 13 }}>
                    {label}
                    <input
                      type="number"
                      min={1}
                      max={5}
                      value={evalScores[k]}
                      onChange={(e) =>
                        setEvalScores((prev) => ({
                          ...prev,
                          [k]: Math.max(1, Math.min(5, Number(e.target.value) || 1)),
                        }))
                      }
                    />
                  </label>
                ))}
              </div>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <button
                  type="button"
                  onClick={onSaveTakeEvaluation}
                  disabled={busy || !session?.id || !evalClipId.trim() || !evalTakeId.trim()}
                >
                  Save Take Evaluation
                </button>
              </div>
              <div style={{ fontSize: 13, color: "#334155" }}>
                <strong>Recent takes:</strong>{" "}
                {evalTakes.length === 0
                  ? "none"
                  : evalTakes
                      .slice(0, 6)
                      .map((t) => `${t.take_id} [G${t.scores?.groove_fit}/H${t.scores?.harmonic_fit}/P${t.scores?.phrase_feel}]`)
                      .join(" · ")}
              </div>
              <div style={{ fontSize: 13, color: "#334155", borderTop: "1px solid #e2e8f0", paddingTop: 8 }}>
                <strong>Summary:</strong>{" "}
                {!evalSummary
                  ? "not loaded"
                  : `all takes ${evalSummary.total_take_count} · G ${evalSummary.overall_averages?.groove_fit ?? 0} · H ${evalSummary.overall_averages?.harmonic_fit ?? 0} · P ${evalSummary.overall_averages?.phrase_feel ?? 0} · A ${evalSummary.overall_averages?.articulation_feel ?? 0} · U ${evalSummary.overall_averages?.usefulness ?? 0}`}
                {evalSummary && Array.isArray(evalSummary.by_engine) && evalSummary.by_engine.length > 0 ? (
                  <div style={{ marginTop: 4 }}>
                    {evalSummary.by_engine
                      .map(
                        (row) =>
                          `${row.engine} (${row.take_count}) → G ${row.averages?.groove_fit ?? 0}, H ${row.averages?.harmonic_fit ?? 0}, P ${row.averages?.phrase_feel ?? 0}, A ${row.averages?.articulation_feel ?? 0}, U ${row.averages?.usefulness ?? 0}`,
                      )
                      .join(" · ")}
                  </div>
                ) : null}
              </div>
            </div>
          </details>
          {abMessage && (
            <p style={{ color: "#15803d", fontSize: 13, margin: "0 0 0.65rem" }}>{abMessage}</p>
          )}
          {compareModeOpen && sessionSnapshotA && (
            <SessionComparePanel
              sessionA={sessionSnapshotA}
              sessionCurrent={session}
              onClose={() => setCompareModeOpen(false)}
            />
          )}
          <div style={{ marginBottom: "0.75rem", fontSize: 14, color: "#475569" }}>
            <strong>Instruments:</strong>{" "}
            <code style={{ background: "#f1f5f9", padding: "2px 6px", borderRadius: 4 }}>
              kit {session.drum_kit ?? "standard"}
            </code>
            {" · "}
            <code style={{ background: "#f1f5f9", padding: "2px 6px", borderRadius: 4 }}>
              bass {session.bass_instrument ?? "finger_bass"}
            </code>
            {" · "}
            <code style={{ background: "#f1f5f9", padding: "2px 6px", borderRadius: 4 }}>
              chords {session.chord_instrument ?? "piano"}
            </code>
            {" · "}
            <code style={{ background: "#f1f5f9", padding: "2px 6px", borderRadius: 4 }}>
              lead {session.lead_instrument ?? "flute"}
            </code>
          </div>
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "0.5rem",
              alignItems: "center",
              marginBottom: "1rem",
              padding: "0.65rem 0.85rem",
              background: "#fff",
              border: "1px solid #e2e8f0",
              borderRadius: 10,
              maxWidth: 720,
            }}
          >
            <span style={{ fontSize: 14, color: "#475569", marginRight: 4 }}>Session preset (this session)</span>
            <select
              value={activePresetDraft}
              onChange={(e) => setActivePresetDraft(e.target.value)}
              disabled={busy}
              style={{ fontSize: 14 }}
            >
              <option value="">Select preset…</option>
              {ACTIVE_SESSION_PRESET_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={onUpdateSessionPreset}
              disabled={busy || !activePresetDraft || activePresetDraft === session.session_preset}
              style={{ padding: "0.35rem 0.75rem" }}
            >
              Update Preset
            </button>
          </div>
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "0.5rem",
              alignItems: "center",
              marginBottom: "1rem",
              padding: "0.65rem 0.85rem",
              background: "#fff",
              border: "1px solid #e2e8f0",
              borderRadius: 10,
              maxWidth: 720,
            }}
          >
            <span style={{ fontSize: 14, color: "#475569", marginRight: 4 }}>Drum kit (this session)</span>
            <select
              value={activeDrumKitDraft}
              onChange={(e) => setActiveDrumKitDraft(e.target.value)}
              disabled={busy}
              style={{ fontSize: 14 }}
            >
              {ACTIVE_DRUM_KITS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={onUpdateDrumKit}
              disabled={busy || activeDrumKitDraft === (session.drum_kit ?? "standard")}
              style={{ padding: "0.35rem 0.75rem" }}
            >
              Update Drum Kit
            </button>
          </div>
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "0.5rem",
              alignItems: "center",
              marginBottom: "1rem",
              padding: "0.65rem 0.85rem",
              background: "#fff",
              border: "1px solid #e2e8f0",
              borderRadius: 10,
              maxWidth: 720,
            }}
          >
            <span style={{ fontSize: 14, color: "#475569", marginRight: 4 }}>Drum style (this session)</span>
            <select
              value={activeDrumDraft}
              onChange={(e) => setActiveDrumDraft(e.target.value)}
              disabled={busy}
              style={{ fontSize: 14 }}
            >
              {ACTIVE_DRUM_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={onUpdateDrumStyle}
              disabled={busy || activeDrumDraft === session.drum_style}
              style={{ padding: "0.35rem 0.75rem" }}
            >
              Update Drum Style
            </button>
          </div>
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "0.5rem",
              alignItems: "center",
              marginBottom: "1rem",
              padding: "0.65rem 0.85rem",
              background: "#fff",
              border: "1px solid #e2e8f0",
              borderRadius: 10,
              maxWidth: 720,
            }}
          >
            <span style={{ fontSize: 14, color: "#475569", marginRight: 4 }}>Drum player (this session)</span>
            <select
              value={activeDrumPlayerDraft}
              onChange={(e) => setActiveDrumPlayerDraft(e.target.value)}
              disabled={busy}
              style={{ fontSize: 14 }}
            >
              <option value="">None</option>
              <option value="stubblefield">Stubblefield-style pocket</option>
              <option value="questlove">Questlove-style laid-back</option>
              <option value="dilla">Dilla-style swung loop</option>
            </select>
            <button
              type="button"
              onClick={onUpdateDrumPlayer}
              disabled={
                busy ||
                (activeDrumPlayerDraft || "") === (session.drum_player ?? "")
              }
              style={{ padding: "0.35rem 0.75rem" }}
            >
              Update Drum Player
            </button>
          </div>
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "0.5rem",
              alignItems: "center",
              marginBottom: "1rem",
              padding: "0.65rem 0.85rem",
              background: "#fff",
              border: "1px solid #e2e8f0",
              borderRadius: 10,
              maxWidth: 720,
            }}
          >
            <span style={{ fontSize: 14, color: "#475569", marginRight: 4 }}>Lead style (this session)</span>
            <select
              value={activeLeadDraft}
              onChange={(e) => setActiveLeadDraft(e.target.value)}
              disabled={busy}
              style={{ fontSize: 14 }}
            >
              {ACTIVE_LEAD_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={onUpdateLeadStyle}
              disabled={busy || activeLeadDraft === session.lead_style}
              style={{ padding: "0.35rem 0.75rem" }}
            >
              Update Lead Style
            </button>
          </div>
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "0.5rem",
              alignItems: "center",
              marginBottom: "1rem",
              padding: "0.65rem 0.85rem",
              background: "#fff",
              border: "1px solid #e2e8f0",
              borderRadius: 10,
              maxWidth: 720,
            }}
          >
            <span style={{ fontSize: 14, color: "#475569", marginRight: 4 }}>Lead player (this session)</span>
            <select
              value={activeLeadPlayerDraft}
              onChange={(e) => setActiveLeadPlayerDraft(e.target.value)}
              disabled={busy}
              style={{ fontSize: 14 }}
            >
              <option value="">None</option>
              <option value="coltrane">Coltrane-style intensity arc</option>
              <option value="cal_tjader">Cal Tjader-style lyrical sync</option>
              <option value="soul_sparse">Soul sparse restraint</option>
              <option value="funk_phrasing">Funk hook phrasing</option>
            </select>
            <button
              type="button"
              onClick={onUpdateLeadPlayer}
              disabled={
                busy ||
                (activeLeadPlayerDraft || "") === (session.lead_player ?? "")
              }
              style={{ padding: "0.35rem 0.75rem" }}
            >
              Update Lead Player
            </button>
          </div>
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "0.5rem",
              alignItems: "center",
              marginBottom: "1rem",
              padding: "0.65rem 0.85rem",
              background: "#fff",
              border: "1px solid #e2e8f0",
              borderRadius: 10,
              maxWidth: 720,
            }}
          >
            <span style={{ fontSize: 14, color: "#475569", marginRight: 4 }}>Lead instrument (this session)</span>
            <select
              value={activeLeadInstrumentDraft}
              onChange={(e) => setActiveLeadInstrumentDraft(e.target.value)}
              disabled={busy}
              style={{ fontSize: 14 }}
            >
              {ACTIVE_LEAD_INSTRUMENTS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={onUpdateLeadInstrument}
              disabled={busy || activeLeadInstrumentDraft === (session.lead_instrument ?? "flute")}
              style={{ padding: "0.35rem 0.75rem" }}
            >
              Update Lead Instrument
            </button>
          </div>
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "0.5rem",
              alignItems: "center",
              marginBottom: "1rem",
              padding: "0.65rem 0.85rem",
              background: "#fff",
              border: "1px solid #e2e8f0",
              borderRadius: 10,
              maxWidth: 720,
            }}
          >
            <span style={{ fontSize: 14, color: "#475569", marginRight: 4 }}>Bass style (this session)</span>
            <select
              value={activeBassDraft}
              onChange={(e) => setActiveBassDraft(e.target.value)}
              disabled={busy}
              style={{ fontSize: 14 }}
            >
              {ACTIVE_BASS_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={onUpdateBassStyle}
              disabled={busy || activeBassDraft === session.bass_style}
              style={{ padding: "0.35rem 0.75rem" }}
            >
              Update Bass Style
            </button>
          </div>
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "0.5rem",
              alignItems: "center",
              marginBottom: "1rem",
              padding: "0.65rem 0.85rem",
              background: "#fff",
              border: "1px solid #e2e8f0",
              borderRadius: 10,
              maxWidth: 720,
            }}
          >
            <span style={{ fontSize: 14, color: "#475569", marginRight: 4 }}>Bass engine (this session)</span>
            <select
              value={activeBassEngineDraft}
              onChange={(e) => setActiveBassEngineDraft(e.target.value)}
              disabled={busy}
              style={{ fontSize: 14 }}
            >
              <option value="baseline">Baseline</option>
              <option value="phrase_v2">Phrase Engine v2</option>
            </select>
            <button
              type="button"
              onClick={onUpdateBassEngine}
              disabled={busy || activeBassEngineDraft === (session.bass_engine ?? "baseline")}
              style={{ padding: "0.35rem 0.75rem" }}
            >
              Update Bass Engine
            </button>
          </div>
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "0.5rem",
              alignItems: "center",
              marginBottom: "1rem",
              padding: "0.65rem 0.85rem",
              background: "#fff",
              border: "1px solid #e2e8f0",
              borderRadius: 10,
              maxWidth: 720,
            }}
          >
            <span style={{ fontSize: 14, color: "#475569", marginRight: 4 }}>Bass player (this session)</span>
            <select
              value={activeBassPlayerDraft}
              onChange={(e) => setActiveBassPlayerDraft(e.target.value)}
              disabled={busy}
              style={{ fontSize: 14 }}
            >
              <option value="">None</option>
              <option value="bootsy">Bootsy-style pocket</option>
              <option value="marcus">Marcus-style fusion line</option>
              <option value="pino">Pino-style soul contour</option>
            </select>
            <button
              type="button"
              onClick={onUpdateBassPlayer}
              disabled={
                busy ||
                (activeBassPlayerDraft || "") === (session.bass_player ?? "")
              }
              style={{ padding: "0.35rem 0.75rem" }}
            >
              Update Bass Player
            </button>
          </div>
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "0.5rem",
              alignItems: "center",
              marginBottom: "1rem",
              padding: "0.65rem 0.85rem",
              background: "#fff",
              border: "1px solid #e2e8f0",
              borderRadius: 10,
              maxWidth: 720,
            }}
          >
            <span style={{ fontSize: 14, color: "#475569", marginRight: 4 }}>Bass instrument (this session)</span>
            <select
              value={activeBassInstrumentDraft}
              onChange={(e) => setActiveBassInstrumentDraft(e.target.value)}
              disabled={busy}
              style={{ fontSize: 14 }}
            >
              {ACTIVE_BASS_INSTRUMENTS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={onUpdateBassInstrument}
              disabled={busy || activeBassInstrumentDraft === (session.bass_instrument ?? "finger_bass")}
              style={{ padding: "0.35rem 0.75rem" }}
            >
              Update Bass Instrument
            </button>
          </div>
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "0.5rem",
              alignItems: "center",
              marginBottom: "1rem",
              padding: "0.65rem 0.85rem",
              background: "#fff",
              border: "1px solid #e2e8f0",
              borderRadius: 10,
              maxWidth: 720,
            }}
          >
            <span style={{ fontSize: 14, color: "#475569", marginRight: 4 }}>Chord style (this session)</span>
            <select
              value={activeChordDraft}
              onChange={(e) => setActiveChordDraft(e.target.value)}
              disabled={busy}
              style={{ fontSize: 14 }}
            >
              {ACTIVE_CHORD_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={onUpdateChordStyle}
              disabled={busy || activeChordDraft === session.chord_style}
              style={{ padding: "0.35rem 0.75rem" }}
            >
              Update Chord Style
            </button>
          </div>
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "0.5rem",
              alignItems: "center",
              marginBottom: "1rem",
              padding: "0.65rem 0.85rem",
              background: "#fff",
              border: "1px solid #e2e8f0",
              borderRadius: 10,
              maxWidth: 720,
            }}
          >
            <span style={{ fontSize: 14, color: "#475569", marginRight: 4 }}>Chord player (this session)</span>
            <select
              value={activeChordPlayerDraft}
              onChange={(e) => setActiveChordPlayerDraft(e.target.value)}
              disabled={busy}
              style={{ fontSize: 14 }}
            >
              <option value="">None</option>
              <option value="herbie">Herbie-style color comp</option>
              <option value="barry_miles">Barry Miles-style modal bed</option>
              <option value="soul_keys">Soul keys warmth</option>
              <option value="funk_stabs">Funk stab punctuation</option>
            </select>
            <button
              type="button"
              onClick={onUpdateChordPlayer}
              disabled={
                busy ||
                (activeChordPlayerDraft || "") === (session.chord_player ?? "")
              }
              style={{ padding: "0.35rem 0.75rem" }}
            >
              Update Chord Player
            </button>
          </div>
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "0.5rem",
              alignItems: "center",
              marginBottom: "1rem",
              padding: "0.65rem 0.85rem",
              background: "#fff",
              border: "1px solid #e2e8f0",
              borderRadius: 10,
              maxWidth: 720,
            }}
          >
            <span style={{ fontSize: 14, color: "#475569", marginRight: 4 }}>Chord instrument (this session)</span>
            <select
              value={activeChordInstrumentDraft}
              onChange={(e) => setActiveChordInstrumentDraft(e.target.value)}
              disabled={busy}
              style={{ fontSize: 14 }}
            >
              {ACTIVE_CHORD_INSTRUMENTS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={onUpdateChordInstrument}
              disabled={busy || activeChordInstrumentDraft === (session.chord_instrument ?? "piano")}
              style={{ padding: "0.35rem 0.75rem" }}
            >
              Update Chord Instrument
            </button>
          </div>
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "0.75rem",
              alignItems: "center",
              marginBottom: "1rem",
              padding: "0.65rem 0.85rem",
              background: "#fff",
              border: "1px solid #e2e8f0",
              borderRadius: 10,
              maxWidth: 720,
            }}
          >
            <span style={{ fontSize: 14, color: "#475569", marginRight: 4 }}>Multi-lane regenerate</span>
            {REGEN_LANE_KEYS.map((key) => (
              <label
                key={key}
                style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 14, cursor: "pointer" }}
              >
                <input
                  type="checkbox"
                  checked={selectedRegenLanes[key]}
                  onChange={() =>
                    setSelectedRegenLanes((prev) => ({ ...prev, [key]: !prev[key] }))
                  }
                  disabled={busy}
                />
                {key}
              </label>
            ))}
            <button type="button" onClick={onLockAllLanes} disabled={busy} style={{ padding: "0.35rem 0.65rem", fontSize: 13 }}>
              Lock all
            </button>
            <button type="button" onClick={onUnlockAllLanes} disabled={busy} style={{ padding: "0.35rem 0.65rem", fontSize: 13 }}>
              Unlock all
            </button>
            <button
              type="button"
              onClick={onRegenerateUnlockedLanes}
              disabled={busy}
              style={{ padding: "0.35rem 0.65rem", fontSize: 13 }}
            >
              Regenerate Unlocked Lanes
            </button>
            <button
              type="button"
              onClick={onRegenerateSelected}
              disabled={busy || !anyRegenLaneSelected}
              style={{ padding: "0.35rem 0.75rem", marginLeft: "auto" }}
            >
              Regenerate Selected Lanes
            </button>
          </div>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
              gap: "0.75rem",
              marginBottom: "1rem",
            }}
          >
            <LaneCard
              title="Drums"
              laneKey="drums"
              preview={session.lanes?.drums?.preview}
              generated={session.lanes?.drums?.generated}
              locked={session.lanes?.drums?.locked}
              notes={session.lanes?.drums?.notes}
              barCount={session.bar_count}
              tempoBpm={session.tempo}
              busy={busy}
              onRegenerate={onRegenerate}
              onGenerateAround={onGenerateAroundLane}
              onSetLocked={onSetLaneLock}
            />
            <LaneCard
              title="Bass"
              laneKey="bass"
              preview={session.lanes?.bass?.preview}
              generated={session.lanes?.bass?.generated}
              locked={session.lanes?.bass?.locked}
              notes={session.lanes?.bass?.notes}
              barCount={session.bar_count}
              tempoBpm={session.tempo}
              busy={busy}
              onRegenerate={onRegenerate}
              onGenerateAround={onGenerateAroundLane}
              onSetLocked={onSetLaneLock}
            />
            <LaneCard
              title="Chords"
              laneKey="chords"
              preview={session.lanes?.chords?.preview}
              generated={session.lanes?.chords?.generated}
              locked={session.lanes?.chords?.locked}
              notes={session.lanes?.chords?.notes}
              barCount={session.bar_count}
              tempoBpm={session.tempo}
              busy={busy}
              onRegenerate={onRegenerate}
              onGenerateAround={onGenerateAroundLane}
              onSetLocked={onSetLaneLock}
            />
            <LaneCard
              title="Lead"
              laneKey="lead"
              preview={session.lanes?.lead?.preview}
              generated={session.lanes?.lead?.generated}
              locked={session.lanes?.lead?.locked}
              notes={session.lanes?.lead?.notes}
              barCount={session.bar_count}
              tempoBpm={session.tempo}
              busy={busy}
              onRegenerate={onRegenerate}
              onGenerateAround={onGenerateAroundLane}
              onSetLocked={onSetLaneLock}
              suitPart={{
                mode: leadSuitMode,
                onModeChange: setLeadSuitMode,
                onSubmit: onAddPartToSuitLead,
              }}
            />
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button type="button" onClick={onExport} disabled={busy || !allGenerated}>
              Export MIDI (zip)
            </button>
          </div>
        </>
      )}
    </div>
  );
}
