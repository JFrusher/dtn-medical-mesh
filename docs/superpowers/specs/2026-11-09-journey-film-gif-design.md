# Journey film GIF: design

Date: 2026-11-09. Status: approved, with two cuts to compare.

## Intent

A 30-second GIF that follows one clinical message from the clinician's keyboard to the hospital and back. It replaces the hero GIF at the top of the README.

The viewer is a recruiter or a generalist engineer. They should come away remembering how well it was made, what Satchel is for, and what the code can do. They do not know what Reed-Solomon is, and should not need to.

## Goals

- Show data entry, encryption, relay between vehicles, an attack being refused, reconstruction at the hospital, and the confirmation returning.
- Everything presented as a system event comes from a real emulator run: message text, ciphertext, vehicle positions, handovers, refused frames, piece counts, timeline.
- A viewer who reads only the large captions understands the story.

## Non-goals

- Audio, or an MP4 version.
- Changes to the app's UI, node logic or the five published scenarios.
- Showing any capability that is not implemented.

## Anti-goals

The film has failed if any of these appear, however well it plays.

**Visual:** gradients, glow, bloom, particles, lens flare, fake film grain, text scramble or decode effects, shatter effects, floating rounded tiles, glass panels, stock icons, spaced monospace capitals, one slow zoom reused on every shot.

**Copy:** two-beat taglines ("X failed. Y was enough."), "not X but Y", groups of three, em dashes, uplifting closers, invented numbers.

**Claims:** anything implying emergency use, a real deployment, a real patient, or a link to a real facility.

## Decisions made by Jacob

| Topic | Decision |
|---|---|
| Footage | Hybrid: real app screens plus added scenes showing what the app does not display, driven by real data |
| Length | About 30 seconds |
| Arc | The message survives adversity |
| Placement | Takes the hero position in the README. The current map GIF moves to "See it work" |
| Adversity | A GIF-only run on Arusha's real roads with a forging node and lossy radio |
| Mood | Human documentary |
| Colour | Warm paper: the app's own paper colour tokens set to warm values for the film. A CSS filter washed the map out |
| Captions | Large plain sentence, small technical note under it |
| Encryption shot | Hard cut from readable referral to its real ciphertext as hex, ruled into 12 numbered rows |
| Route | Relay chain: clinic to street light to motorbike to ambulance to hospital. The originally planned van leg never happened in a real run |
| Staging | The schedule in the GIF-only scenario is arranged (see Staged schedule). The end card stays "Emulated, not deployed. Not for emergencies." |
| Screens | Full-screen cuts, split-screen only at sending and arrival |
| Opening | Render two cuts, identical after 3 s: one opens on the real map, one on a line drawing. Jacob chooses after watching both |
| Last frame | Jacob's name and contact |

## Shot list

Timings are approximate.

| Time | Shot | Layout | Source |
|---|---|---|---|
| 0 to 3 s | Map cut: real Arusha map with Murieti marked. Drawing cut: clinic desk with laptop wired to the site node. Both captioned "A clinic outside Arusha has no signal." | Full | Real app, or drawn |
| 3 to 8 s | Clinician types a referral, with a correction and a pause, sets Critical, presses Send | Clinic chat beside map | Real app |
| 8 to 11 s | Hard cut to the ciphertext, ruled into 12 numbered rows. Caption: "Sealed for the hospital and cut into 12 pieces." Note: "Ed25519, X25519, XChaCha20-Poly1305" | Full | Real bytes |
| 11 to 18 s | Map follows the pieces from motorbike to van to ambulance. The 12-slot counter is on screen. Caption: "Vehicles already on the road carry the pieces without being able to read them." | Full | Real app |
| 18 to 21 s | The forging node is marked on the map and its pieces are refused. Caption and count use the real refused figure | Full | Real app |
| 21 to 25 s | Counter reaches 4 and the hospital chat opens the referral. Caption: "The hospital needs any 4 of the 12 to open it." Note: "Reed-Solomon erasure coding" | Map beside hospital chat | Real app |
| 25 to 28 s | Journey view: route and timeline, ending with the confirmation reaching the clinic. Caption: "A signed confirmation comes back to the clinic." | Full | Real app |
| 28 to 31 s | Satchel, Jacob Frusher, jacob@frusher.co.uk, GitHub repo. Small note: "Emulated, not deployed. Not for emergencies." | Full | Card |

Captions are drafts. Numbers in them must match the final run.

## Staged schedule

The events are real and the timetable is not. `write_film_scenario` in `tools/film.py` makes the relay the only physical path. The motorbike only rides between a street light beside Murieti and a meeting point, the ambulance only drives between that point and the hospital, other vehicles and background traffic are removed, and random departure delays are off. A forging carrier loads an older message at Kisongo, then parks on the ambulance's road. Encryption, handovers, refusals, reconstruction and the confirmation all run in the emulator unchanged. The published scenarios and results do not use this schedule.

## Visual system

- Typography follows the app's font stack. Captions sit left-aligned on a solid paper panel with a thin ink border, placed lower left. The panel has square corners and no shadow.
- App colours only: teal `#0f6e74` for the network, alarm red `#b4232c` for threats, paper and ink for everything else.
- Piece counter: 12 square numbered slots. Teal means arrived, outline means not arrived, red struck through means refused. A slot changes only on a real event.
- Camera moves differ by shot and have a reason: follow the vehicle, or push in on a handover. Between shots the camera pans for real across the map, dissolves are short, and the row numbers of the ciphertext become the piece counter.
- The drawing is plain ink on warm paper with no people. It shows a desk, a laptop wired to a small node box, patient files, and a window with a parked motorbike. Builder's call: the desk version was chosen over the exterior because it shows the real setup and cuts straight into typing.
- Both cuts render to separate review files. Only the chosen cut stays in `docs/media`, and the README points to it.
- The app's "Not for emergencies" banner stays visible in app shots.

## Technical constraints

- Extend `tools/record_gifs.py` with one more job. It already serves the live emulator, and `window.render` exists to draw exact frames.
- Added scenes are HTML overlays in the same Playwright session. No video framework.
- gifski encodes the frames and is installed outside the repo, with approval, when the build starts. The film job does not need ffmpeg.
- Target width 960 px and at most 15 MB. The size is an estimate to test against the first render; current GIFs are about 3 MB.
- The attack scenario should be derived from `arusha.yaml` at record time if practical, so the repo does not gain a second 2,600-line scenario and the "5 scenarios" results stay accurate.
- `forge_shards` in `simulation/hostile.py` and the radio loss settings used in `stress.yaml` already exist.
- A Critical message is 4 + 8 = 12 pieces (`node/config.py`).

## Risks to resolve while building

- Routing copies pieces to any node met early in a run, so a clean three-vehicle chain is not guaranteed. Choose the seed, send time and start position to get it, and show what the run did. If no run gives the full chain, stop and ask.
- Custody receipts may give piece counts without piece numbers. If so, get numbers from node state or events. If a forged piece cannot be matched to a slot, show the refused count on its own.
- Playwright's screen recording is low quality and timing jitters. Prefer stepping the simulation and capturing still frames.
- The README's alt text, and any README checks in `validation/test_docs.py`, must stay correct after the swap.

## Left to the builder

Seed and timing, exact frame rate, palette and compression settings, camera paths, how the existing hero GIF is repositioned in "See it work", and the final caption wording within the rules above.
