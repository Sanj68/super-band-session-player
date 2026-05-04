import { useState } from "react";
import { analyzeReferenceAudio, uploadReferenceAudio } from "../api/client.js";
import { getSourceGrooveSummary } from "../utils/sourceGroove.js";

function formatSecs(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "0.00s";
  return `${n.toFixed(2)}s`;
}

export default function ReferenceAudioPanel({ session, busy, setBusy, setError, setStatus, setSession }) {
  const [selectedFile, setSelectedFile] = useState(null);

  const onUpload = async () => {
    if (!session?.id || !selectedFile) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await uploadReferenceAudio(session.id, selectedFile);
      setSession(updated);
      setStatus(updated.message ?? "Reference audio uploaded.");
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const onAnalyze = async () => {
    if (!session?.id) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await analyzeReferenceAudio(session.id);
      setSession(updated);
      setStatus(updated.message ?? "Reference audio analyzed.");
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const ref = session?.reference_audio;
  const sourceStatus = session?.engine_data?.source_analysis?.source_lane ?? "none";
  const sa = session?.engine_data?.source_analysis ?? null;
  const grooveSummary = session && sa ? getSourceGrooveSummary(session) : null;

  return (
    <section
      style={{
        marginBottom: "1rem",
        padding: "0.75rem 0.85rem",
        border: "1px solid #e2e8f0",
        borderRadius: 10,
        background: "#fff",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <h2 style={{ margin: 0, fontSize: "1rem" }}>Reference Audio</h2>
        <span style={{ fontSize: 12, color: "#64748b" }}>
          Source analysis: <strong>{sourceStatus}</strong>
        </span>
      </div>
      <div style={{ marginTop: 8, display: "grid", gap: 6, fontSize: 13, color: "#334155" }}>
        <div>
          <strong>Filename:</strong> {ref?.filename ?? "none"}
        </div>
        <div>
          <strong>Analyzed:</strong> {ref?.analyzed ? "yes" : "no"}
        </div>
        <div>
          <strong>Duration:</strong> {formatSecs(ref?.duration_seconds)}
          {" · "}
          <strong>Head trim:</strong> {formatSecs(ref?.head_trim_seconds)}
        </div>
        {ref?.analyzed && grooveSummary ? (
          <div
            style={{
              marginTop: 2,
              padding: "0.45rem 0.55rem",
              borderRadius: 8,
              background: "#f1f5f9",
              fontSize: 12,
              color: "#334155",
              lineHeight: 1.45,
            }}
          >
            <div>
              <strong>Source groove:</strong> {grooveSummary.detected ? "detected" : "not detected"}
            </div>
            <div>
              <strong>Resolution:</strong> {grooveSummary.detected ? `${grooveSummary.resolution} slots/bar` : "—"}
            </div>
            <div>
              <strong>Bars mapped:</strong> {grooveSummary.barsMapped} / {grooveSummary.barCount}
            </div>
            <div>
              <strong>Avg groove confidence:</strong>{" "}
              {grooveSummary.avgConfidence != null ? grooveSummary.avgConfidence.toFixed(2) : "—"}
            </div>
            <div>
              <strong>Strong kick slots:</strong> {grooveSummary.kickSummary}
            </div>
            <div>
              <strong>Strong snare slots:</strong> {grooveSummary.snareSummary}
            </div>
          </div>
        ) : null}
      </div>
      <div style={{ marginTop: 10, display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <input
          type="file"
          accept=".wav,.mp3,.flac,.ogg,.m4a,.aac,audio/*"
          disabled={busy || !session?.id}
          onChange={(e) => setSelectedFile(e.target.files?.[0] ?? null)}
        />
        <button type="button" onClick={onUpload} disabled={busy || !session?.id || !selectedFile}>
          Upload Reference
        </button>
        <button type="button" onClick={onAnalyze} disabled={busy || !session?.id || !session?.reference_audio}>
          Analyze Reference
        </button>
      </div>
    </section>
  );
}
