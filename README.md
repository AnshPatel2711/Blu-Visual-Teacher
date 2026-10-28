# Blu Visual Teacher

A local, offline-first visual lesson generator. Enter a topic to plan, narrate, render, and export a 1280×720 H.264/AAC MP4 lesson that plays in the browser.

## Project layout

- `lesson.json` — fallback lesson data (Python if–else); renderer does not execute model output.
- `src/` — local narration and video generation scripts.
- `web/` — browser lesson page, poster, and (stale) playable MP4 artifact.
- `work/` — generated narration clips, frames, and MP4 previews.
- `checks/` — manual verification notes.

## Requirements

- Windows, Python 3.
- Pillow, FFmpeg on PATH, `kokoro-onnx`, `soundfile` (see `requirements.txt`).
- Ollama with `gemma3:4b` (local lesson planning).
- Kokoro models in sibling `../kokoro/` (`kokoro-v1.0.onnx`, `voices-v1.0.bin`) for English.
- eSpeak NG (via `espeakng-loader`) for Hindi, Bengali, Tamil narration.
- `Nirmala.ttc` font (Windows) for Indic scripts.

## Run

```powershell
.\start.ps1
```

Starts the local server at `http://127.0.0.1:8000`. Open the page, enter a topic, choose options, and click Generate Lesson. The app plans, narrates, renders, and exports on demand; it does not rebuild an MP4 on startup.

Rebuild without server: `python src\build_lesson.py` (uses the fallback lesson).

## Notes

- English uses Kokoro `af_heart` with a Microsoft Zira fallback. Hindi/Bengali/Tamil use eSpeak NG.
- Only English, Hindi, Bengali, and Tamil are offered; non-English requests are validated end to end.
- Offline after all local models/assets are downloaded. Ollama must be running for arbitrary topics.
- Browser playback in a real browser was not verified; HTTP endpoints were.
