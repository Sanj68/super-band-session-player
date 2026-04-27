# super-band-session-player

Local rule-based MIDI session generator. Provide a key, tempo, and feel; get back a multi-lane MIDI arrangement (drums, bass, chords, lead) as a downloadable file.

No database. No auth. Runs entirely locally.

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
