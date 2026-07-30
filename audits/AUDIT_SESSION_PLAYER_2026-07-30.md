# Session Player Audit — 2026-07-30

Repo: `/Users/sub/Work/Code/Projects/super-band-session-player`
Branch: `codex/session-player-audit-safety-20260730`
Audited commit: `75e9c7869aacdea550841bdd061dd471b6349861`

## Verdict

The Python/Fusion core is unusually well defended for a local MVP: its full
test suite, persistence validation, deterministic Fusion contract checks, and
native builds all pass. The controlled Logic pass completed on 30 July: both
fixed-bass pocket-pass comparisons were preferred, live pocket and expressive
articulation passed, transport stop produced no hanging notes, Bridge captured
97 frames, and no Listener click/dropout was heard through bars 1–17.

The Listener real-time-thread P0 found during this audit is now repaired and
closed by a clean post-repair Logic pass plus fresh harmonic-frame delivery.
The accepted bass register is also persisted and reproduced directly by the
Bass AU, with no separate Logic Transposer.
The accepted 116-BPM session is now immutable and bound to a canonical
SHA-256 receipt, while playback and transient Bridge overlays remain available.
The backend is now owned by a per-user LaunchAgent with fail-closed startup
preflight, health probing, crash restart, and an explicit port-8001 boundary
that does not collide with AutoFactory on port 8000.
The product is not yet ready for a broad “studio-safe” claim because the
Listener has not yet been stressed inside a production-size mix and the
remaining P1 data-integrity and remote-recovery findings are open.

Product code was changed to repair the Listener callback boundary, persist the
accepted Bass output register, seal the accepted fixture, own the backend
lifecycle, and add regression contracts for those boundaries. Live state was
deliberately split into isolated 88-BPM historical and 116-BPM current test
fixtures after the initial audit mistook stale 25 July acceptance notes for the
latest musical truth.

## Priority findings

### Retracted P0 — 88 BPM was historical, not the latest 116 BPM work

The initial audit interpreted the 25 July 88-BPM/+0.5 “on the one now” handoff
as current truth. Sanjeev stopped the Logic pass and identified the newer test
era. Saved Logic metadata then confirmed:

- `session player test.logicx` is the genuine historical 88-BPM project;
- `Session Player test 23.07.26.logicx` was later saved at 116 BPM;
- `Fusion Contract Baseline 2026-07-28.logicx` is 116 BPM;
- the final `codex session player test.logicx` pocket benchmark is 116 BPM.

The apparent drift was therefore session-ID reuse across two test eras, not
evidence that the later 116-BPM work had regressed. The fixtures are now split:

- historical 88-BPM/+0.5 take remains recoverable on session
  `92e7b255-8fba-43a3-8d8c-742d64a91778`;
- the exact pre-audit 116-BPM state was duplicated to isolated session
  `b415ee2b-1c78-4afe-b2dd-b6274789feea`;
- all three AUs are configured to that isolated 116-BPM session;
- the final 28 July benchmark was copied to
  `Session Player Audit 116 BPM 2026-07-30.logicx`, leaving the baseline
  untouched.

Recommended repair: give every named acceptance fixture its own immutable
session binding and receipt. Never reuse a production-bound session ID for a
new tempo, form, or research generation.

Repair completed on 30 July:

- added an irreversible acceptance-fixture seal with a canonical durable-state
  manifest and SHA-256 receipt;
- MIDI is represented by byte length and content digest rather than duplicated
  inside the manifest;
- sealed sessions remain readable by Logic and accept transient Bridge
  overlays, but settings, regeneration, history recall, and MIDI replacement
  cannot publish over the accepted state;
- duplication deliberately produces a new unsealed working session;
- corrupted or mismatched sealed snapshots fail closed during persistence
  restore;
- isolated session `b415ee2b-1c78-4afe-b2dd-b6274789feea` is sealed as
  `Session Player Audit 116 BPM 2026-07-30 - Logic accepted`;
- receipt:
  `f91848565379a78194ab907ab735daa83577dbb270b025512082dfb6872d9b95`;
- after backend restart the same receipt, 116 BPM, `+12` output register, and
  112-note Bass part were restored; a deliberate tempo mutation returned
  `409 acceptance_fixture_sealed`.

### Closed P1 — Accepted playback octave was not encoded in Session Player state

The isolated live AU emits notes 31–60. The approved fixed-bass benchmark raw
MIDI spans a comparable 33–58, but its Logic track is explicitly auditioned at
`+12`. Sanjeev heard the live part as one octave too low. A downstream Logic
MIDI FX Transposer at +12 corrected the register without changing the approved
MIDI, timing, or articulation.

Region/MIDI-default transpose did not work because Session Player Bass owns and
replaces the lane MIDI. The working signal order is:

`Session Player Bass -> Transposer +12 -> Bass instrument`

Repair completed on 30 July:

- added a persisted, octave-restricted
  `bass_output_transpose_semitones` session field;
- exposed it in the plugin Bass-part contract while leaving authored MIDI
  unchanged;
- Session Player Bass now applies the shift only at AU playback and includes
  it in playback identity, note-off handling, and its visible status line;
- the isolated 116-BPM session is durably set to `+12`;
- after restarting Logic and removing the separate Logic Transposer, Sanjeev
  confirmed the register matches the approved sound.

The project no longer depends on an undocumented downstream transpose.

### Closed P0 — Listener did non-real-time-safe work on Logic's audio callback

`SessionPlayerListenerAudioProcessor::processBlock()` calls
`maybeEmitBarFrame()` at a bar transition. That call performs:

- an 8192-point FFT;
- chroma binning and onset analysis;
- `juce::String` construction/copying;
- FIFO copies of a frame containing strings;
- a blocking `juce::CriticalSection` lock used by the editor.

Evidence:

- `audio-listener/Source/PluginProcessor.cpp:488`
- `audio-listener/Source/PluginProcessor.cpp:633`
- `audio-listener/Source/PluginProcessor.cpp:660`
- `audio-listener/Source/PluginProcessor.cpp:674`
- `audio-listener/Source/PluginProcessor.cpp:731`
- `audio-listener/Source/PluginProcessor.cpp:749`

`auval` validates correctness and format handling but does not prove deadline
safety. This design can produce a repeatable click/dropout at bar boundaries,
especially in a loaded Logic session.

Recommended repair: make the audio callback copy only bounded POD samples or a
preallocated analysis window into a lock-free SPSC queue. Run FFT, inference,
strings, UI publication, and HTTP work on a worker thread. Add a callback-time
benchmark with allocation/lock assertions.

Repair status, later on 30 July:

- implemented a four-slot, preallocated SPSC queue of fixed-size trivially
  copyable analysis jobs;
- the callback now performs only downmix/ring accumulation, fixed bounded
  copy at a bar boundary, and atomic/FIFO signalling;
- FFT, chroma, onset analysis, `juce::String` construction, UI locking, and
  bridge publication now run on a dedicated Listener analysis thread;
- stale jobs are rejected by transport state and capture epoch;
- a source-contract regression test fails if analysis, strings, locks, or
  publication return to `processBlock()` or its boundary handoff;
- Listener Release build and signing passed, installed binary matched the
  build, Apple `auval` passed, and the full backend suite reported 998 passed.

The installed repaired AU then passed the post-repair Logic audition without
an audible click, pop, dropout, or other regression. Fresh harmonic frames from
the Listener reached the isolated session and received HTTP 200 responses.
Together with the build, signing, `auval`, and source-contract results, this
closes the finding.

### Closed P1 — Backend lifecycle was manual

At audit start, the AU config pointed Bridge, Listener, and Bass at
`127.0.0.1:8001`, but nothing was listening. Port 8000 is occupied by the
separate AutoFactory process, so falling back to 8000 would be wrong.

The backend was initially started manually on port 8001 for the controlled
Logic pass.

Recommended repair: add one explicit Session Player service/bootstrap command
that binds loopback port 8001, verifies `/health`, verifies the configured
session exists, and refuses to collide with AutoFactory.

Repair completed on 30 July:

- installed per-user LaunchAgent
  `com.subone.session-player-backend`, with `RunAtLoad`, failed-process
  restart, throttling, and dedicated logs under
  `~/Library/Logs/Session Player/`;
- added `scripts/session_player_backend.sh` for install, start, stop, restart,
  status, health, and log access;
- startup preflight validates the AU loopback configuration, requires port
  8001 rather than AutoFactory's port 8000, restores and validates durable
  sessions including acceptance receipts, requires the configured session to
  exist, and refuses a port collision;
- the live health probe verifies the service, configured session, and Bass
  playback contract rather than treating a listening socket as sufficient;
- a forced process kill was recovered by launchd with a replacement process;
- a later managed restart returned healthy under launchd as run 3, PID 21052;
- live listener inspection confirmed AutoFactory remained on port 8000 while
  Session Player alone owned `127.0.0.1:8001`;
- the post-restart probe restored the exact sealed acceptance receipt, 116 BPM,
  112 Bass notes, and `+12` output transpose.

The lifecycle finding is closed for the current local-AU product shape.

### P1 — Forty-four commits are not on the remote

`HEAD` is 44 commits ahead of `origin/main` (41 on local `main`, plus the three
Fusion-branch commits). The worktree is clean, and machine backups reduce the
immediate loss risk, but the complete working product is not recoverable from
the repository remote.

Recommended repair: review the branch boundary, then push a named safety branch
before further Session Player development. Do not merge the three Fusion
commits until the timing and Logic acceptance findings above are resolved.

### P1 — Reference uploads leak files and enforce their limit after buffering

The reference-audio directory contains:

- 235 files total;
- 33 files referenced by persisted sessions;
- 202 orphan files;
- 982,052,546 orphan bytes (about 937 MiB).

Each replacement writes a new blob but never removes the superseded blob.
There is also no session deletion/garbage-collection path. Both upload handlers
call `await file.read()` before applying the 25 MiB check, so an oversized
request is fully buffered before rejection.

Evidence:

- `backend/app/routes/session_routes.py:1341`
- `backend/app/routes/session_routes.py:1344`
- `backend/app/routes/session_routes.py:1351`
- `backend/app/routes/session_routes.py:1465`
- `backend/app/routes/session_routes.py:1468`
- `backend/app/routes/session_routes.py:1475`

Recommended repair: stream uploads in bounded chunks, commit the new session
reference first, then retire the prior blob after durable persistence succeeds.
Add a dry-run GC that identifies files not referenced by sessions or recoverable
history before any deletion.

### P1 — Saved setups/evaluations can be silently destroyed on read

Unlike the session, candidate, and bass-history stores, `setup_store` and
`evaluation_store` overwrite invalid/unreadable JSON with an empty document.
One malformed write or transient read error therefore turns the next GET into
irreversible data loss. They also use one fixed `.json.tmp` filename and no
read-modify-write lock, so concurrent requests can collide or lose updates.

Evidence:

- `backend/app/services/setup_store.py:21`
- `backend/app/services/setup_store.py:29`
- `backend/app/services/setup_store.py:33`
- `backend/app/services/evaluation_store.py:15`
- `backend/app/services/evaluation_store.py:29`
- `backend/app/services/evaluation_store.py:33`

Recommended repair: use the same fail-closed, UUID-temp, quarantine, and
`RLock` pattern already implemented in `session_store`/`bass_history_store`.

### P2 — Bass AU can overwrite a failed action message immediately

The Bass polling thread records an HTTP rejection for Regenerate/Keep, then the
same loop treats the action as handled and calls `fetchPart()` with status
updates enabled. A valid old part can therefore replace the failure message
immediately, making a rejected producer action look successful/current.

Evidence:

- `audio-midifx/Source/PluginProcessor.cpp:575`
- `audio-midifx/Source/PluginProcessor.cpp:627`
- `audio-midifx/Source/PluginProcessor.cpp:630`

Recommended repair: preserve action-result status through the subsequent
part refresh, as the command path already does with `fetchPart(false)`. Add a
native test for 409/422/503 responses.

### P2 — Frontend development dependencies have known advisories

`npm audit` reports four frontend development-tool vulnerabilities:

- 2 high (`vite`, `postcss`);
- 1 moderate (`esbuild`);
- 1 low (`@babel/core`).

The desktop dependency tree reports zero. These are build/dev-server
dependencies rather than code shipped in the static bundle, which reduces
production impact, but Vite's dev server is part of the daily workflow and the
desktop target includes Windows.

Recommended repair: upgrade Vite/plugin-react on a dedicated branch, rebuild,
rerun the full suite, and re-audit. Do not apply a blind major-version audit
fix on the accepted branch.

### P2 — Desktop packaging is a wrapper, not a standalone product

The Tauri app bundles the frontend but has no backend sidecar/lifecycle code;
production still depends on a separately started FastAPI server. Its CSP is
also explicitly disabled (`desktop/src-tauri/tauri.conf.json:19`).

Recommended repair: either label the desktop target developer-only or bundle a
loopback-only backend sidecar with health/lifecycle ownership and a restrictive
CSP.

## Verification performed

- Backend: **1,012 passed**, 4 existing librosa warnings.
- Research: **29 passed**.
- Frontend: Vite production build passed.
- Native source builds:
  - Session Player Bass AU passed.
  - Session Player Listener AU passed.
  - Session Player Bridge AU passed.
- Installed artifacts match the newly built component directories.
- Apple `auval` passed all three installed AUs.
- Python bytecode compilation passed.
- `pip check` passed.
- Current stores validated:
  - 32 sessions (including the isolated 116-BPM audit copy);
  - 527 candidate runs / 2,096 takes;
  - 38 bass-history snapshots.
- Managed backend verification:
  - LaunchAgent installed, loaded, and running;
  - forced-kill recovery and managed restart passed;
  - preflight collision/config/session checks passed;
  - live probe recovered the sealed fixture receipt, 116 BPM, 112 Bass notes,
    and `+12` output transpose;
  - port inspection confirmed Session Player on loopback 8001 and AutoFactory
    separately on 8000.
- Candidate store read benchmark: 16.69 ms mean, 21.94 ms max at 3.6 MiB.
- Fusion property pass: 8,000 build/serialize/restore round trips across
  1, 2, 3, 4, 7, 16, 31, and 128 bars.
- `git diff --check` and repository object validation passed.
- Controlled Logic acceptance:
  - both fixed-bass pocket-pass sections judged better than their “before”
    sections;
  - live 116-BPM pocket judged good;
  - downstream +12 transpose judged the correct register;
  - after the persisted-register repair, the same accepted register was heard
    with the separate Logic Transposer removed;
  - string articulation audible from the expressive event stream;
  - three mid-note transport stops produced immediate silence/no hang;
  - Bridge connected and reported 97 frames;
  - Listener pre-repair pass from bars 1–17 produced no audible click, pop, or
    dropout;
  - the installed repaired Listener also passed the Logic retest, while fresh
    harmonic frames reached the isolated backend session with HTTP 200
    responses.

## Coverage gaps

- No frontend test script exists; only production compilation covers React.
- Native behavior relies on compile-time assertions, `auval`, and manual Logic
  audition. There is no automated process-block timing/allocation harness.
- The hands-on Logic pass covered a controlled light-load project. It did not
  stress Listener under a production-size mix or prove callback deadlines.

## Recommended order

1. Review the branch boundary and push a named safety branch.
2. Repair upload lifecycle and safely reclaim confirmed orphan audio.
3. Harden setup/evaluation stores.
4. Upgrade frontend tooling and add UI/native regression harnesses.
5. Add a production-load Listener timing/allocation stress harness.
