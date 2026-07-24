# Session Player Morning Bootstrap — Saturday 25 July 2026

Prepared: Friday 24 July 2026, overnight
Owner: Sanjeev Sharma
Repo: `/Users/sub/Work/Code/Projects/super-band-session-player`

## Scope

Session Player is currently Sanjeev's private studio tool. This work adds
recoverable musical exploration inside Logic. It does not add distribution,
licensing, pricing, public testing or any commercial surface.

## Verified repository and services

- Branch: `main`
- Local branch was 34 commits ahead of `origin/main` before the overnight
  commit; never push.
- Worktree was clean before the overnight slice.
- Frontend remained healthy at `http://127.0.0.1:5173`.
- The stale Session Player backend was restarted on
  `http://127.0.0.1:8000`; AutoFactory's separate `0.0.0.0:8000` process was
  not touched.
- Bass AU Release build: passed and copied to
  `~/Library/Audio/Plug-Ins/Components/Session Player Bass.component`.
- Apple validation: `auval -v aumi SpMx SOne` passed.
- Backend: **507 tests passed**.
- Frontend production build: passed.
- Nothing was pushed.

## What was built

The Bass AU now makes exploration recoverable:

1. **Generate idea** and every recognised text command automatically preserve
   the exact current clean/performance MIDI first.
2. **KEEP** pins the current idea.
3. **Earlier** and **Later** restore exact MIDI and its control state.
4. The AU reports idea position and kept count.
5. Identical keeps are deduplicated.
6. History is bound to the session and its tempo, key, scale, chord map, source
   and loop length. It fails closed rather than putting an old idea over a new
   harmony context.
7. Kept ideas remain; unkept exploration retains the latest 48 ideas per
   session.

Runtime history lives locally in `backend/data/bass_history.json` and is
gitignored.

## Important live-session truth

The controlled combined-source session remains:

`92e7b255-8fba-43a3-8d8c-742d64a91778`

The backend still reports:

- 88 BPM
- 16 bars
- D natural minor
- Phrase Engine v2

However, the current live part was changed during later experimentation and is
now reported as:

- rhythmic
- Sub / Synth
- Groove Lock 1.00
- Character 1.00
- 49 performance notes

Do not mistake this later experiment for the earlier accepted supportive,
fingered, Groove Lock 0.70 part.

## First morning test — five minutes in Logic

1. Reinsert **Session Player Bass** in Logic, or restart Logic if it still
   displays the cached old editor.
2. Confirm the new row appears: **Earlier · KEEP · Later**.
3. Confirm the current plugin controls hydrate from the live part rather than
   reverting to defaults.
4. Press **KEEP**.
   - Expected: `Idea 1/1 | 1 kept | KEPT`.
5. Change one obvious control, then press **Generate idea**.
   - Expected: the kept idea remains available and **Earlier** enables.
6. Play the new idea briefly, then press **Earlier**.
   - Expected: the exact kept musical part returns and the controls return with
     it.
7. Press **Later**.
   - Expected: the newer idea returns exactly.
8. Close and reopen the plugin editor.
   - Expected: the history summary and navigation remain.

Pass only if Sanjeev can hear that the two recalled ideas are genuinely the
same performances as before. API equality and MIDI byte equality already pass;
the Logic/instrument ear-test is the remaining product proof.

## If the test passes

The next bounded slice is to make the advisor's **Accompany**,
**Counterpoint** and **Explore** paths actionable through this recovery layer.
Do not add more style controls first.

## If the test fails

Stop route work. Record whether the failure is:

- wrong notes returned;
- right notes but wrong control state;
- stuck notes at the recall boundary;
- history lost after editor/project reopen;
- cached old AU UI.

Fix the recovery fault before adding exploration.
