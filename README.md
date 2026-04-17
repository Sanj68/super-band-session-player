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

## Status

Local MVP. Rule-based generation only — no ML models or external APIs required.
