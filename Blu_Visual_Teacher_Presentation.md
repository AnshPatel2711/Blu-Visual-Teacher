# Blu Visual Teacher — Presentation (Slide Outline)

Target: ~9 slides, ~5 minutes.

---

## Slide 1 — Title
**Blu Visual Teacher**
Local, offline-first visual lesson generator
- Enter a topic → get a narrated, animated MP4 lesson
- Built for hackathon demo

## Slide 2 — Problem
- Learners struggle with abstract programming concepts (e.g. if–else).
- Static text/code is hard to follow; video lessons are expensive to produce.
- Cloud tools raise privacy/offline concerns.
- Need: fast, local, visual + narrated lessons from a single topic.

## Slide 3 — Proposed solution
- Topic in → validated lesson plan → narration → rendered frames → MP4.
- Everything runs locally: Ollama (planning), Kokoro (voice), FFmpeg (export).
- Browser UI: generate, preview, download.

## Slide 4 — Key features
- Local LLM lesson planning with JSON-schema validation.
- Topic-aware visuals: diagrams, code panels, captions, progress bar.
- Natural narration (Kokoro af_heart, Zira fallback).
- 1280×720 H.264/AAC MP4 with fast-start + range requests.
- Offline fallback for Python conditional topics; clear errors otherwise.
- No cloud, no new dependencies.

## Slide 5 — Target users
- Beginner programming learners.
- Educators/mentors needing quick visual explainers.
- Offline/low-connectivity environments.
- Hackathon judges evaluating local AI tooling.

## Slide 6 — System architecture
- Frontend: `web/index.html` (form, `<video>`, download link).
- Backend: `src/build_lesson.py` (ThreadingHTTPServer, 127.0.0.1:8000).
- API: `/api/generate`, `/api/preview/{id}`, `/api/export/{id}`.
- Pipeline: planning → voice → render → ffmpeg → in-memory preview registry.
- Assets: `../kokoro/` models; FFmpeg on PATH.

## Slide 7 — Lesson-planning workflow (Ollama/Gemma)
- `detect_lesson_type(topic)` → loop / conditional / concept.
- `generate_lesson_plan(topic)` builds prompt + JSON schema, POSTs to Ollama (`gemma3:4b`, temp 0).
- `normalize_text` cleans output; `validate_lesson_plan` enforces fields, scene counts, topic match.
- Concept topics get up to 2 attempts; conditional/loop get 1.
- Offline fallback: conditional topics only; others return a clear error.

## Slide 8 — Validation, narration, rendering, export
- Validation: schema (types, lengths, kinds), example fields, topic-keyword match.
- Narration: Kokoro `af_heart` (24 kHz WAV) → Zira/SAPI fallback; clips padded to ≥30s total.
- Rendering: Pillow frames at 12 fps; captions (≤3 lines), progress bar; scene boundaries follow voice durations.
- Export: FFmpeg libx264/yuv420p + AAC, faststart; served with range requests.

## Slide 9 — Verified test results
- `lesson.json` JSON parse: PASS (5 scenes).
- `py_compile`: PASS.
- Module import: PASS.
- Ollama reachable; `gemma3:4b` installed: PASS.
- Lesson planning + validation: PASS (5 scenes).
- Full build: PASS (48.53s, 586 frames, MP4 825,203 bytes).
- MP4 probe: PASS (H.264 1280×720, AAC, 48.533s).
- HTTP E2E (generate/preview/export): PASS (all 200).
- Offline fallback tests: PASS (conditional fallback; non-conditional/loop raise clear errors).
- Browser playback in real browser: NOT TESTED (HTTP-level only).

## Slide 10 — Limitations and future work
- Arbitrary topics need local Ollama; only Python conditionals have offline fallback.
- Multilingual: English (Kokoro af_heart/Zira), Hindi, Bengali, Tamil (eSpeak NG via ctypes). Nirmala.ttc for Indic scripts. Hindi MP4 verified (67.6s). Bengali/Tamil plans validated; full video pending.
- Browser console and real playback not verified (`/health` route missing).
- In-memory preview registry; stale `web/lesson.mp4` artifact.
- Future: more offline fallbacks, multilingual narration, queue/concurrency, tests.

## Slide 11 — Live demo plan (~2 min)
1. Start `.\start.ps1`, open http://127.0.0.1:8000.
2. Enter "Python if-else conditional statements", Generate.
3. Show loading, preview playback, download link.
4. Download + play MP4; confirm via ffprobe.
5. Q&A on architecture and validation.

---

Visuals: use `web/poster.svg`, a frame from `work/` (if available), or the generated MP4 thumbnail. No new assets created.
