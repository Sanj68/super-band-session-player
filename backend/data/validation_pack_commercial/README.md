# Commercial Reference Validation Pack

This pack is a first-pass validation set for known commercial references.

## Layout

- `clips/` - bounced reference WAV files for validation
- `manifest.json` - practical expectations (tempo, tonal center, mode, broad section count)

## Run

```bash
cd backend
. .venv/bin/activate
python tools/run_validation_pack.py \
  --api-base http://127.0.0.1:8000 \
  --manifest data/validation_pack_commercial/manifest.json
```
