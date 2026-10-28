# Blu Visual Teacher — Technical Handover Guide

## 1. Project purpose and current capabilities

Blu Visual Teacher is a **local, offline-first visual lesson generator**. A user enters a topic (e.g. "Python if-else conditional statements"); the application plans a short lesson, synthesizes English narration, renders synchronized visuals and captions, and exports a **1280×720 H.264/AAC MP4** that plays in the browser with a download link.

Current capabilities (verified):
- Local lesson planning via Ollama (`gemma3:4b`) with a JSON-schema contract and validation.
- Local narration via Kokoro (`af_heart`), falling back to Windows SAPI/Zira.
- Visual rendering with Pillow (diagrams, code panels, captions, progress bar) at 12 fps.
- MP4 export via FFmpeg with fast-start and range-request serving (preview + download).
- A browser UI (no build step) that submits a topic, shows a loading state, plays the preview, and enables download.
- Offline fallback for **Python conditional** topics when Ollama is unavailable (fixed if–else lesson); a clear error for other topics.

Not claimed (not verified): fully offline operation for arbitrary topics, multilingual/Indian-language support, browser playback in a real browser engine (HTTP-level only verified), and the `/health` route (not implemented).

## 2. Repository structure

```
Blu-Visual-Teacher/
├── README.md
├── lesson.json               # Fallback lesson data (Python if–else), must be valid JSON
├── requirements.txt          # Pillow, kokoro-onnx, soundfile
├── start.ps1                 # Entry point: runs build_lesson.py --serve
├── src/
│   ├── build_lesson.py       # Core module: planning, validation, rendering, HTTP server
│   ├── generate_voice.py     # Kokoro (primary) -> Zira/SAPI (fallback) narration
│   └── generate_voice.ps1    # Windows SAPI/Zira fallback script
├── web/
│   ├── index.html            # Topic form, <video> player, download link
│   ├── style.css
│   ├── poster.svg
│   └── lesson.mp4            # Stale artifact from an older run
├── work/                     # Generated artifacts (previews, clips, frames, logs)
├── checks/
│   └── video-loading-check.txt
├── PROJECT_GUIDE.md          # This file
└── Blu_Visual_Teacher_Presentation.md  # Slide outline
```

## 3. Important files and roles

- `src/build_lesson.py`
  - Constants: `OLLAMA_MODEL = "gemma3:4b"`, `OLLAMA_URL`, `W/H/FPS`.
  - `detect_lesson_type(topic)` — regex classifier: loop / conditional / concept.
  - `validate_lesson_plan(candidate, ...)` — schema + topic-match validation.
  - `generate_lesson_plan(topic)` — builds prompt, calls Ollama, normalizes, validates.
  - `load_lesson_plan(topic)` — generation with conditional-topic offline fallback.
  - `build_lesson(topic=None)` — orchestrates voice, rendering, ffmpeg, preview registration.
  - `render_frame(...)` / `draw_*` — Pillow frame generation.
  - `LessonRequestHandler` — HTTP API: `/api/generate`, `/api/preview/{id}`, `/api/export/{id}`.
  - `run_server()` — ThreadingHTTPServer on 127.0.0.1:8000.
- `src/generate_voice.py` — Kokoro primary, SAPI/Zira fallback.
- `src/generate_voice.ps1` — SAPI/Zira implementation.
- `lesson.json` — fallback lesson; must remain valid JSON (fixed).
- `web/index.html` — frontend workflow and preview/download handling.

## 4. Technologies and dependencies

- Python 3.14 (verified), Pillow>=10, kokoro-onnx>=0.4.7, soundfile>=0.14.
- FFmpeg on PATH (verified present).
- Ollama with `gemma3:4b` (verified installed and runnable).
- Kokoro models in sibling `../kokoro/`: `kokoro-v1.0.onnx`, `voices-v1.0.bin`.
- Windows SAPI voice (Zira) as fallback.
- No cloud APIs or new dependencies.

## 5. Installation and run (Windows)

```powershell
# From project folder
python -m pip install -r requirements.txt
# Ensure ffmpeg is on PATH
# Ensure Ollama is installed and `gemma3:4b` pulled
ollama pull gemma3:4b

# Start
.\start.ps1
# Opens http://127.0.0.1:8000
```

Rebuild without server: `python src\build_lesson.py`.


## 12. Performance measurements (baseline)

Instrumented via stderr TIMING lines in `build_lesson` (no UI change).

Representative run: "Python if-else conditional statements", Beginner, 60s target, English:
- Planning (Ollama): ~6.1s
- TTS (Kokoro): part of ~43s total pipeline
- Full pipeline (planning + voice + render + ffmpeg): ~43-60s
- Rendered frames: 586-821 depending on duration
- Output MP4: ~825KB-1.12MB

Optimizations applied: in-memory font cache (`_FONT_CACHE`), FFmpeg `-preset fast`. No quality reduction (resolution, narration, content preserved). Timings are environment-specific (CPU-only, gemma3:4b).


## 11. Feature status (after Priority 1–3 work)

Completed:
- Frontend controls: study level (Auto/Beginner/Intermediate/Advanced), duration (30/45/60/90 s), language (English only), optional visual preferences (cards/sequence/compare/cycle) — all wired to `/api/generate`.
- Backend accepts and validates `level`, `duration`, `language`, `visual_preferences`; integrates level and visual preferences into the planning prompt; duration drives pause padding (30–90 s).
- Offline fallback for Python conditional topics; clear error otherwise (verified).
- Adaptive planning: prompt now requires a teaching plan (objective, starting point, key concept, example, order, visuals, takeaway) before scene generation; narration targets 2-4 teaching sentences; level-specific guidance for Auto/Beginner/Intermediate/Advanced.
- Captions are limited to three lines and may truncate long narration with an ellipsis; the full narration is always spoken in the audio.

Partial:
- Multilingual: English uses Kokoro `af_heart` with Zira fallback. Hindi, Bengali, and Tamil use eSpeak NG 1.52.0 via ctypes (output mode RETRIEVAL) with voices `hi`, `bn`, `ta`. Nirmala.ttc font renders Indic scripts. Hindi full MP4 verified (H.264 1280×720, AAC, 67.6s). Bengali and Tamil lesson plans generated and validated; full video tests pending.

Unverified:
- Browser playback in a real browser engine (HTTP-level only).
- Real effect of study level/visual preferences on model output (prompt-level; not A/B tested).
- `/health` route not implemented.

No application code regressions introduced; `py_compile` and smoke test (level/duration/visual prefs) passed.

## 6. How a topic becomes a lesson and MP4

1. UI POSTs topic to `/api/generate`.
2. `build_lesson(topic)` -> `load_lesson_plan` -> `detect_lesson_type` -> `generate_lesson_plan` (Ollama, JSON schema) -> `validate_lesson_plan`.
3. If Ollama fails and topic is a Python conditional, `FALLBACK_LESSON` is used; otherwise a clear error is returned.
4. `generate_voice.py` writes `voice_XX.wav` (Kokoro or Zira).
5. Frames rendered per scene via `render_frame`; clips concatenated with silence gaps.
6. FFmpeg encodes 1280x720 H.264/AAC with fast-start.
7. MP4 moved to `work/lesson-preview-{id}.mp4`, registered in-memory; URLs returned.
8. UI sets `<video src>`, waits for metadata (15s timeout), enables download link.

## 7. Tests completed and evidence

- `lesson.json` JSON parse: PASS (`scenes=5`).
- `py_compile src/build_lesson.py`: PASS (exit 0).
- Module import: PASS (`import ok`).
- Ollama reachability: PASS (reachable; `gemma3:4b` installed).
- Lesson planning (`Python if-else conditional statements`): PASS (PLAN_OK, 5 scenes, VALIDATION_OK).
- Validation: PASS.
- Full build pipeline: PASS (BUILD_OK, 48.53s, 586 frames, MP4 825,203 bytes).
- MP4 probe (ffprobe): PASS (H.264 1280x720, AAC, 48.533s).
- HTTP E2E (`/api/generate`, `/api/preview`, `/api/export`): PASS (all 200, correct content-type and disposition).
- Offline fallback tests: PASS (conditional fallback used; non-conditional/loop raise clear error).
- Browser playback in a real browser engine: NOT TESTED (HTTP-level only).
- `/health` route: NOT IMPLEMENTED (404 in logs).

## 8. Known limitations

- Concept-topic generation (e.g. photosynthesis) can fail validation when the model emits incorrect facts; the validator rejects these and surfaces a clear error. No offline fallback exists for non-conditional topics.
- Arbitrary-topic generation requires local Ollama; only Python conditional topics have an offline fallback.
- No multilingual/Indian-language support verified.
- Browser console and real playback not verified.
- `web/lesson.mp4` is a stale artifact; the app serves previews from `work/`.
- In-memory preview registry resets on server restart; old files may linger.
- `CURRENT_PREVIEW_ID`/`PREVIEW_FILES` are process-local.

## 9. Demonstration plan

1. Start `.\start.ps1`, open http://127.0.0.1:8000.
2. Enter "Python if-else conditional statements", click Generate.
3. Show loading state, then preview playback and download link.
4. Download and play the MP4 (ffprobe can confirm streams).
5. Explain architecture and validation; mention offline fallback behavior and limitations.

## 10. Answering technical questions

- Planning: Ollama `gemma3:4b` with a JSON schema; `validate_lesson_plan` enforces fields, scene counts, topic match.
- Narration: Kokoro `af_heart` -> Zira/SAPI fallback.
- Rendering: Pillow frames at 12 fps, captions, progress bar; clips padded to >=30s.
- Export: FFmpeg libx264/yuv420p + AAC, faststart; range requests supported.
- Fallback: only for Python conditional topics when Ollama is down; otherwise clear error.
- No cloud, no new dependencies added.
