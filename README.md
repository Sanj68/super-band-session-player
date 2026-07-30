# super-band-session-player

Local source-aware MIDI session generator. Provide a source, key, tempo, and
feel; get back a coordinated multi-lane MIDI arrangement (drums, bass, chords,
lead) as editable MIDI.

No remote database or authentication. Session state persists locally and the
product runs entirely on the machine.

## What it does

- Generates MIDI parts for multiple lanes: drums, bass, chords, lead
- Each lane uses rule-based generation with configurable style profiles (e.g. bass styles: supportive, melodic, rhythmic, slap, fusion; player profiles: bootsy, marcus, pino)
- Session context (anchor chords, density, kick/snare weighting) is shared across lanes for coherent output
- Exports via `pretty_midi`
- React/Vite frontend with a piano roll preview, lane cards, and saved setups panel

## Structure

```
backend/
  app/
    main.py                   FastAPI entrypoint
    routes/
      session_routes.py       Session generate/export endpoints
      setup_routes.py         Saved setups management
    models/
      session.py              Session request/response models
      setup.py                Setup models
    services/
      generator.py            Top-level MIDI generation coordinator
      bass_generator.py       Bass lane
      chord_generator.py      Chord lane
      drum_generator.py       Drum lane
      lead_generator.py       Lead lane
      session_context.py      Shared session state and density logic
      anchor_lane_roles.py    Role-based voicing helpers
      midi_export.py          MIDI file export
    utils/
      music_theory.py         Scale, chord, and interval utilities
frontend/
  src/
    App.jsx
    components/
      LaneCard.jsx
      PianoRollPreview.jsx
      SavedSetupsPanel.jsx
      SessionControls.jsx
      SessionComparePanel.jsx
    api/client.js
```

## Run

**Backend**
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install fastapi uvicorn pretty_midi

uvicorn app.main:app --reload   # http://localhost:8000
```

**Frontend**
```bash
cd frontend
npm install
npm run dev   # http://localhost:5173
```

## Logic Quickstart

The normal macOS setup runs the backend as a per-user LaunchAgent on
`127.0.0.1:8001`:

```bash
scripts/session_player_backend.sh install   # once
scripts/session_player_backend.sh status
```

The agent starts at login, restarts after a crash, enables the live Bridge,
and refuses to launch if port 8001 is already owned. Its startup preflight also
checks that the AU configuration uses loopback port 8001, that the configured
session exists, and that the durable session snapshot—including any acceptance
receipt—is valid. Port 8000 is never used by Session Player.

Operations:

```bash
scripts/session_player_backend.sh start
scripts/session_player_backend.sh stop
scripts/session_player_backend.sh restart
scripts/session_player_backend.sh health
scripts/session_player_backend.sh logs
```

Reference-audio uploads are streamed in bounded chunks with a 25 MiB maximum.
After a replacement is durably committed, the superseded blob is removed only
when no current session or recoverable Bass history still references it.

Reference-audio garbage collection is deliberately two-phase. Stop the managed
backend, run a hashed dry run, then supply that exact plan digest to apply it:

```bash
scripts/session_player_backend.sh stop
backend/.venv/bin/python backend/tools/reference_audio_gc.py
backend/.venv/bin/python backend/tools/reference_audio_gc.py \
  --apply --confirm-plan-sha256 <digest-from-dry-run>
scripts/session_player_backend.sh start
```

Both runs write receipts under
`~/Library/Application Support/Session Player/GC Receipts/`. Apply refuses if
the inventory, size, modification time, content hash, or live/history
references changed after the dry run.

Saved setups and take evaluations also fail closed. If either JSON document is
invalid or unreadable, the backend returns HTTP 503 and preserves the original
beside it as `*.quarantine-*`. The quarantine is a recovery barrier: restore a
validated copy to the canonical filename (or explicitly archive/remove the
quarantine before starting a new empty store) rather than retrying writes over
unknown state.

For development without the managed service:

1. Stop the agent:
   ```bash
   scripts/session_player_backend.sh stop
   ```
2. Start the backend:
   ```bash
   cd backend
   source .venv/bin/activate
   SESSION_PLAYER_ENABLE_GROOVE_BRIDGE=true uvicorn app.main:app --reload --port 8001
   ```
3. Start the frontend:
   ```bash
   cd frontend
   npm run dev
   ```
4. Open `http://localhost:5173`, choose the session settings, and generate the session.
5. Click **Download MIDI for Logic** for one combined MIDI file, or download individual lane MIDI files for drums, bass, chords, and lead.
6. Drag the downloaded `.mid` file into Logic.
7. Assign Logic instruments to the imported MIDI tracks.

## Live source-to-bass proof

1. Confirm the managed backend is healthy with
   `scripts/session_player_backend.sh health`.
2. Create the working session in the web app. Analyser AUs with no explicit
   session configured automatically bind to the newest created session.
3. On the source track or bus, insert both audio effects:
   - **Session Player Bridge** for groove evidence.
   - **Session Player Listener** for harmonic evidence.
4. On a software-instrument bass track, insert **Session Player Bass** in the
   MIDI FX slot before the bass instrument.
5. Play the source for at least two complete bars. The Listener indicator turns
   green once its analysis reaches the backend.
6. Regenerate the bassist or enter a command such as `busier`, `redo bar 2`, or
   `turnaround on bar 4`.
7. Use **KEEP** before a favourite idea. Every regeneration is also preserved
   automatically; **Earlier** and **Later** recall exact prior MIDI and controls.
8. Download the bass lane from the web app whenever an editable MIDI region is
   required.

An explicit `SESSION_PLAYER_SESSION_ID` or
`~/Library/Application Support/Session Player Bridge/config.json` binding still
overrides automatic newest-session selection.

When another local service owns port 8000, the Bass MIDI FX API can be moved
without rebuilding by adding `plugin_api_base_url` to that config file:

```json
{
  "api_base_url": "http://127.0.0.1:8001/api/bridge",
  "plugin_api_base_url": "http://127.0.0.1:8001/api/plugin"
}
```

`SESSION_PLAYER_PLUGIN_URL` overrides the file value for the Bass MIDI FX.

## Desktop app (Tauri)

A native macOS/Windows wrapper lives in `desktop/`. It embeds the built frontend and talks to the backend over `http://127.0.0.1:8000`.

**Prerequisites:** Rust toolchain (`rustup`), Node ≥ 18.

**Dev mode** (hot-reload via the Vite dev server — backend must already be running):
```bash
cd desktop
npm install
npm run desktop:dev
```

**Production build** (bundles the compiled frontend into a native `.app` / `.exe`):
```bash
cd desktop
npm install
npm run desktop:build   # output: desktop/src-tauri/target/release/bundle/
```

> First build downloads and compiles Tauri's Rust crates — expect 5–10 min on a cold cache.

To add an app icon, place a 1024×1024 PNG at `desktop/app-icon.png` and run:
```bash
cd desktop && npx @tauri-apps/cli icon app-icon.png
```
Then reference the generated files in `desktop/src-tauri/tauri.conf.json` under `bundle.icon`.

## Status

Local MVP. Rule-based generation only — no ML models or external APIs required.

## Audio Analysis Validation Pack

Before changing audio->generation behavior, use the validation pack scaffold:

- `backend/data/validation_pack/README.md`
- `backend/data/validation_pack/manifest.json`
- `backend/tools/run_validation_pack.py`

Run:

```bash
cd backend
. .venv/bin/activate
python tools/run_validation_pack.py --api-base http://127.0.0.1:8000
```

Add 5 local clips under `backend/data/validation_pack/clips/` and fill expected fields in the manifest.
