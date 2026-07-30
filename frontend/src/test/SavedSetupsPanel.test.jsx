import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import SavedSetupsPanel from "../components/SavedSetupsPanel.jsx";

const setup = {
  name: "Pocket",
  session_preset: "fusion",
  drum_style: "funk",
  bass_style: "fusion",
  chord_style: "wide",
  lead_style: "fusion",
};

function makeProps(overrides = {}) {
  return {
    setups: [setup],
    saveName: "",
    setSaveName: vi.fn(),
    busy: false,
    onRefresh: vi.fn(),
    onSave: vi.fn(),
    onLoad: vi.fn(),
    onApplyToSession: vi.fn(),
    activeSessionId: null,
    onDelete: vi.fn(),
    ...overrides,
  };
}

describe("SavedSetupsPanel", () => {
  it("requires both a name for saving and a session for applying", () => {
    const props = makeProps();
    const { rerender } = render(<SavedSetupsPanel {...props} />);

    expect(
      screen.getByRole("button", { name: "Save current styles" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Apply to Current Session" }),
    ).toBeDisabled();

    rerender(
      <SavedSetupsPanel
        {...props}
        saveName="Late-night fusion"
        activeSessionId="session-1"
      />,
    );

    const saveButton = screen.getByRole("button", {
      name: "Save current styles",
    });
    const applyButton = screen.getByRole("button", {
      name: "Apply to Current Session",
    });
    expect(saveButton).toBeEnabled();
    expect(applyButton).toBeEnabled();

    fireEvent.click(saveButton);
    fireEvent.click(applyButton);
    expect(props.onSave).toHaveBeenCalledOnce();
    expect(props.onApplyToSession).toHaveBeenCalledWith(setup);
  });

  it("loads and deletes the selected setup", () => {
    const props = makeProps({ activeSessionId: "session-1" });
    render(<SavedSetupsPanel {...props} />);

    fireEvent.click(screen.getByRole("button", { name: "Load" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    expect(props.onLoad).toHaveBeenCalledWith(setup);
    expect(props.onDelete).toHaveBeenCalledWith("Pocket");
  });
});
