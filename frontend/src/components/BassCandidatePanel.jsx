import { useCallback, useEffect, useRef, useState } from "react";
import {
  downloadBassCandidateMidi,
  generateBassCandidates,
  getBassCandidateTakeNotes,
  listBassCandidates,
  promoteBassCandidate,
  referenceAudioUrl,
  regenerateBassBars,
} from "../api/client.js";
import PianoRollPreview from "./PianoRollPreview.jsx";

const MAX_VISIBLE_RUNS = 10;
const MAX_PREFETCH_TAKES = 24;
const STAGE_LABELS = {
  strict: "Top Pick",
  relaxed: "Alt Groove",
  final_fill: "Fallback Take",
};

export default function BassCandidatePanel({ session, setSession, busy, setBusy, setError, setStatus }) {
  const [candidateTakeCount, setCandidateTakeCount] = useState(4);
  const [candidateSeed, setCandidateSeed] = useState("");
  const [candidateClipId, setCandidateClipId] = useState("");
  const [adjustBarStart, setAdjustBarStart] = useState("2");
  const [adjustBarEnd, setAdjustBarEnd] = useState("4");
  const [adjustSeed, setAdjustSeed] = useState("");
  const [bassCandidateRuns, setBassCandidateRuns] = useState([]);
  const [openTakeRolls, setOpenTakeRolls] = useState({});
  const [takeNotesByKey, setTakeNotesByKey] = useState({});
  const [loadingTakeNotes, setLoadingTakeNotes] = useState({});
  const [playingTakeKey, setPlayingTakeKey] = useState("");
  const [takeAKey, setTakeAKey] = useState("");
  const [takeBKey, setTakeBKey] = useState("");
  const [abAuditioning, setAbAuditioning] = useState(false);
  const [downloadingTakeKey, setDownloadingTakeKey] = useState("");
  const [sourceLevel, setSourceLevel] = useState(0.55);
  const [bassContextLevel, setBassContextLevel] = useState(0.26);
  const audioContextRef = useRef(null);
  const sourceAudioRef = useRef(null);
  const activeNodesRef = useRef([]);
  const sequenceIdRef = useRef(0);
  const sequenceTimersRef = useRef(new Set());
  const prevSessionIdRef = useRef(null);

  useEffect(() => {
    if (!session?.id) return;
    setCandidateClipId(session.id);
  }, [session?.id]);

  const refreshBassCandidates = useCallback(async () => {
    if (!session?.id) return;
    const rows = await listBassCandidates(session.id);
    setBassCandidateRuns(Array.isArray(rows) ? rows : []);
  }, [session?.id]);

  useEffect(() => {
    if (!session?.id) {
      setBassCandidateRuns([]);
      setOpenTakeRolls({});
      setTakeNotesByKey({});
      setLoadingTakeNotes({});
      setPlayingTakeKey("");
      setTakeAKey("");
      setTakeBKey("");
      setAbAuditioning(false);
      setDownloadingTakeKey("");
      return;
    }
    refreshBassCandidates().catch(() => {});
  }, [session?.id, refreshBassCandidates]);

  const clearPlaybackResources = useCallback(() => {
    sequenceTimersRef.current.forEach((timerId) => {
      clearTimeout(timerId);
    });
    sequenceTimersRef.current.clear();
    activeNodesRef.current.forEach((pair) => {
      try {
        pair.osc.stop();
      } catch {
        // ignore already-stopped oscillator errors
      }
      try {
        pair.osc.disconnect();
      } catch {
        // ignore disconnect errors
      }
      try {
        pair.gain.disconnect();
      } catch {
        // ignore disconnect errors
      }
    });
    activeNodesRef.current = [];
    if (sourceAudioRef.current) {
      sourceAudioRef.current.pause();
      sourceAudioRef.current.currentTime = 0;
      sourceAudioRef.current = null;
    }
  }, []);

  const stopPlayback = useCallback(() => {
    clearPlaybackResources();
    sequenceIdRef.current += 1;
    setPlayingTakeKey("");
    setAbAuditioning(false);
  }, [clearPlaybackResources]);

  const beginPlaybackSequence = useCallback(() => {
    clearPlaybackResources();
    sequenceIdRef.current += 1;
    const nextSequenceId = sequenceIdRef.current;
    setPlayingTakeKey("");
    setAbAuditioning(false);
    return nextSequenceId;
  }, [clearPlaybackResources]);

  const registerSequenceTimer = useCallback((sequenceId, delayMs, callback) => {
    const timerId = window.setTimeout(() => {
      sequenceTimersRef.current.delete(timerId);
      if (sequenceIdRef.current !== sequenceId) return;
      callback();
    }, delayMs);
    sequenceTimersRef.current.add(timerId);
    return timerId;
  }, []);

  useEffect(() => {
    if (
      prevSessionIdRef.current &&
      session?.id &&
      prevSessionIdRef.current !== session.id
    ) {
      stopPlayback();
    }
    prevSessionIdRef.current = session?.id ?? null;
  }, [session?.id, stopPlayback]);

  useEffect(() => {
    return () => {
      stopPlayback();
      if (audioContextRef.current) {
        audioContextRef.current.close().catch(() => {});
      }
    };
  }, [stopPlayback]);

  const loadTakeNotes = useCallback(
    async (runId, takeId) => {
      if (!session?.id) return;
      const key = `${runId}::${takeId}`;
      if (takeNotesByKey[key]) return takeNotesByKey[key];
      if (loadingTakeNotes[key]) return null;
      setLoadingTakeNotes((prev) => ({ ...prev, [key]: true }));
      try {
        const notes = await getBassCandidateTakeNotes(session.id, runId, takeId);
        const nextNotes = Array.isArray(notes) ? notes : [];
        setTakeNotesByKey((prev) => ({ ...prev, [key]: nextNotes }));
        return nextNotes;
      } catch (e) {
        setError(e.message || String(e));
        return null;
      } finally {
        setLoadingTakeNotes((prev) => ({ ...prev, [key]: false }));
      }
    },
    [session?.id, takeNotesByKey, loadingTakeNotes, setError],
  );

  const onToggleTakeRoll = useCallback(
    (runId, takeId) => {
      const key = `${runId}::${takeId}`;
      const nextOpen = !openTakeRolls[key];
      setOpenTakeRolls((prev) => ({ ...prev, [key]: nextOpen }));
      if (nextOpen) {
        loadTakeNotes(runId, takeId).catch(() => {});
      }
    },
    [openTakeRolls, loadTakeNotes],
  );

  useEffect(() => {
    if (!session?.id || bassCandidateRuns.length === 0) return;
    let cancelled = false;
    const visibleRuns = bassCandidateRuns.slice(0, MAX_VISIBLE_RUNS);
    const visibleTakePairs = [];
    for (const run of visibleRuns) {
      for (const take of run.takes ?? []) {
        visibleTakePairs.push([run.run_id, take.take_id]);
        if (visibleTakePairs.length >= MAX_PREFETCH_TAKES) break;
      }
      if (visibleTakePairs.length >= MAX_PREFETCH_TAKES) break;
    }

    const prefetch = async () => {
      for (const [runId, takeId] of visibleTakePairs) {
        if (cancelled) return;
        await loadTakeNotes(runId, takeId);
      }
    };
    prefetch().catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [session?.id, bassCandidateRuns, loadTakeNotes]);

  const playTake = useCallback(
    async (runId, takeId, options = {}) => {
      const { skipStop = false, sequenceId = null, onEnded = null } = options;
      if (!session?.id) return;
      const key = `${runId}::${takeId}`;
      let seqId = sequenceId;
      if (!skipStop) {
        seqId = beginPlaybackSequence();
      }
      if (seqId == null) {
        seqId = sequenceIdRef.current;
      }
      let notes = takeNotesByKey[key];
      if (!notes) {
        notes = await loadTakeNotes(runId, takeId);
      }
      if (!Array.isArray(notes) || notes.length === 0) {
        setStatus("No notes available for playback.");
        return false;
      }
      if (sequenceIdRef.current !== seqId) return false;
      if (!audioContextRef.current) {
        audioContextRef.current = new window.AudioContext();
      }
      const ctx = audioContextRef.current;
      if (ctx.state === "suspended") {
        await ctx.resume();
      }

      const now = ctx.currentTime + 0.03;
      const maxEnd = notes.reduce((acc, n) => Math.max(acc, Number(n.end) || 0), 0);
      const scheduleLaneNotes = (laneNotes, voice) => {
        const pairs = [];
        laneNotes.forEach((n) => {
          const start = now + Math.max(0, Number(n.start) || 0);
          const end = now + Math.max(0, Number(n.end) || 0);
          const velocity = Math.max(0, Math.min(127, Number(n.velocity) || 80));
          const amp = (velocity / 127) * voice.gain;
          const releaseStart = Math.max(start + voice.attack, end - voice.release);
          const osc = ctx.createOscillator();
          const gain = ctx.createGain();
          osc.type = voice.wave;
          osc.frequency.value = voice.fixedFreq ?? 440 * 2 ** ((Number(n.pitch) - 69) / 12);
          gain.gain.setValueAtTime(0, now);
          gain.gain.linearRampToValueAtTime(amp, start + voice.attack);
          gain.gain.setValueAtTime(amp, releaseStart);
          gain.gain.linearRampToValueAtTime(0, end);
          osc.connect(gain);
          gain.connect(ctx.destination);
          osc.start(start);
          osc.stop(Math.max(end, start + 0.02));
          pairs.push({ osc, gain });
        });
        return pairs;
      };
      const activePairs = scheduleLaneNotes(notes, {
        wave: "triangle",
        gain: 0.2,
        attack: 0.01,
        release: 0.02,
      });
      activeNodesRef.current = activePairs;
      setPlayingTakeKey(key);
      setStatus(`Playing ${takeId}…`);
      registerSequenceTimer(seqId, Math.ceil((maxEnd + 0.1) * 1000), () => {
        setPlayingTakeKey("");
        if (typeof onEnded === "function") {
          onEnded();
        }
      });
      return true;
    },
    [session?.id, takeNotesByKey, loadTakeNotes, setStatus, beginPlaybackSequence, registerSequenceTimer],
  );

  const playTakeInContext = useCallback(
    async (runId, takeId) => {
      if (!session?.id) return false;
      const key = `${runId}::${takeId}`;
      const seqId = beginPlaybackSequence();
      let bassNotes = takeNotesByKey[key];
      if (!bassNotes) {
        bassNotes = await loadTakeNotes(runId, takeId);
      }
      if (!Array.isArray(bassNotes) || bassNotes.length === 0) {
        setStatus("No candidate bass notes available for contextual playback.");
        return false;
      }
      if (sequenceIdRef.current !== seqId) return false;
      if (!audioContextRef.current) {
        audioContextRef.current = new window.AudioContext();
      }
      const ctx = audioContextRef.current;
      if (ctx.state === "suspended") {
        await ctx.resume();
      }
      const now = ctx.currentTime + 0.03;
      const drumsNotes = Array.isArray(session?.lanes?.drums?.notes) ? session.lanes.drums.notes : [];
      const chordsNotes = Array.isArray(session?.lanes?.chords?.notes) ? session.lanes.chords.notes : [];
      const leadNotes = Array.isArray(session?.lanes?.lead?.notes) ? session.lanes.lead.notes : [];

      const scheduleLaneNotes = (laneNotes, voice) => {
        const pairs = [];
        laneNotes.forEach((n) => {
          const start = now + Math.max(0, Number(n.start) || 0);
          const end = now + Math.max(0, Number(n.end) || 0);
          const velocity = Math.max(0, Math.min(127, Number(n.velocity) || 80));
          const amp = (velocity / 127) * voice.gain;
          const releaseStart = Math.max(start + voice.attack, end - voice.release);
          const osc = ctx.createOscillator();
          const gain = ctx.createGain();
          osc.type = voice.wave;
          osc.frequency.value = voice.fixedFreq ?? 440 * 2 ** ((Number(n.pitch) - 69) / 12);
          gain.gain.setValueAtTime(0, now);
          gain.gain.linearRampToValueAtTime(amp, start + voice.attack);
          gain.gain.setValueAtTime(amp, releaseStart);
          gain.gain.linearRampToValueAtTime(0, end);
          osc.connect(gain);
          gain.connect(ctx.destination);
          osc.start(start);
          osc.stop(Math.max(end, start + 0.02));
          pairs.push({ osc, gain });
        });
        return pairs;
      };

      const activePairs = [
        ...scheduleLaneNotes(drumsNotes, {
          wave: "square",
          gain: 0.075,
          attack: 0.0008,
          release: 0.02,
          fixedFreq: 170,
        }),
        ...scheduleLaneNotes(chordsNotes, {
          wave: "triangle",
          gain: 0.06,
          attack: 0.018,
          release: 0.12,
        }),
        ...scheduleLaneNotes(leadNotes, {
          wave: "sine",
          gain: 0.038,
          attack: 0.008,
          release: 0.022,
        }),
        ...scheduleLaneNotes(bassNotes, {
          wave: "sawtooth",
          gain: bassContextLevel,
          attack: 0.007,
          release: 0.04,
        }),
      ];
      activeNodesRef.current = activePairs;
      setPlayingTakeKey(key);
      setStatus(`Playing ${takeId} in context…`);
      const maxEnd = Math.max(
        ...bassNotes.map((n) => Number(n.end) || 0),
        ...drumsNotes.map((n) => Number(n.end) || 0),
        ...chordsNotes.map((n) => Number(n.end) || 0),
        ...leadNotes.map((n) => Number(n.end) || 0),
        0,
      );
      registerSequenceTimer(seqId, Math.ceil((maxEnd + 0.12) * 1000), () => {
        setPlayingTakeKey("");
      });
      return true;
    },
    [session, takeNotesByKey, loadTakeNotes, setStatus, beginPlaybackSequence, registerSequenceTimer, bassContextLevel],
  );

  const playTakeWithSource = useCallback(
    async (runId, takeId) => {
      if (!session?.id || !session?.reference_audio) return false;
      const key = `${runId}::${takeId}`;
      const seqId = beginPlaybackSequence();
      let bassNotes = takeNotesByKey[key];
      if (!bassNotes) {
        bassNotes = await loadTakeNotes(runId, takeId);
      }
      if (!Array.isArray(bassNotes) || bassNotes.length === 0) {
        setStatus("No candidate bass notes available for source-backed playback.");
        return false;
      }
      if (sequenceIdRef.current !== seqId) return false;
      if (!audioContextRef.current) {
        audioContextRef.current = new window.AudioContext();
      }
      const ctx = audioContextRef.current;
      if (ctx.state === "suspended") {
        await ctx.resume();
      }

      const audio = new window.Audio(referenceAudioUrl(session.id));
      audio.preload = "auto";
      audio.crossOrigin = "anonymous";
      audio.volume = Math.max(0, Math.min(1, Number(sourceLevel) || 0));
      sourceAudioRef.current = audio;
      const sourceStartAt = Math.max(
        0,
        Number(session?.engine_data?.source_analysis?.bar_start_anchor_used_seconds) ||
          Number(session?.reference_audio?.head_trim_seconds) ||
          0,
      );
      const waitForCanPlay = new Promise((resolve) => {
        if (audio.readyState >= 2) {
          resolve();
          return;
        }
        const onReady = () => {
          audio.removeEventListener("canplay", onReady);
          resolve();
        };
        audio.addEventListener("canplay", onReady);
      });
      await waitForCanPlay;
      if (sequenceIdRef.current !== seqId) return false;
      audio.currentTime = Math.min(sourceStartAt, Math.max(0, (audio.duration || sourceStartAt) - 0.05));
      await audio.play();

      const now = ctx.currentTime + 0.03;
      const pairs = [];
      bassNotes.forEach((n) => {
        const start = now + Math.max(0, Number(n.start) || 0);
        const end = now + Math.max(0, Number(n.end) || 0);
        const velocity = Math.max(0, Math.min(127, Number(n.velocity) || 80));
        const amp = (velocity / 127) * bassContextLevel;
        const releaseStart = Math.max(start + 0.008, end - 0.04);
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = "sawtooth";
        osc.frequency.value = 440 * 2 ** ((Number(n.pitch) - 69) / 12);
        gain.gain.setValueAtTime(0, now);
        gain.gain.linearRampToValueAtTime(amp, start + 0.008);
        gain.gain.setValueAtTime(amp, releaseStart);
        gain.gain.linearRampToValueAtTime(0, end);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start(start);
        osc.stop(Math.max(end, start + 0.02));
        pairs.push({ osc, gain });
      });
      activeNodesRef.current = pairs;
      setPlayingTakeKey(key);
      setStatus(`Playing ${takeId} against source audio…`);
      const maxEnd = Math.max(...bassNotes.map((n) => Number(n.end) || 0), 0);
      registerSequenceTimer(seqId, Math.ceil((maxEnd + 0.12) * 1000), () => {
        setPlayingTakeKey("");
      });
      return true;
    },
    [
      session,
      takeNotesByKey,
      loadTakeNotes,
      setStatus,
      beginPlaybackSequence,
      registerSequenceTimer,
      sourceLevel,
      bassContextLevel,
    ],
  );

  const parseTakeKey = useCallback((key) => {
    const [runId, takeId] = String(key).split("::");
    if (!runId || !takeId) return null;
    return { runId, takeId };
  }, []);

  const getTakeDurationSec = useCallback(async (runId, takeId) => {
    const key = `${runId}::${takeId}`;
    let notes = takeNotesByKey[key];
    if (!notes) {
      notes = await loadTakeNotes(runId, takeId);
    }
    if (!Array.isArray(notes) || notes.length === 0) return 0;
    return notes.reduce((acc, n) => Math.max(acc, Number(n.end) || 0), 0);
  }, [takeNotesByKey, loadTakeNotes]);

  const onAuditionAB = useCallback(async () => {
    if (!takeAKey || !takeBKey) {
      setStatus("Set both A and B takes first.");
      return;
    }
    if (takeAKey === takeBKey) {
      setStatus("A and B should be different takes.");
      return;
    }
    const a = parseTakeKey(takeAKey);
    const b = parseTakeKey(takeBKey);
    if (!a || !b) {
      setStatus("A/B selection is invalid.");
      return;
    }
    const seqId = beginPlaybackSequence();
    setAbAuditioning(true);
    const aOk = await playTake(a.runId, a.takeId, { skipStop: true, sequenceId: seqId });
    if (!aOk || sequenceIdRef.current !== seqId) {
      setAbAuditioning(false);
      return;
    }
    const aDur = await getTakeDurationSec(a.runId, a.takeId);
    const delayMs = Math.max(120, Math.ceil((aDur + 0.08) * 1000));
    registerSequenceTimer(seqId, delayMs, () => {
      playTake(b.runId, b.takeId, {
        skipStop: true,
        sequenceId: seqId,
        onEnded: () => setAbAuditioning(false),
      }).catch(() => {
        setAbAuditioning(false);
      });
    });
  }, [takeAKey, takeBKey, parseTakeKey, playTake, getTakeDurationSec, setStatus, beginPlaybackSequence, registerSequenceTimer]);

  const findTakeLabel = useCallback((key) => {
    const parsed = parseTakeKey(key);
    if (!parsed) return "—";
    const run = bassCandidateRuns.find((r) => r.run_id === parsed.runId);
    const take = run?.takes?.find((t) => t.take_id === parsed.takeId);
    if (!run || !take) return parsed.takeId;
    return `${take.take_id} (${run.run_id})`;
  }, [bassCandidateRuns, parseTakeKey]);

  const safeNumber = useCallback((value, fallback = null) => {
    const n = Number(value);
    return Number.isFinite(n) ? n : fallback;
  }, []);

  const stageLabel = useCallback((stage) => {
    const key = String(stage || "").trim();
    return STAGE_LABELS[key] ?? "Candidate";
  }, []);

  const onDownloadTakeMidi = useCallback(
    async (runId, takeId) => {
      if (!session?.id) return;
      const takeKey = `${runId}::${takeId}`;
      setError(null);
      setDownloadingTakeKey(takeKey);
      try {
        const blob = await downloadBassCandidateMidi(session.id, runId, takeId);
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `bass_candidate_${runId}_${takeId}.mid`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
        setStatus(`Downloaded MIDI for ${takeId}.`);
      } catch (e) {
        setError(e.message || String(e));
      } finally {
        setDownloadingTakeKey("");
      }
    },
    [session?.id, setError, setStatus],
  );

  const onGenerateBassCandidates = useCallback(async () => {
    if (!session?.id) return;
    setBusy(true);
    setError(null);
    try {
      const created = await generateBassCandidates(session.id, {
        take_count: Math.max(2, Math.min(12, Number(candidateTakeCount) || 4)),
        seed: candidateSeed.trim() ? Number(candidateSeed.trim()) : null,
        clip_id: candidateClipId.trim() || null,
      });
      await refreshBassCandidates();
      setStatus(`Generated ${created.take_count} bass candidates (${created.run_id}).`);
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }, [
    session?.id,
    candidateTakeCount,
    candidateSeed,
    candidateClipId,
    refreshBassCandidates,
    setBusy,
    setError,
    setStatus,
  ]);

  const onPromoteBassCandidate = useCallback(
    async (runId, takeId) => {
      if (!session?.id) return;
      setBusy(true);
      setError(null);
      try {
        const updated = await promoteBassCandidate(session.id, runId, takeId);
        setSession(updated);
        await refreshBassCandidates();
        setStatus(updated.message ?? `Promoted bass candidate ${takeId}.`);
      } catch (e) {
        setError(e.message || String(e));
      } finally {
        setBusy(false);
      }
    },
    [session?.id, refreshBassCandidates, setBusy, setError, setSession, setStatus],
  );

  const onAdjustSelectedBassBars = useCallback(async () => {
    if (!session?.id) return;
    const barStart = Number(adjustBarStart);
    const barEnd = Number(adjustBarEnd);
    if (!Number.isInteger(barStart) || !Number.isInteger(barEnd)) {
      setError("Start bar and end bar must be whole numbers.");
      return;
    }
    const body = {
      bar_start: barStart,
      bar_end: barEnd,
    };
    if (adjustSeed.trim()) {
      const seed = Number(adjustSeed.trim());
      if (!Number.isInteger(seed)) {
        setError("Variation seed must be a whole number.");
        return;
      }
      body.seed = seed;
    }
    setBusy(true);
    setError(null);
    try {
      const updated = await regenerateBassBars(session.id, body);
      setSession(updated);
      setStatus("Bars regenerated.");
    } catch (e) {
      const detail = e?.detail?.detail;
      setError(detail?.message || e.message || String(e));
    } finally {
      setBusy(false);
    }
  }, [session?.id, adjustBarStart, adjustBarEnd, adjustSeed, setBusy, setError, setSession, setStatus]);

  return (
    <details
      style={{
        marginBottom: "1rem",
        padding: "0.65rem 0.85rem",
        background: "#fff",
        border: "1px solid #e2e8f0",
        borderRadius: 10,
      }}
    >
      <summary style={{ cursor: "pointer", fontWeight: 600 }}>Bass Candidates (conditioning run)</summary>
      <div style={{ marginTop: 10, display: "grid", gap: "0.6rem", maxWidth: 900 }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 8 }}>
          <label style={{ display: "grid", gap: 4, fontSize: 13 }}>
            Take count
            <input
              type="number"
              min={2}
              max={12}
              value={candidateTakeCount}
              onChange={(e) => setCandidateTakeCount(Math.max(2, Math.min(12, Number(e.target.value) || 4)))}
            />
          </label>
          <label style={{ display: "grid", gap: 4, fontSize: 13 }}>
            Seed (optional)
            <input value={candidateSeed} onChange={(e) => setCandidateSeed(e.target.value)} placeholder="auto" />
          </label>
          <label style={{ display: "grid", gap: 4, fontSize: 13 }}>
            Clip ID (optional)
            <input value={candidateClipId} onChange={(e) => setCandidateClipId(e.target.value)} />
          </label>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button type="button" onClick={onGenerateBassCandidates} disabled={busy || !session?.id}>
            Generate Bass Candidates
          </button>
          <button type="button" onClick={refreshBassCandidates} disabled={busy || !session?.id}>
            Refresh Candidate Runs
          </button>
        </div>
        <div
          style={{
            display: "grid",
            gap: 8,
            padding: "0.55rem 0.65rem",
            border: "1px solid #e2e8f0",
            borderRadius: 8,
            background: "#f8fafc",
          }}
        >
          <strong style={{ fontSize: 13, color: "#334155" }}>Adjust Selected Bass Bars</strong>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))", gap: 8 }}>
            <label style={{ display: "grid", gap: 4, fontSize: 13 }}>
              Start bar
              <input
                type="number"
                min={0}
                max={Math.max(0, (session?.bar_count ?? 1) - 1)}
                value={adjustBarStart}
                onChange={(e) => setAdjustBarStart(e.target.value)}
              />
            </label>
            <label style={{ display: "grid", gap: 4, fontSize: 13 }}>
              End bar
              <input
                type="number"
                min={1}
                max={session?.bar_count ?? 1}
                value={adjustBarEnd}
                onChange={(e) => setAdjustBarEnd(e.target.value)}
              />
            </label>
            <label style={{ display: "grid", gap: 4, fontSize: 13 }}>
              Variation seed
              <input value={adjustSeed} onChange={(e) => setAdjustSeed(e.target.value)} placeholder="fresh" />
            </label>
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <button
              type="button"
              onClick={onAdjustSelectedBassBars}
              disabled={busy || !session?.id || !session?.lanes?.bass?.generated}
            >
              Regenerate Selected Bars
            </button>
            <span style={{ fontSize: 12, color: "#64748b" }}>
              Bars are zero-based; end bar is not included.
            </span>
          </div>
        </div>
        {session?.reference_audio ? (
          <div
            style={{
              display: "flex",
              gap: 10,
              flexWrap: "wrap",
              alignItems: "center",
              fontSize: 12,
              color: "#334155",
              padding: "0.4rem 0.5rem",
              border: "1px solid #e2e8f0",
              borderRadius: 8,
              background: "#f8fafc",
            }}
          >
            <strong>Source-backed audition</strong>
            <label style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
              Source level
              <input
                type="range"
                min={0}
                max={1}
                step={0.01}
                value={sourceLevel}
                onChange={(e) => setSourceLevel(Number(e.target.value))}
                disabled={busy}
              />
              {sourceLevel.toFixed(2)}
            </label>
            <label style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
              Bass level
              <input
                type="range"
                min={0}
                max={0.6}
                step={0.01}
                value={bassContextLevel}
                onChange={(e) => setBassContextLevel(Number(e.target.value))}
                disabled={busy}
              />
              {bassContextLevel.toFixed(2)}
            </label>
          </div>
        ) : null}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            flexWrap: "wrap",
            padding: "0.4rem 0.5rem",
            border: "1px solid #e2e8f0",
            borderRadius: 8,
            background: "#f8fafc",
            fontSize: 12,
            color: "#334155",
          }}
        >
          <strong>A/B:</strong>
          <span>A {takeAKey ? findTakeLabel(takeAKey) : "not set"}</span>
          <span>·</span>
          <span>B {takeBKey ? findTakeLabel(takeBKey) : "not set"}</span>
          <button
            type="button"
            onClick={onAuditionAB}
            disabled={abAuditioning || !takeAKey || !takeBKey || takeAKey === takeBKey}
            style={{ padding: "0.2rem 0.6rem", marginLeft: "auto" }}
          >
            {abAuditioning ? "Auditioning..." : "Audition A → B"}
          </button>
        </div>
        <div style={{ fontSize: 13, color: "#334155" }}>
          {bassCandidateRuns.length === 0
            ? "No candidate runs yet."
            : `${bassCandidateRuns.length} run(s) found for this session.`}
        </div>
        {bassCandidateRuns.slice(0, MAX_VISIBLE_RUNS).map((run) => (
          <div
            key={run.run_id}
            style={{
              border: "1px solid #e2e8f0",
              borderRadius: 8,
              padding: "0.55rem 0.65rem",
              background: "#f8fafc",
              display: "grid",
              gap: 6,
            }}
          >
            <div style={{ fontSize: 12, color: "#475569" }}>
              <strong>{run.run_id}</strong> · {run.take_count} take(s) · tempo {run.conditioning_tempo} · phase{" "}
              {run.conditioning_phase_offset}/4 · sections {run.conditioning_sections_count}
            </div>
            <div style={{ display: "grid", gap: 4 }}>
              {(run.takes ?? []).map((take) => {
                const isCurrent =
                  session?.current_bass_candidate_run_id === run.run_id &&
                  session?.current_bass_candidate_take_id === take.take_id;
                const takeKey = `${run.run_id}::${take.take_id}`;
                const isRollOpen = !!openTakeRolls[takeKey];
                const isNotesLoading = !!loadingTakeNotes[takeKey];
                const takeNotes = takeNotesByKey[takeKey] ?? [];
                const isPlaying = playingTakeKey === takeKey;
                const isDownloading = downloadingTakeKey === takeKey;
                const playbackStatus = isNotesLoading ? "Loading notes..." : isPlaying ? "Playing" : "Stopped";
                const playbackStatusColor = isNotesLoading
                  ? "#0369a1"
                  : isPlaying
                    ? "#166534"
                    : "#64748b";
                const selectionStage = String(take?.selection_stage || "").trim();
                const selectionLabel = stageLabel(selectionStage);
                const motifFamily = String(take?.motif_family || "").trim() || null;
                const signatureDistance = safeNumber(take?.signature_distance);
                const qualityFloorCutoff = safeNumber(take?.quality_floor_cutoff);
                const topPoolScore = safeNumber(take?.top_pool_score);
                const qualityTotal = safeNumber(take?.quality_total, 0);
                const qualityScores =
                  take?.quality_scores && typeof take.quality_scores === "object" ? take.quality_scores : {};
                const qualityReason = String(take?.quality_reason || "").trim();
                return (
                  <div
                    key={take.take_id}
                    style={{
                      display: "grid",
                      gap: 6,
                      border: "1px solid #e2e8f0",
                      borderRadius: 8,
                      background: "#fff",
                      padding: "0.45rem 0.55rem",
                    }}
                  >
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", fontSize: 13 }}>
                      <code style={{ background: "#e2e8f0", borderRadius: 4, padding: "2px 6px" }}>{take.take_id}</code>
                      {isCurrent ? (
                        <span
                          style={{
                            background: "#dcfce7",
                            color: "#166534",
                            border: "1px solid #86efac",
                            borderRadius: 999,
                            padding: "1px 8px",
                            fontSize: 12,
                            fontWeight: 600,
                          }}
                        >
                          Current
                        </span>
                      ) : null}
                      <span>seed {take.seed}</span>
                      <span>{take.note_count} notes</span>
                      <span
                        style={{
                          background: "#e2e8f0",
                          color: "#334155",
                          borderRadius: 999,
                          padding: "1px 8px",
                          fontSize: 12,
                          fontWeight: 600,
                        }}
                      >
                        {selectionLabel}
                      </span>
                      <span style={{ color: "#334155", fontWeight: 600 }}>
                        Quality {qualityTotal.toFixed(3)}
                      </span>
                      <button
                        type="button"
                        onClick={() => onDownloadTakeMidi(run.run_id, take.take_id)}
                        disabled={isDownloading}
                        style={{ padding: "0.2rem 0.6rem" }}
                      >
                        {isDownloading ? "Downloading..." : "Download MIDI"}
                      </button>
                      <button
                        type="button"
                        onClick={() => setTakeAKey(takeKey)}
                        style={{
                          padding: "0.2rem 0.6rem",
                          background: takeAKey === takeKey ? "#dbeafe" : undefined,
                          borderColor: takeAKey === takeKey ? "#93c5fd" : undefined,
                        }}
                      >
                        {takeAKey === takeKey ? "A set" : "Set A"}
                      </button>
                      <button
                        type="button"
                        onClick={() => setTakeBKey(takeKey)}
                        style={{
                          padding: "0.2rem 0.6rem",
                          background: takeBKey === takeKey ? "#fce7f3" : undefined,
                          borderColor: takeBKey === takeKey ? "#f9a8d4" : undefined,
                        }}
                      >
                        {takeBKey === takeKey ? "B set" : "Set B"}
                      </button>
                      <button
                        type="button"
                        onClick={() => onToggleTakeRoll(run.run_id, take.take_id)}
                        disabled={isNotesLoading}
                        style={{ padding: "0.2rem 0.6rem" }}
                      >
                        {isRollOpen ? "Hide Roll" : "Show Roll"}
                      </button>
                      <button
                        type="button"
                        onClick={() => playTake(run.run_id, take.take_id)}
                        disabled={isNotesLoading || isPlaying}
                        style={{ padding: "0.2rem 0.6rem" }}
                      >
                        {isPlaying ? "Playing…" : "Play"}
                      </button>
                      <button
                        type="button"
                        onClick={() => playTakeInContext(run.run_id, take.take_id)}
                        disabled={isNotesLoading || isPlaying}
                        style={{ padding: "0.2rem 0.6rem" }}
                      >
                        Play In Context
                      </button>
                      {session?.reference_audio ? (
                        <button
                          type="button"
                          onClick={() => playTakeWithSource(run.run_id, take.take_id)}
                          disabled={isNotesLoading || isPlaying}
                          style={{ padding: "0.2rem 0.6rem" }}
                        >
                          Play With Source
                        </button>
                      ) : null}
                      <button
                        type="button"
                        onClick={stopPlayback}
                        disabled={!isPlaying}
                        style={{ padding: "0.2rem 0.6rem" }}
                      >
                        Stop
                      </button>
                      <button
                        type="button"
                        onClick={() => onPromoteBassCandidate(run.run_id, take.take_id)}
                        disabled={busy}
                        style={{ padding: "0.2rem 0.6rem" }}
                      >
                        Use This Take
                      </button>
                      <span
                        style={{
                          marginLeft: "auto",
                          fontSize: 12,
                          fontWeight: 600,
                          color: playbackStatusColor,
                        }}
                      >
                        {playbackStatus}
                      </span>
                    </div>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", fontSize: 12, color: "#475569" }}>
                      {motifFamily ? <span>Motif: {motifFamily.replaceAll("_", " ")}</span> : null}
                      {qualityReason ? <span style={{ color: "#64748b" }}>{qualityReason}</span> : null}
                    </div>
                    <details style={{ fontSize: 12, color: "#64748b" }}>
                      <summary style={{ cursor: "pointer" }}>Take details</summary>
                      <div style={{ marginTop: 6, display: "grid", gap: 4 }}>
                        <div>Selection stage: {selectionStage || "n/a"}</div>
                        <div>
                          Signature distance: {signatureDistance != null ? signatureDistance.toFixed(3) : "n/a"}
                        </div>
                        <div>
                          Floor cutoff: {qualityFloorCutoff != null ? qualityFloorCutoff.toFixed(3) : "n/a"}
                          {" · "}
                          Top pool score: {topPoolScore != null ? topPoolScore.toFixed(3) : "n/a"}
                        </div>
                        {Object.keys(qualityScores).length > 0 ? (
                          <div>
                            Score breakdown:{" "}
                            {Object.entries(qualityScores)
                              .map(([k, v]) => `${k}=${safeNumber(v, 0).toFixed(3)}`)
                              .join(" · ")}
                          </div>
                        ) : null}
                      </div>
                    </details>
                    {isRollOpen ? (
                      <>
                        {isNotesLoading ? (
                          <div style={{ fontSize: 12, color: "#64748b" }}>Loading take notes…</div>
                        ) : null}
                        <PianoRollPreview
                          notes={takeNotes}
                          barCount={session?.bar_count ?? 8}
                          tempoBpm={session?.tempo ?? 120}
                          generated={!isNotesLoading}
                          accent="#8b5cf6"
                        />
                      </>
                    ) : null}
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </details>
  );
}
