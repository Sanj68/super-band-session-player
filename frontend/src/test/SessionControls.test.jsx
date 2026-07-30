import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import SessionControls from "../components/SessionControls.jsx";

function makeProps(overrides = {}) {
  return {
    tempo: 116,
    setTempo: vi.fn(),
    keyNote: "C",
    setKeyNote: vi.fn(),
    scale: "major",
    setScale: vi.fn(),
    bars: 16,
    setBars: vi.fn(),
    leadStyle: "melodic",
    setLeadStyle: vi.fn(),
    leadPlayer: "",
    setLeadPlayer: vi.fn(),
    bassStyle: "supportive",
    setBassStyle: vi.fn(),
    bassEngine: "baseline",
    setBassEngine: vi.fn(),
    chordStyle: "simple",
    setChordStyle: vi.fn(),
    chordProgression: "",
    setChordProgression: vi.fn(),
    chordPlayer: "",
    setChordPlayer: vi.fn(),
    drumStyle: "straight",
    setDrumStyle: vi.fn(),
    leadInstrument: "flute",
    setLeadInstrument: vi.fn(),
    bassInstrument: "finger_bass",
    setBassInstrument: vi.fn(),
    bassPlayer: "",
    setBassPlayer: vi.fn(),
    chordInstrument: "piano",
    setChordInstrument: vi.fn(),
    drumKit: "standard",
    setDrumKit: vi.fn(),
    drumPlayer: "",
    setDrumPlayer: vi.fn(),
    sessionPreset: "",
    setSessionPreset: vi.fn(),
    anchorLane: "",
    onAnchorLaneChange: vi.fn(),
    busy: false,
    onGenerate: vi.fn(),
    ...overrides,
  };
}

describe("SessionControls", () => {
  it("applies every Fusion contract default when Fusion is selected", () => {
    const props = makeProps();
    render(<SessionControls {...props} />);

    fireEvent.change(screen.getByLabelText("Session preset"), {
      target: { value: "fusion" },
    });

    expect(props.setSessionPreset).toHaveBeenCalledWith("fusion");
    expect(props.setDrumStyle).toHaveBeenCalledWith("funk");
    expect(props.setBassStyle).toHaveBeenCalledWith("fusion");
    expect(props.setChordStyle).toHaveBeenCalledWith("wide");
    expect(props.setLeadStyle).toHaveBeenCalledWith("fusion");
    expect(props.setBassEngine).toHaveBeenCalledWith("phrase_v2");
    expect(props.setBassPlayer).toHaveBeenCalledWith("");
  });

  it("locks controls owned by the Fusion DNA contract", () => {
    render(
      <SessionControls
        {...makeProps({
          sessionPreset: "fusion",
          bassStyle: "fusion",
          bassEngine: "phrase_v2",
          chordStyle: "wide",
          drumStyle: "funk",
          leadStyle: "fusion",
        })}
      />,
    );

    expect(screen.getByLabelText("Bass engine")).toBeDisabled();
    expect(
      screen.getByLabelText("Bass player (contract adapters later)"),
    ).toBeDisabled();
    expect(
      screen.getByLabelText("Keys rhythm (set by Fusion DNA)"),
    ).toBeDisabled();
    expect(
      screen.getByLabelText("Drum rhythm (set by Fusion DNA)"),
    ).toBeDisabled();
  });
});
