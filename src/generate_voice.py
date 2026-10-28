from __future__ import annotations
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_KOKORO_DIR = Path(__file__).resolve().parents[2] / "kokoro"
DEFAULT_MODEL = DEFAULT_KOKORO_DIR / "kokoro-v1.0.onnx"
DEFAULT_VOICES = DEFAULT_KOKORO_DIR / "voices-v1.0.bin"

def generate_kokoro(lesson_path: Path, output_dir: Path, model_path: Path, voices_path: Path) -> None:
    if not model_path.is_file():
        raise FileNotFoundError(f"Kokoro model not found: {model_path}")
    if not voices_path.is_file():
        raise FileNotFoundError(f"Kokoro voices not found: {voices_path}")

    # Keep phonemizer’s temporary eSpeak DLL copy inside the writable project work folder.
    tempfile.tempdir = str(output_dir)
    import soundfile as sf
    from kokoro_onnx import Kokoro

    lesson = json.loads(lesson_path.read_text(encoding="utf-8-sig"))
    kokoro = Kokoro(str(model_path), str(voices_path))
    for index, scene in enumerate(lesson["scenes"]):
        audio, sample_rate = kokoro.create(
            scene["narration"], voice="af_heart", speed=1.0, lang="en-us"
        )
        if len(audio) == 0 or sample_rate <= 0:
            raise RuntimeError(f"Kokoro returned empty audio for scene {index + 1}")
        output_path = output_dir / f"voice_{index:02d}.wav"
        sf.write(output_path, audio, sample_rate, subtype="PCM_16", format="WAV")
    print("Narration generated with Kokoro af_heart.")

import ctypes
import array
import wave
import os
from espeakng_loader import get_library_path, get_data_path, make_library_available

ESPEAK_LANG = {"English": "en", "Hindi": "hi", "Bengali": "bn", "Tamil": "ta"}

def _espeak_synthesize(text, output_path, language):
    """Synthesize text to a WAV file using eSpeak NG (RETRIEVAL mode). Returns True on success."""
    make_library_available()
    lib = ctypes.CDLL(get_library_path())
    audio = array.array("h")
    CALLBACK = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(ctypes.c_short), ctypes.c_int, ctypes.c_void_p)
    def cb(wav, numsamples, events):
        if wav and numsamples > 0:
            audio.extend(wav[:numsamples])
        return 0
    cb_func = CALLBACK(cb)
    lib.espeak_SetSynthCallback(cb_func)
    lib.espeak_Initialize.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    lib.espeak_Initialize.restype = ctypes.c_int
    sr = lib.espeak_Initialize(1, 0, get_data_path().encode(), 0)
    if sr <= 0:
        raise RuntimeError("eSpeak NG initialization failed")
    voice = ESPEAK_LANG.get(language, "en")
    lib.espeak_SetVoiceByName.argtypes = [ctypes.c_char_p]
    lib.espeak_SetVoiceByName.restype = ctypes.c_int
    if lib.espeak_SetVoiceByName(voice.encode()) != 0:
        raise RuntimeError("eSpeak NG voice not available: %s" % voice)
    text_bytes = (text or "").encode("utf-8") + b"\x00"
    lib.espeak_Synth.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_uint, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
    lib.espeak_Synth.restype = ctypes.c_int
    rc = lib.espeak_Synth(text_bytes, len(text_bytes), 0, 1, 0, 1)
    if rc != 0:
        raise RuntimeError("eSpeak NG synthesis failed (rc=%d)" % rc)
    lib.espeak_Synchronize()
    lib.espeak_Terminate()
    if len(audio) == 0:
        raise RuntimeError("eSpeak NG produced no audio for language %s" % language)
    with wave.open(str(output_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(audio.tobytes())
    return True

def generate_espeak(lesson_path, output_dir, language):
    lesson = json.loads(lesson_path.read_text(encoding="utf-8-sig"))
    for index, scene in enumerate(lesson["scenes"]):
        narration = scene.get("narration", "")
        if not narration.strip():
            raise RuntimeError("Empty narration for scene %d" % (index + 1))
        output_path = output_dir / ("voice_%02d.wav" % index)
        _espeak_synthesize(narration, output_path, language)
    print("Narration generated with eSpeak NG (%s)." % language)

def generate_zira(lesson_path: Path, output_dir: Path, fallback_script: Path) -> None:
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(fallback_script), "-LessonPath", str(lesson_path), "-OutputDir", str(output_dir)],
        check=True,
    )
    print("Narration generated with Microsoft Zira fallback.")
def main() -> None:
    parser = argparse.ArgumentParser(description="Generate local lesson narration.")
    parser.add_argument("--lesson", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--fallback-script", type=Path, required=True)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--voices", type=Path, default=DEFAULT_VOICES)
    parser.add_argument("--language", type=str, default="English")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    language = args.language or "English"
    if language in ("Hindi", "Bengali", "Tamil"):
        generate_espeak(args.lesson, args.output_dir, language)
    elif language == "English":
        try:
            generate_kokoro(args.lesson, args.output_dir, args.model, args.voices)
        except Exception as error:
            print(f"Kokoro narration failed ({error}); using Microsoft Zira fallback.", file=sys.stderr)
            generate_zira(args.lesson, args.output_dir, args.fallback_script)
    else:
        raise SystemExit("Unsupported language: %s" % language)

if __name__ == "__main__":
    main()
