import { useMemo, useState } from "react";
import {
  analyzeReferenceAudio,
  createSession,
  generateSession,
  patchSession,
  uploadReferenceAudio,
} from "../api/client.js";
import { getSourceGrooveSummary } from "../utils/sourceGroove.js";

const PC_TO_KEY = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];

function detectedBarsFromSession(session) {
  const sections = session?.engine_data?.source_analysis?.sections ?? [];
  if (Array.isArray(sections) && sections.length > 0) {
    const maxEnd = Math.max(...sections.map((s) => Number(s.end_bar) || 0), 0);
    if (maxEnd > 0) return maxEnd + 1;
  }
  const barStarts = session?.engine_data?.source_analysis?.bar_starts_seconds ?? [];
  if (Array.isArray(barStarts) && barStarts.length > 1) return barStarts.length - 1;
  return session?.bar_count ?? 8;
}

export default function UploadFirstEntryPanel({
  session,
  setSession,
  busy,
  setBusy,
  setError,
  setStatus,
  setTempo,
  setKeyNote,
  setScale,
  setBars,
}) {
  const [selectedFile, setSelectedFile] = useState(null);
  const source = session?.engine_data?.source_analysis ?? null;
  const refAudio = session?.reference_audio ?? null;
  const grooveSummary = session && source ? getSourceGrooveSummary(session) : null;

  const warnings = useMemo(() => {
    if (!source) return [];
    const out = [];
    if ((source.tempo_confidence ?? 1) < 0.55) out.push("Low tempo confidence.");
    if ((source.tonal_center_confidence ?? 1) < 0.5) out.push("Low key-center confidence.");
    if ((source.scale_mode_confidence ?? 1) < 0.5) out.push("Low scale confidence.");
    if ((source.bar_start_confidence ?? 1) < 0.5) out.push("Low bar-start confidence.");
    return out;
  }, [source]);

  const onUploadFirst = async () => {
    if (!selectedFile) return;
    setBusy(true);
    setError(null);
    try {
      let activeSession = session;
      if (!activeSession?.id) {
        const created = await createSession({
          tempo: 108,
          key: "C",
          scale: "major",
          bar_count: 8,
        });
        activeSession = created.session;
        setSession(activeSession);
      }
      const uploaded = await uploadReferenceAudio(activeSession.id, selectedFile);
      setSession(uploaded);
      setStatus("Reference uploaded. Run Analyze to detect musical context.");
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const onAnalyze = async () => {
    if (!session?.id || !session.reference_audio) return;
    setBusy(true);
    setError(null);
    try {
      const analyzed = await analyzeReferenceAudio(session.id);
      setSession(analyzed);
      setStatus(analyzed.message ?? "Reference analyzed.");
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const onApplyAndGenerateFromReference = async () => {
    if (!session?.id || !source) return;
    const tempoEstimate = Math.max(40, Math.min(240, Math.round(Number(source.tempo_estimate_bpm) || 108)));
    const keyGuess = PC_TO_KEY[Math.max(0, Math.min(11, Number(source.tonal_center_pc_guess) || 0))];
    const scaleGuess = source.scale_mode_guess || "major";
    const barsGuess = Math.max(1, Math.min(128, detectedBarsFromSession(session)));
    setBusy(true);
    setError(null);
    try {
      await patchSession(session.id, {
        tempo: tempoEstimate,
        key: keyGuess,
        scale: scaleGuess,
        bar_count: barsGuess,
      });
      const generated = await generateSession(session.id);
      setSession(generated.session);
      setTempo(tempoEstimate);
      setKeyNote(keyGuess);
      setScale(scaleGuess);
      setBars(barsGuess);
      setStatus("Applied detected context and generated lanes from this same reference session.");
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section
      style={{
        marginBottom: "1rem",
        padding: "0.8rem 0.9rem",
        border: "2px solid #cbd5e1",
        borderRadius: 12,
        background: "#f8fafc",
      }}
    >
      <h2 style={{ margin: 0, fontSize: "1.05rem" }}>Upload First Workflow</h2>
      <p style={{ margin: "0.35rem 0 0", fontSize: 13, color: "#475569" }}>
        Start here: upload source audio, analyze context, then apply detected values and generate.
      </p>
      <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <input
          type="file"
          accept=".wav,.mp3,.flac,.ogg,.m4a,.aac,audio/*"
          disabled={busy}
          onChange={(e) => setSelectedFile(e.target.files?.[0] ?? null)}
        />
        <button type="button" onClick={onUploadFirst} disabled={busy || !selectedFile}>
          {session?.id ? "Upload Reference" : "Create Session + Upload"}
        </button>
        <button type="button" onClick={onAnalyze} disabled={busy || !session?.id || !session?.reference_audio}>
          Analyze
        </button>
        <button type="button" onClick={onApplyAndGenerateFromReference} disabled={busy || !session?.id || !source}>
          Apply + Generate From Reference
        </button>
      </div>

      <div style={{ marginTop: 10, display: "grid", gap: 4, fontSize: 13, color: "#334155" }}>
        <div>
          <strong>Reference:</strong> {refAudio?.filename ?? "none"} · analyzed {refAudio?.analyzed ? "yes" : "no"} · duration{" "}
          {Number(refAudio?.duration_seconds ?? 0).toFixed(2)}s · head trim{" "}
          {Number(refAudio?.head_trim_seconds ?? 0).toFixed(2)}s
        </div>
        <div>
          <strong>Detected tempo:</strong> {source ? Number(source.tempo_estimate_bpm ?? source.tempo ?? 0).toFixed(1) : "—"}{" "}
          {source ? `(conf ${Number(source.tempo_confidence ?? 0).toFixed(2)})` : ""}
        </div>
        <div>
          <strong>Detected key / scale:</strong>{" "}
          {source
            ? `${PC_TO_KEY[Math.max(0, Math.min(11, Number(source.tonal_center_pc_guess) || 0))]} ${source.scale_mode_guess ?? "major"}`
            : "—"}{" "}
          {source
            ? `(key conf ${Number(source.tonal_center_confidence ?? 0).toFixed(2)}, scale conf ${Number(source.scale_mode_confidence ?? 0).toFixed(2)})`
            : ""}
        </div>
        <div>
          <strong>Detected bars / sections:</strong> {source ? detectedBarsFromSession(session) : "—"} bar(s)
          {Array.isArray(source?.sections) && source.sections.length > 0
            ? ` · ${source.sections.map((s) => `${s.label}[${s.start_bar}-${s.end_bar}]`).join(" · ")}`
            : ""}
        </div>
        <div>
          <strong>Source-analysis status:</strong> {source?.source_lane ?? "none"}
        </div>
        {refAudio?.analyzed && grooveSummary ? (
          <div style={{ fontSize: 12, color: "#475569", lineHeight: 1.45 }}>
            <strong>Source groove:</strong> {grooveSummary.detected ? "detected" : "not detected"} ·{" "}
            <strong>resolution</strong> {grooveSummary.detected ? `${grooveSummary.resolution}/bar` : "—"} ·{" "}
            <strong>bars</strong> {grooveSummary.barsMapped}/{grooveSummary.barCount} ·{" "}
            <strong>avg conf</strong>{" "}
            {grooveSummary.avgConfidence != null ? grooveSummary.avgConfidence.toFixed(2) : "—"} ·{" "}
            <strong>kick</strong> {grooveSummary.kickSummary} · <strong>snare</strong> {grooveSummary.snareSummary}
          </div>
        ) : null}
        {warnings.length > 0 ? (
          <div style={{ color: "#b45309" }}>
            <strong>Warnings:</strong> {warnings.join(" · ")}
          </div>
        ) : source ? (
          <div style={{ color: "#15803d" }}>
            <strong>Confidence:</strong> detection confidence looks healthy.
          </div>
        ) : null}
      </div>
    </section>
  );
}
