# Session Player Audit — 2026-07-30

Repo: `/Users/sub/Work/Code/Projects/super-band-session-player`
Branch: `codex/session-player-audit-safety-20260730`
Audit baseline: `75e9c7869aacdea550841bdd061dd471b6349861`
Repairs and verification: current through the branch head on 30 July 2026.

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
synthetic native load test is not a substitute for a production-size Logic
project and desktop packaging remains developer-oriented. The Listener
callback, frontend's highest-value control paths, and Bass action-status policy
now have executable regression coverage.

Product code was changed to repair the Listener callback boundary, persist the
accepted Bass output register, seal the accepted fixture, own the backend
lifecycle, make reference-audio storage transactional, harden auxiliary JSON
stores, and add regression contracts for those boundaries. Live state was
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
- a Release-mode CTest now drives the actual processor for 18 bars at
  48 kHz/128 samples under a preallocated 64-track, four-stage workload;
- 12,414 measured callbacks and 16 bar-boundary handoffs completed with zero
  callback-thread C++ heap allocations;
- across ten consecutive passes, each 2.67 ms block deadline held; the
  representative verbose pass measured callback p99 at 0.458 microseconds,
  callback p99.9 at 0.666 microseconds, boundary max at 0.958 microseconds,
  64-track host-cycle p99 at 32.750 microseconds, and host-cycle max at
  36.458 microseconds;
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

### Closed P1 — Local history lacked a remote recovery point

At audit start, `HEAD` was 44 commits ahead of `origin/main` (41 on local
`main`, plus the three Fusion-branch commits). Machine backups reduced the
immediate loss risk, but the complete working product was not recoverable from
the repository remote.

Recommended repair: review the branch boundary, then push a named safety branch
before further Session Player development. Do not merge the three Fusion
commits until the timing and Logic acceptance findings above are resolved.

Repair completed on 30 July:

- reviewed the complete tracked and untracked worktree boundary;
- excluded generated binaries, Logic projects, and durable session data;
- created and pushed
  `codex/session-player-audit-safety-20260730`;
- GitHub directly reported repair commit
  `b1a76d30112aab3798632636618e234b206d20a1` at that remote ref;
- the safety branch contains the 44 pre-existing local commits plus the
  verified audit repair commit; `origin/main` remains unchanged and nothing
  was merged.

The accepted runtime is now recoverable from the named remote branch.

### Closed P1 — Reference uploads leaked files and buffered before limiting

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

Repair completed on 30 July:

- both upload lanes now stream in 1 MiB chunks and stop at a 25 MiB bound
  without an unbounded `read()`;
- uploads land through a unique temporary file, flush and `fsync`, then publish
  to a unique final blob with an atomic rename;
- session middleware retires a superseded blob only after the replacement
  reference has been durably committed;
- persistence failure, handler failure, and rejected sealed-session mutation
  roll back the session and discard the uncommitted new blob;
- retirement checks the complete live session map and retained Bass history,
  so a duplicate session or recoverable idea keeps its source audio;
- added a two-phase GC whose dry run records exact relative paths, sizes,
  modification times, and SHA-256 hashes; apply requires the exact plan digest
  and rechecks file identity and all current/history references;
- GC refuses symlinks, non-regular entries, changed files, newly referenced
  files, and missing protected files;
- the live dry run reproduced the audit exactly: 202 orphans,
  982,052,546 bytes, 33 protected paths, and zero unsafe entries;
- applied plan
  `eb97ab8fba2804a1a7122c2aa9a78e6a1358f2b2eca25f60b80e6e03f154b36c`;
- the post-clean dry run found zero orphans, 33 protected files, zero missing
  protected files, and zero unsafe entries;
- the reference-audio directory now occupies 194,048 KiB instead of about
  1.1 GiB. The 202 deleted blobs are not locally recoverable; the applied
  receipt preserves their exact paths, sizes, timestamps, and content hashes
  under `~/Library/Application Support/Session Player/GC Receipts/`.

The upload lifecycle and known orphan backlog are closed.

### Closed P1 — Saved setups/evaluations could be silently destroyed on read

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

Repair completed on 30 July:

- both stores now validate the complete document and every record; invalid
  JSON, non-standard constants, unsupported schemas, and invalid rows fail the
  whole read instead of returning a partial or empty result;
- corrupt or unreadable originals are moved intact to uniquely named
  quarantine files;
- the presence of a quarantine file creates a recovery barrier, so a later
  read cannot silently initialize an empty replacement;
- writes serialize strictly, flush and `fsync`, use a UUID-specific temporary
  file, and atomically replace the canonical document while preserving the
  prior file on failure;
- setup create/delete and evaluation note/take routes now hold one `RLock`
  across the complete read-modify-write transaction;
- store recovery failures return a structured HTTP 503 with `Retry-After`
  rather than presenting empty data;
- concurrent duplicate setup requests produce exactly one create and one
  conflict, while 16 simultaneous unique take writes are all retained;
- the generated local evaluation file is now ignored by Git.

The data-loss and concurrent-update finding is closed.

### Closed P2 — Bass AU could overwrite a failed action message immediately

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

Repair completed on 30 July:

- the polling loop now tracks whether the latest producer action failed and
  passes that decision into the mandatory part refresh;
- rejected Regenerate/Keep responses and offline failures refresh playable MIDI
  silently, leaving the actual engine message visible;
- successful producer actions still publish the fresh part's normal musical
  status;
- later actions in the same polling turn retain last-action semantics;
- rejected Earlier/Later navigation receives the same protection;
- the command path keeps its existing explicit silent refresh and deliberate
  three-second response hold;
- native compile-time assertions cover HTTP 409, 422, 503, offline, and 200,
  while a source contract prevents an unconditional status-updating
  `fetchPart()` from returning to the action loop;
- the Bass AU Release build, ad-hoc signature, installed-binary digest, and
  Apple `auval` all passed.

The stale-success status overwrite is closed.

### Closed P2 — Frontend development dependency advisories

The audit initially found four frontend development-tool vulnerabilities:

- 2 high (`vite`, `postcss`);
- 1 moderate (`esbuild`);
- 1 low (`@babel/core`).

These were removed by upgrading Vite 5 to 8.2.0 and plugin-react 4 to 6.0.5 on
the safety branch. React remains on 18.3.1, avoiding an unrelated React-major
migration. The Vite development proxy and desktop build now target the owned
Session Player backend on port 8001 rather than AutoFactory's port 8000.

Post-repair proof:

- `npm audit`: zero vulnerabilities;
- Vite 8.2.0 production build passed;
- Vitest 4.1.10 with React Testing Library and jsdom: 8 tests passed;
- Fusion preset/lock behavior, saved-setup guards, reference-audio
  success/failure behavior, and structured API recovery errors are covered;
- the desktop dependency tree remains at zero vulnerabilities.

### P2 — Desktop packaging is a wrapper, not a standalone product

The Tauri app bundles the frontend but has no backend sidecar/lifecycle code;
production still depends on a separately started FastAPI server. Its CSP is
also explicitly disabled (`desktop/src-tauri/tauri.conf.json:19`).

Recommended repair: either label the desktop target developer-only or bundle a
loopback-only backend sidecar with health/lifecycle ownership and a restrictive
CSP.

## Verification performed

- Backend: **1,033 passed**, 4 existing librosa warnings.
- Research: **29 passed**.
- Frontend:
  - Vite 8.2.0 production build passed;
  - 8 Vitest/React Testing Library tests passed;
  - `npm audit` reports zero vulnerabilities;
  - the installed dependency tree is valid.
- Native source builds:
  - Session Player Bass AU passed.
  - Session Player Listener AU passed.
  - Session Player Bridge AU passed.
- Listener real-time stress:
  - actual Release processor exercised at 48 kHz/128 samples for 18 bars;
  - 64 simulated tracks with four processing stages each;
  - 12,414 measured callbacks and 16 measured bar boundaries per run;
  - zero callback-thread C++ heap allocations;
  - callback p99/p99.9 and boundary maximum stayed below 10%/20%/25% of
    the 2.67 ms block deadline;
  - the complete synthetic host cycle stayed below its deadline;
  - ten consecutive CTest passes completed;
  - rebuilt/installed Listener SHA-256:
    `bfc1ccdf8c163a4998b820282528b27b54ba9f12e99062afa25b4866c1b1f688`;
  - strict signature verification and Apple `auval` passed.
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
- Reference-audio lifecycle:
  - bounded streaming, empty/oversize cleanup, persistence ordering, rollback,
    recoverable-history protection, and hashed-GC refusal tests passed;
  - exact 202-file dry-run plan applied, reclaiming 982,052,546 bytes;
  - post-clean inventory: 33 protected files, zero missing, zero orphaned, zero
    unsafe;
  - backend restarted and restored the sealed 116-BPM acceptance fixture.
- Auxiliary JSON stores:
  - corruption, invalid-record, strict-JSON, transient-read, atomic-write
    failure, quarantine-barrier, and structured-503 tests passed;
  - concurrent duplicate setup creation and 16-way evaluation writes passed
    without lost updates.
- Bass action status:
  - native 409/422/503/offline/success policy assertions compiled;
  - the standalone seven-case C++ policy executable passed through CTest;
  - source contract confirms failed producer actions use a silent part refresh;
  - rebuilt and installed Bass AU binary SHA-256:
    `67c270ce8c76a09480be15ecc779191fa45a934d4db85546746c765ed32a3696`;
  - strict code-signature verification and Apple `auval` passed.
- Candidate store read benchmark: 16.69 ms mean, 21.94 ms max at 3.6 MiB.
- Fusion property pass: 8,000 build/serialize/restore round trips across
  1, 2, 3, 4, 7, 16, 31, and 128 bars.
- `git diff --check` and repository object validation passed.
- Remote safety recovery:
  - branch `codex/session-player-audit-safety-20260730` pushed to GitHub;
  - remote repair commit verified as
    `b1a76d30112aab3798632636618e234b206d20a1`;
  - no merge into `origin/main`.
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

- Frontend component coverage now protects the highest-risk control paths, but
  there is no full browser end-to-end suite.
- Native action-status and Listener callback timing/allocation now have
  executable CTests.
- The Listener load test is deterministic and synthetic. The hands-on Logic
  pass still covered a controlled light-load project rather than a full
  production session with third-party instruments and effects.

## Recommended order

1. Add a browser end-to-end smoke test around backend/session creation.
2. Repeat the Listener audition inside a real production-size Logic mix.
