import { afterEach, describe, expect, it, vi } from "vitest";

import { createSession } from "../api/client.js";

describe("API client errors", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("preserves structured recovery status from a failed response", async () => {
    const detail = {
      error: "local_store_recovery_required",
      message: "The local session store needs recovery.",
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 503,
        text: vi.fn().mockResolvedValue(JSON.stringify({ detail })),
      }),
    );

    const request = createSession({ tempo: 116 });

    await expect(request).rejects.toMatchObject({
      message: "The local session store needs recovery.",
      status: 503,
      code: "local_store_recovery_required",
      apiDetail: detail,
    });
  });
});
