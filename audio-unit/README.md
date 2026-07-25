# Session Player Bridge AU

Audio Unit effect plugin for Logic Pro. It analyses the live audio stream and sends source feature frames to the Session Player bridge API.

## Build

```bash
cd audio-unit
cmake -S . -B build -G "Unix Makefiles" -DCMAKE_BUILD_TYPE=Release
cmake --build build
```

The compiled AU component is produced under:

```text
audio-unit/build/SessionPlayerBridge_artefacts/Release/AU/Session Player Bridge.component
```

After reviewing the diff, install the component with:

```bash
/usr/bin/ditto \
  "build/SessionPlayerBridge_artefacts/Release/AU/Session Player Bridge.component" \
  "$HOME/Library/Audio/Plug-Ins/Components/Session Player Bridge.component"
```

## Backend

Start the backend with the bridge flag enabled:

```bash
cd backend
SESSION_PLAYER_ENABLE_GROOVE_BRIDGE=true uvicorn app.main:app --reload
```

## Configuration

When `session_id` is omitted, the plugin asks the backend for the newest created
session and retains that binding for the life of the plugin instance. This is
the zero-configuration path for a single working session.

To pin an analyser to a particular session, provide an explicit binding. The
plugin reads environment variables first, then:

```text
~/Library/Application Support/Session Player Bridge/config.json
```

Supported keys:

```json
{
  "api_base_url": "http://127.0.0.1:8000/api/bridge",
  "session_id": "session-id-from-the-web-app",
  "source_id": "logic-live"
}
```

Environment variable equivalents:

```text
SESSION_PLAYER_BRIDGE_URL
SESSION_PLAYER_SESSION_ID
SESSION_PLAYER_SOURCE_ID
```

The Session Player Listener AU uses the same `api_base_url`, optional
`session_id`, `SESSION_PLAYER_BRIDGE_URL`, and `SESSION_PLAYER_SESSION_ID`
overrides.
