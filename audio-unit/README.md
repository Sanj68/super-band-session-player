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

Do not install it until Sanjeev has reviewed and approved the diff. After approval, copy the component to:

```text
~/Library/Audio/Plug-Ins/Components/
```

## Backend

Start the backend with the bridge flag enabled:

```bash
cd backend
SESSION_PLAYER_ENABLE_GROOVE_BRIDGE=true uvicorn app.main:app --reload
```

## Configuration

The plugin reads config from environment variables first, then from:

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
