import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ReferenceAudioPanel from "../components/ReferenceAudioPanel.jsx";
import {
  analyzeReferenceAudio,
  uploadReferenceAudio,
} from "../api/client.js";

vi.mock("../api/client.js", () => ({
  analyzeReferenceAudio: vi.fn(),
  uploadReferenceAudio: vi.fn(),
}));

function makeProps(overrides = {}) {
  return {
    session: { id: "session-1" },
    busy: false,
    setBusy: vi.fn(),
    setError: vi.fn(),
    setStatus: vi.fn(),
    setSession: vi.fn(),
    ...overrides,
  };
}

describe("ReferenceAudioPanel", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("uploads the selected audio and commits the returned session", async () => {
    const updated = {
      id: "session-1",
      message: "Reference accepted.",
      reference_audio: { filename: "pocket.wav" },
    };
    uploadReferenceAudio.mockResolvedValue(updated);
    const props = makeProps();
    const { container } = render(<ReferenceAudioPanel {...props} />);
    const file = new File(["audio"], "pocket.wav", { type: "audio/wav" });

    fireEvent.change(container.querySelector('input[type="file"]'), {
      target: { files: [file] },
    });
    fireEvent.click(screen.getByRole("button", { name: "Upload Reference" }));

    await waitFor(() => {
      expect(uploadReferenceAudio).toHaveBeenCalledWith("session-1", file);
    });
    expect(props.setError).toHaveBeenCalledWith(null);
    expect(props.setSession).toHaveBeenCalledWith(updated);
    expect(props.setStatus).toHaveBeenCalledWith("Reference accepted.");
    expect(props.setBusy.mock.calls).toEqual([[true], [false]]);
  });

  it("surfaces upload errors and always releases the busy state", async () => {
    uploadReferenceAudio.mockRejectedValue(
      new Error("Max upload size is 25MB."),
    );
    const props = makeProps();
    const { container } = render(<ReferenceAudioPanel {...props} />);
    const file = new File(["too large"], "huge.wav", {
      type: "audio/wav",
    });

    fireEvent.change(container.querySelector('input[type="file"]'), {
      target: { files: [file] },
    });
    fireEvent.click(screen.getByRole("button", { name: "Upload Reference" }));

    await waitFor(() => {
      expect(props.setError).toHaveBeenCalledWith(
        "Max upload size is 25MB.",
      );
    });
    expect(props.setSession).not.toHaveBeenCalled();
    expect(props.setBusy.mock.calls).toEqual([[true], [false]]);
  });

  it("analyzes an existing reference without requiring a new file", async () => {
    const updated = {
      id: "session-1",
      message: "Analysis complete.",
      reference_audio: { filename: "pocket.wav", analyzed: true },
    };
    analyzeReferenceAudio.mockResolvedValue(updated);
    const props = makeProps({
      session: {
        id: "session-1",
        reference_audio: { filename: "pocket.wav", analyzed: false },
      },
    });
    render(<ReferenceAudioPanel {...props} />);

    fireEvent.click(screen.getByRole("button", { name: "Analyze Reference" }));

    await waitFor(() => {
      expect(analyzeReferenceAudio).toHaveBeenCalledWith("session-1");
    });
    expect(props.setSession).toHaveBeenCalledWith(updated);
    expect(props.setStatus).toHaveBeenCalledWith("Analysis complete.");
    expect(props.setBusy.mock.calls).toEqual([[true], [false]]);
  });
});
