from __future__ import annotations
import json, math, os, re, shutil, subprocess, sys, tempfile, threading, time, urllib.request, wave, uuid
from pathlib import Path
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "work"
WEB = ROOT / "web"
FALLBACK_EXAMPLE = {
    "condition": "score >= 50", "true_value": "72", "true_output": "Pass",
    "false_value": "35", "false_output": "Try again",
}
FALLBACK_LESSON = json.loads((ROOT / "lesson.json").read_text(encoding="utf-8-sig"))
FALLBACK_LESSON.update({"lesson_type": "conditional", "example": FALLBACK_EXAMPLE})
LESSON = FALLBACK_LESSON
BUILD_LOCK = threading.Lock()
PREVIEW_LOCK = threading.Lock()
PREVIEW_FILES = {}
CURRENT_PREVIEW_ID = None
OLLAMA_MODEL = "gemma3:4b"
OLLAMA_URL = "http://127.0.0.1:11434/api/generate"

class LessonGenerationError(RuntimeError):
    pass

SCENE_KINDS = [scene["kind"] for scene in FALLBACK_LESSON["scenes"]]
LOOP_SCENE_KINDS = ["intro", "loop", "iteration", "iteration", "summary"]
LESSON_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "maxLength": 80},
        "subject": {"type": "string", "enum": ["Python"]},
        "language": {"type": "string", "enum": ["English", "Hindi", "Bengali", "Tamil"]},
        "lesson_type": {"type": "string", "enum": ["conditional"]},
        "example": {
            "type": "object",
            "properties": {
                "condition": {"type": "string", "pattern": "^[A-Za-z_][A-Za-z0-9_]*\\s*(==|!=|>=|<=|>|<)\\s*(-?[0-9]+(\\.[0-9]+)?|True|False|[A-Za-z_][A-Za-z0-9_]*)$"},
                "true_value": {"type": "string", "pattern": "^-?[0-9]+(\\.[0-9]+)?$"},
                "true_output": {"type": "string"},
                "false_value": {"type": "string", "pattern": "^-?[0-9]+(\\.[0-9]+)?$"},
                "false_output": {"type": "string"},
            },
            "required": ["condition", "true_value", "true_output", "false_value", "false_output"],
            "additionalProperties": False,
        },
        "scenes": {
            "type": "array", "minItems": len(SCENE_KINDS), "maxItems": len(SCENE_KINDS),
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "maxLength": 55},
                    "narration": {"type": "string", "maxLength": 220},
                    "kind": {"type": "string", "enum": SCENE_KINDS},
                },
                "required": ["title", "narration", "kind"], "additionalProperties": False,
            },
        },
    },
    "required": ["title", "subject", "language", "lesson_type", "example", "scenes"], "additionalProperties": False,
}
LOOP_LESSON_SCHEMA = json.loads(json.dumps(LESSON_SCHEMA))
LOOP_LESSON_SCHEMA["properties"]["lesson_type"]["enum"] = ["loop"]
LOOP_LESSON_SCHEMA["properties"]["example"] = {
    "type": "object",
    "properties": {"variable": {"type": "string", "enum": ["number"]}, "values": {"type": "array", "items": {"type": "integer", "enum": [1, 2, 3]}, "minItems": 3, "maxItems": 3}},
    "required": ["variable", "values"], "additionalProperties": False,
}
LOOP_LESSON_SCHEMA["properties"]["scenes"]["items"]["properties"]["kind"]["enum"] = LOOP_SCENE_KINDS
CONCEPT_SCENE_KINDS = ["intro", "explain", "example", "worked_example", "application", "summary"]
CONCEPT_LESSON_SCHEMA = {
    "type": "object",
    "properties": {
        "topic": {"type": "string", "maxLength": 120}, "title": {"type": "string", "maxLength": 80},
        "subject": {"type": "string", "maxLength": 40}, "language": {"type": "string", "enum": ["English", "Hindi", "Bengali", "Tamil"]},
        "lesson_type": {"type": "string", "enum": ["concept"]},
        "scenes": {
            "type": "array", "minItems": 3, "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": CONCEPT_SCENE_KINDS},
                    "title": {"type": "string", "maxLength": 55}, "narration": {"type": "string", "maxLength": 280},
                    "visual": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string", "enum": ["cards", "sequence", "compare", "cycle"]},
                            "items": {"type": "array", "minItems": 2, "maxItems": 4,
                                "items": {"type": "object", "properties": {
                                    "label": {"type": "string", "maxLength": 24}, "detail": {"type": "string", "maxLength": 64}},
                                    "required": ["label", "detail"], "additionalProperties": False}},
                        },
                        "required": ["type", "items"], "additionalProperties": False,
                    },
                },
                "required": ["kind", "title", "narration", "visual"], "additionalProperties": False,
            },
        },
    },
    "required": ["topic", "title", "subject", "language", "lesson_type", "scenes"],
    "additionalProperties": False,
}
W, H, FPS = 1280, 720, 12
BG, PANEL, INK, MUTED, BLUE, GREEN, GOLD, RED = "#101522", "#1a2333", "#f3f6fc", "#a8b4c6", "#83d7fa", "#65dfa8", "#f2c879", "#ff8585"
FONT_DIR = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
CURRENT_LANGUAGE = "English"
INDIC_FONT_PATH = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "Nirmala.ttc"
_FONT_CACHE = {}
def font(size, bold=False, language=None):
    if language is None:
        language = CURRENT_LANGUAGE
    key = (size, bold, language)
    cached = _FONT_CACHE.get(key)
    if cached is not None:
        return cached
    if language in ("Hindi", "Bengali", "Tamil") and INDIC_FONT_PATH.exists():
        p = INDIC_FONT_PATH
        try:
            result = ImageFont.truetype(str(p), size, index=0)
        except Exception:
            p = FONT_DIR / "segoeui.ttf"
            result = ImageFont.truetype(str(p), size)
    else:
        p = FONT_DIR / ("segoeuib.ttf" if bold else "segoeui.ttf")
        if not p.exists():
            p = FONT_DIR / "arial.ttf"
        result = ImageFont.truetype(str(p), size)
    _FONT_CACHE[key] = result
    return result
def detect_lesson_type(topic):
    text = topic.strip().lower()
    if re.search(r"\bpython\b", text) and re.search(r"\b(loop|loops|looping|iteration|iterations|iterate|iterating)\b", text):
        return "loop"
    if re.search(r"\bpython\b", text) and re.search(r"\b(if|else|conditional|conditionals|condition|branch|branches)\b", text):
        return "conditional"
    if re.search(r"\bif\s*[-–/]?\s*else\b|\bconditional programming\b", text):
        return "conditional"
    return "concept"

def validate_lesson_plan(candidate, expected_type=None, requested_topic=None, expected_language=None):
    if not isinstance(candidate, dict):
        raise ValueError("Lesson plan must be a JSON object")
    if not all(isinstance(candidate.get(k), str) and candidate[k].strip() for k in ("title", "subject", "language", "lesson_type")):
        raise ValueError("Lesson title, subject, language, and type must be non-empty text")
    if len(candidate["title"]) > 80 or len(candidate["subject"]) > 40:
        raise ValueError("Lesson title or subject is too long")
    lesson_type = candidate["lesson_type"].strip().lower()
    if lesson_type not in ("conditional", "loop", "concept") or (expected_type and lesson_type != expected_type):
        raise ValueError("Generated lesson type does not match the requested topic")
    if candidate["language"].strip().lower() not in ("english", "hindi", "bengali", "tamil"):
        raise ValueError("The local narration pipeline currently supports English, Hindi, Bengali, and Tamil lessons only")
    if expected_language and candidate["language"].strip().casefold() != expected_language.casefold():
        raise ValueError(f"Generated lesson language {candidate['language']!r} does not match requested language {expected_language!r}")

    if lesson_type == "concept":
        required = {"topic", "title", "subject", "language", "lesson_type", "scenes"}
        if set(candidate) != required:
            raise ValueError("Concept lesson has invalid top-level fields")
        if not isinstance(candidate["topic"], str) or not requested_topic:
            raise ValueError("Generated lesson did not identify the requested topic")
        def topic_terms(value):
            ignored={"a","an","the","of","in","on","and","how","working","work","principle","basics","basic","introduction","intro","concept","python"}
            words=re.findall(r"[a-z0-9]+",value.casefold())
            return {word[:-3]+"y" if word.endswith("ies") and len(word)>4 else word[:-1] if word.endswith("s") and not word.endswith("ss") and len(word)>3 else word for word in words if word not in ignored}
        requested_terms=topic_terms(requested_topic)
        generated_terms=topic_terms(candidate["topic"])
        minimum_matches=max(1,math.ceil(len(requested_terms)*0.6))
        if not generated_terms or not generated_terms.issubset(requested_terms) or len(generated_terms & requested_terms)<minimum_matches:
            raise ValueError(f"Generated lesson topic {candidate['topic']!r} does not match requested topic {requested_topic!r}")
        scene_kinds = CONCEPT_SCENE_KINDS
    else:
        required = {"title", "subject", "language", "lesson_type", "example", "scenes"}
        if set(candidate) != required:
            raise ValueError("Lesson plan has invalid top-level fields")
        example = candidate["example"]
        if lesson_type == "conditional":
            example_fields = {"condition", "true_value", "true_output", "false_value", "false_output"}
            if not isinstance(example, dict) or set(example) != example_fields:
                raise ValueError("Conditional example has invalid fields")
            if not all(isinstance(value, str) and value.strip() for value in example.values()):
                raise ValueError("Conditional example fields must be non-empty text")
            literal = r"(?:-?\d+(?:\.\d+)?|True|False|[A-Za-z_][A-Za-z0-9_]*|'[A-Za-z0-9 _-]{1,16}'|\"[A-Za-z0-9 _-]{1,16}\")"
            condition_match = re.fullmatch(rf"([A-Za-z_][A-Za-z0-9_]*)\s*(?:==|!=|>=|<=|>|<)\s*{literal}", example["condition"])
            if not condition_match or len(example["condition"]) > 40:
                raise ValueError("Conditional example must use one simple comparison")
            for field in ("true_value", "false_value"):
                if len(example[field]) > 20 or not re.fullmatch(literal, example[field]):
                    raise ValueError(f"Conditional example {field} must be a simple displayed value")
            for field in ("true_output", "false_output"):
                if len(example[field]) > 24 or not re.fullmatch(r"[A-Za-z0-9 _.,!?-]+", example[field]):
                    raise ValueError(f"Conditional example {field} must be short display text")
            scene_kinds = SCENE_KINDS
        else:
            if not isinstance(example, dict) or set(example) != {"variable", "values"}:
                raise ValueError("Loop example has invalid fields")
            if not isinstance(example["variable"], str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,19}", example["variable"]):
                raise ValueError("Loop variable must be a simple Python identifier")
            values = example["values"]
            if example["variable"] != "number" or values != [1, 2, 3]:
                raise ValueError("Loop example must use number over the values 1, 2, and 3")
            scene_kinds = LOOP_SCENE_KINDS

    scenes = candidate["scenes"]
    if not isinstance(scenes, list) or (lesson_type == "concept" and not 3 <= len(scenes) <= 5) or (lesson_type != "concept" and len(scenes) != len(scene_kinds)):
        raise ValueError("Concept lessons need 3–5 scenes" if lesson_type == "concept" else f"Lesson must contain exactly {len(scene_kinds)} scenes")
    for index, scene in enumerate(scenes):
        expected_kind = scene_kinds[index] if lesson_type != "concept" else scene.get("kind") if isinstance(scene, dict) else None
        fields = {"kind", "title", "narration", "visual"} if lesson_type == "concept" else {"title", "narration", "kind"}
        if not isinstance(scene, dict) or set(scene) != fields:
            raise ValueError(f"Scene {index + 1} has invalid fields")
        if any(not isinstance(scene.get(field), str) or not scene[field].strip() for field in ("title", "narration", "kind")):
            raise ValueError(f"Scene {index + 1} needs a title, narration, and kind")
        if len(scene["title"]) > 55 or len(scene["narration"]) > 500 or scene["kind"] != expected_kind or (lesson_type == "concept" and scene["kind"] not in CONCEPT_SCENE_KINDS):
            raise ValueError(f"Scene {index + 1} text or kind is invalid")
        if lesson_type == "concept":
            narration_tail=re.sub(r"[.!?\"'’”]+$","",scene["narration"].strip()).rstrip().casefold()
            if narration_tail.endswith(("-",",",":",";","/")) or re.search(r"\b(?:a|an|the|and|or|to|of|in|on|at|from|with|because|called|is|are|through|which|into|by|it|them|they|these|those|its|their|making|including|such)$",narration_tail):
                raise ValueError(f"Scene {index + 1} narration appears incomplete near: {scene['narration'][-60:]!r}")
            word_count=len(re.findall(r"\b[\w]+(?:['’][\w]+)?\b",scene["narration"]))
            if word_count<7:
                raise ValueError(f"Scene {index + 1} narration is too short to teach the idea ({word_count} words)")
        if lesson_type == "concept":
            visual = scene["visual"]
            if not isinstance(visual, dict) or set(visual) != {"type", "items"} or visual["type"] not in ("cards", "sequence", "compare", "cycle"):
                raise ValueError(f"Scene {index + 1} visual is invalid")
            items = visual["items"]
            if not isinstance(items, list) or not 2 <= len(items) <= 4:
                raise ValueError(f"Scene {index + 1} needs two to four visual items")
            for item in items:
                if not isinstance(item, dict) or set(item) != {"label", "detail"}:
                    raise ValueError(f"Scene {index + 1} visual item is invalid")
                for field, limit in (("label", 24), ("detail", 64)):
                    value = item[field]
                    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(char) < 32 for char in value):
                        reason=f"length {len(value)} exceeds {limit}" if isinstance(value,str) and len(value)>limit else "contains control characters" if isinstance(value,str) and any(ord(char)<32 for char in value) else "is empty or not text"
                        raise ValueError(f"Scene {index + 1} visual {field} {reason}")
            scene_text=(scene["narration"]+" "+json.dumps(visual,ensure_ascii=False)).casefold()
            if visual["type"]=="cycle" and not re.search(r"cycle|repeat|again|return|continuous|loop|rotat",scene_text):
                raise ValueError(f"Scene {index + 1} uses a cycle visual without describing repetition")
    if requested_topic:
        ignored={"python","lesson","concept","topic","beginner","basic","basics","explain","teaching","with","from","that","this","into","about","what","how","make","using","programming","working","work","principle","introduction","intro","understanding"}
        keywords={word for word in re.findall(r"[a-z0-9]+",requested_topic.casefold()) if len(word)>3 and word not in ignored}
        generated_text=json.dumps({key:value for key,value in candidate.items() if key != "topic"},ensure_ascii=False).casefold()
        matched={word for word in keywords if word in generated_text}
        required_matches=max(1,math.ceil(len(keywords)*0.5)) if keywords else 0
        if len(matched)<required_matches:
            raise ValueError("Generated lesson content does not match the requested topic")
    if lesson_type == "concept" and requested_topic:
        full_text=json.dumps(candidate,ensure_ascii=False).casefold()
        examples=[scene for scene in scenes if scene["kind"] in ("example","worked_example")]
        if not examples:
            raise ValueError("Concept plan needs a concrete example or worked example")
        example_text=" ".join(scene["narration"]+" "+json.dumps(scene["visual"],ensure_ascii=False) for scene in examples).casefold()
        def topic_has(*terms): return all(term in requested_topic.casefold() for term in terms)
        if topic_has("binary", "search"):
            if len(scenes)!=3:
                raise ValueError("Binary search lesson needs exactly three concise scenes")
            binary_examples=[scene for scene in examples if scene["kind"] in ("example","worked_example")]
            missing=set()
            for scene in binary_examples:
                scene_text=(scene["narration"]+" "+json.dumps(scene["visual"],ensure_ascii=False)).casefold()
                lists=[ [int(value) for value in re.findall(r"\d+",match)] for match in re.findall(r"\[\s*\d+(?:\s*,\s*\d+){2,}\s*\]",scene_text) ]
                correct_narrowing=(re.search(r"middle|midpoint",scene_text) and re.search(r"(?:6\s*(?:is|<)\s*less than\s*8|6\s*<\s*8|6\s+is\s+smaller\s+than\s+8)",scene_text) and re.search(r"lower half",scene_text) and re.search(r"\[\s*8\s*,\s*10\s*\]|8\s+and\s+10",scene_text))
                checks={"sorted list":any(values==[2,4,6,8,10] for values in lists) and bool(re.search(r"\bsorted\b",scene_text)),"target 8":bool(re.search(r"\btarget\s*8\b|\bfind\s+8\b",scene_text)),"correct midpoint and narrowing":bool(correct_narrowing),"sequence visual":scene["visual"]["type"]=="sequence"}
                if all(checks.values()): break
                missing.update(label for label,ok in checks.items() if not ok)
            else:
                raise ValueError("Binary search example is missing: "+", ".join(sorted(missing)))
        elif "array" in requested_topic.casefold():
            if len(scenes)!=3:
                raise ValueError("Python arrays lesson needs exactly three concise scenes")
            for snippet in re.findall(r"`([^`]+)`",full_text):
                valid_assignment=re.fullmatch(r"[A-Za-z_]\w*\s*=\s*\[\s*-?\d+(?:\s*,\s*-?\d+)+\s*\]",snippet.strip())
                valid_access=re.fullmatch(r"[A-Za-z_]\w*\[\s*\d+\s*\]",snippet.strip())
                if not (valid_assignment or valid_access):
                    raise ValueError("Python arrays contains an invalid displayed code snippet")
            if re.search(r"array\.array\s*\([^)]*\d+\s*\)",full_text):
                raise ValueError("Python array.array constructor example is invalid")
            array_example_ok=any(re.search(r"\[\s*10\s*,\s*20\s*,\s*30\s*\]",scene_text) and re.search(r"index\s*(?:1|one).{0,35}20|20.{0,35}index\s*(?:1|one)",scene_text) and scene["visual"]["type"]=="sequence" for scene in examples for scene_text in ((scene["narration"]+" "+json.dumps(scene["visual"],ensure_ascii=False)).casefold(),))
            if not array_example_ok:
                raise ValueError("Arrays example must put [10, 20, 30], index 1 returns 20, and a sequence visual together in one example scene")
            if re.search(r"array is (?:a )?(?:special )?type of list",full_text):
                raise ValueError("Python list and array types must not be presented as identical")
            if "array.array" not in full_text and not re.search(r"\barray[- ]like\b|\barray is like a list\b|\barrays are like lists\b",full_text):
                raise ValueError("Python lesson must distinguish a built-in list from array.array")
            number_words={"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,"eight":8,"nine":9,"ten":10}
            for scene in examples:
                list_values=[re.findall(r"-?\d+",match) for match in re.findall(r"\[\s*-?\d+(?:\s*,\s*-?\d+)+\s*\]",scene["narration"])]
                count_match=re.search(r"\b(two|three|four|five|six|seven|eight|nine|ten) numbers\b",scene["narration"].casefold())
                if count_match and list_values and len(list_values[0])!=number_words[count_match.group(1)]:
                    raise ValueError("Array example count does not match its displayed values")
                if re.search(r"\b(change|update|replace|add)\b",scene["narration"].casefold()):
                    if len(list_values)<2:
                        raise ValueError("Array update example needs to show both before and after values")
                    if re.search(r"\b(each|every)\b",scene["narration"].casefold()):
                        if len(list_values[0])!=len(list_values[-1]):
                            raise ValueError("Array transformation must preserve the number of elements")
                    index_match=re.search(r"index\s+(\d+)",scene["narration"].casefold())
                    if index_match and len(list_values)>=2:
                        idx=int(index_match.group(1)); old,new=list_values[0],list_values[-1]
                        if idx<len(old) and idx<len(new) and (old[idx]==new[idx] or [i for i,(a,c) in enumerate(zip(old,new)) if a!=c] != [idx]):
                            raise ValueError("Array update example must change the stated index only")
        elif "photosynthesis" in requested_topic.casefold():
            if not all(term in full_text for term in ("sunlight","water","carbon dioxide")) or not any(term in full_text for term in ("glucose","sugar")) or "oxygen" not in full_text:
                raise ValueError("Photosynthesis needs sunlight, water, carbon dioxide, sugar, and oxygen in its explanation")
            if "air" not in full_text or not re.search(r"leaf|leaves",full_text):
                raise ValueError("Photosynthesis should show carbon dioxide entering through leaves from the air")
            photo_sentences=[sentence.casefold() for scene in scenes for sentence in re.split(r"[.!?]",scene["narration"]+" "+" ".join(item["label"]+" "+item["detail"] for item in scene["visual"]["items"]))]
            if any(re.search(r"(?:carbon dioxide|co2)[^.!?]{0,35}(?:soil|roots)|(?:soil|roots)[^.!?]{0,35}(?:carbon dioxide|co2)",sentence) for sentence in photo_sentences):
                raise ValueError("Photosynthesis plan incorrectly places carbon dioxide in soil or roots")
            if re.search(r"\b(\d+)\s*(?:grams?|g)\s+of\s+water.{0,100}\b\1\s*(?:grams?|g)\s+of\s+carbon dioxide.{0,100}\b\1\s*(?:grams?|g)\s+of\s+(?:glucose|sugar)",full_text):
                raise ValueError("Photosynthesis plan invents an equal-mass input/output example")
            if not any(scene["visual"]["type"]=="sequence" for scene in scenes):
                raise ValueError("Photosynthesis needs a sequence visual for the process")
        elif "motor" in requested_topic.casefold():
            if "magnetic" not in full_text or "coil" not in full_text or not any(term in full_text for term in ("current","electric")) or not re.search(r"rotat|turn",full_text):
                raise ValueError("Electric motor needs the current-to-magnetic-force-to-rotation explanation")
            if not any(scene["visual"]["type"]=="sequence" for scene in scenes):
                raise ValueError("Electric motor needs a sequence visual for its working principle")
            if not any(re.search(r"motor|fan|rotor",(scene["narration"]+json.dumps(scene["visual"])).casefold()) for scene in examples):
                raise ValueError("Electric motor example must demonstrate a motor, fan, or rotor")
            if re.search(r"\d+(?:\.\d+)?\s*(?:v|volts?).{0,80}\d+(?:\.\d+)?\s*(?:hz|rpm)",example_text) or re.search(r"magnet(?:ic field)? pulls? (?:on )?the coil",full_text):
                raise ValueError("Electric motor explanation contains an unsupported physics claim")
            if re.search(r"faster (?:the )?(?:electricity|current).{0,60}faster (?:the )?rotation",full_text):
                raise ValueError("Electric motor plan incorrectly equates faster current with faster rotation")
    return candidate

def generate_lesson_plan(topic, level="Auto", duration=45, language="English", visual_preferences=None):
    topic = topic.strip()
    lesson_type = detect_lesson_type(topic)
    level_guidance = ""
    if level and level != "Auto":
        level_map = {
            "Beginner": "Teach at a Beginner level: define basic terms, use simple examples, avoid jargon, and explain each step slowly.",
            "Intermediate": "Teach at an Intermediate level: explain how the concept works, how parts relate, and use a concrete worked example.",
            "Advanced": "Teach at an Advanced level: include deeper reasoning, important nuances, edge cases, and relevant technical details.",
        }
        level_guidance = level_map.get(level, "Teach at a %s level." % level)
    else:
        level_guidance = "Teach at an Auto level: infer an appropriate depth from the topic and the learner's likely starting point."
    visual_guidance = ""
    if visual_preferences:
        visual_guidance = "Prefer these visual types when they fit: %s. Do not force every listed type into every lesson." % ", ".join(visual_preferences)
    language_guidance = "Produce the entire lesson (titles, narration, labels, examples, summary) in %s. Keep programming keywords and syntax in their original form where appropriate, with explanations in %s." % (language, language)
    teaching_guidance = ("Narration must be educational: introduce the idea, explain what it means and why it matters, "
        "use a concrete example, walk through reasoning step by step, and end with a takeaway. "
        "Avoid filler, repetition, and invented facts. Keep each narration to 2-4 clear sentences.")
    duration_guidance = "Target a lesson of about %d seconds: choose the scene count and depth to fit this duration without padding with silence." % max(30, min(90, int(duration) if duration else 45))
    planning_preamble = ("Before writing scenes, decide the learning objective, the learner's starting point, the key concept, "
        "the best example or demonstration, the logical order of explanations, the most suitable visual for each concept, "
        "and the final takeaway. Then produce only scenes that advance that plan; do not restate the title or repeat a previous explanation.")
    extra_guidance = " ".join(part for part in (level_guidance, visual_guidance, teaching_guidance, duration_guidance, planning_preamble, language_guidance) if part).strip()
    if lesson_type == "concept":
        schema = CONCEPT_LESSON_SCHEMA
        scene_kinds = CONCEPT_SCENE_KINDS
        topic_lower=topic.casefold()
        if "binary" in topic_lower and "search" in topic_lower:
            schema=json.loads(json.dumps(CONCEPT_LESSON_SCHEMA));schema["properties"]["scenes"]["minItems"]=3;schema["properties"]["scenes"]["maxItems"]=3
            schema["properties"]["scenes"]["items"]["properties"]["visual"]["properties"]["type"]["enum"]=["sequence"]
            schema["properties"]["scenes"]["items"]["properties"]["narration"]["maxLength"]=200
            topic_guidance="Use roles intro, worked_example, summary. In worked_example use visual type sequence with 4 items: (1) sorted list [2, 4, 6, 8, 10], (2) target 8 and midpoint 6, (3) 6 < 8 so discard lower half [2, 4, 6], (4) remaining [8, 10], find 8. Say binary search requires sorted input. Each narration: one complete sentence, 8–12 words."
        elif "array" in topic_lower:
            schema=json.loads(json.dumps(CONCEPT_LESSON_SCHEMA));schema["properties"]["scenes"]["minItems"]=3;schema["properties"]["scenes"]["maxItems"]=3
            schema["properties"]["scenes"]["items"]["properties"]["visual"]["properties"]["type"]["enum"]=["sequence"]
            schema["properties"]["scenes"]["items"]["properties"]["visual"]["properties"]["items"]["items"]["properties"]["detail"]["maxLength"]=48
            schema["properties"]["scenes"]["items"]["properties"]["narration"]["maxLength"]=200
            topic_guidance="Use roles intro, worked_example, summary. Intro: Python lists are commonly used as array-like sequences; typed arrays are separate. Worked_example: visual type sequence, items [10, 20, 30], index 0→10, index 1→20, index 2→30. Narration must state index 1 returns 20. No other example or update. Each narration: one complete sentence, 8–12 words."
        elif "photosynthesis" in topic_lower:
            topic_guidance="Explain sunlight, water, and carbon dioxide from air entering leaves through stomata to make glucose and oxygen. Roots absorb water from soil, not carbon dioxide. Avoid invented mass ratios; show the steps as a sequence."
        elif "motor" in topic_lower:
            topic_guidance="For a simple DC motor, explain that current in a coil inside a magnetic field creates force on the current-carrying coil and turns the rotor. Use a fan motor example, not an unrelated magnet experiment. Do not invent a speed from voltage or claim that faster current alone means faster rotation."
        else:
            topic_guidance="Select essential topic-specific facts and use a concrete example appropriate to this subject."
        if extra_guidance:
            topic_guidance = (topic_guidance + " " + extra_guidance).strip()
        if ("binary" in topic_lower and "search" in topic_lower) or "array" in topic_lower:
            schema["properties"]["scenes"]["items"]["properties"]["narration"]["maxLength"]=200
            prompt=(
                "Return an accurate JSON lesson only about " + json.dumps(topic) + ". Use 3 scenes in order: intro, worked_example, summary. "
                "Each narration should be 2-4 clear sentences that teach the idea. Use 2-4 visual items, labels under 24 characters, details under 40 characters, and only cards, sequence, compare, or cycle. "
                + topic_guidance + f" Set subject correctly, language {language}, lesson_type concept, and topic faithfully. Follow this JSON schema exactly: " + json.dumps(schema,ensure_ascii=False)
            )
        else:
            prompt = (
                "Teach ONLY this educational concept in a concise beginner " + language + " lesson: "
                + json.dumps(topic, ensure_ascii=False)
                + ". Return 3 to 5 scenes, choosing the count and scene roles to fit the subject. Include a concrete example or worked_example. "
                "Each scene must include a visual of type cards, sequence, compare, or cycle with two to four short labeled items and brief details. "
                "Choose visual types for their teaching purpose: sequence for steps or processes, compare for genuine contrasts, cycle only for a repeating cycle, cards for grouped ideas. Do not use cards by default or invent unsupported visual types. "
                "Explain why/how using topic-specific facts. Make examples concrete, keep narration and visuals consistent and factual, and do not invent details. Each narration should be 2-4 clear sentences that teach the idea. Keep labels/details short. "
                "Set topic to a faithful concise name retaining its distinguishing terms, subject correctly, lesson_type to concept, and language to " + language + ". "
                + topic_guidance + " Return JSON only, no markdown or executable code. Follow this schema exactly: " + json.dumps(schema, ensure_ascii=False)
            )
    else:
        schema = LOOP_LESSON_SCHEMA if lesson_type == "loop" else LESSON_SCHEMA
        scene_kinds = LOOP_SCENE_KINDS if lesson_type == "loop" else SCENE_KINDS
        example_instructions = (
            "Teach ONLY a Python for-loop over the list [1, 2, 3] using variable number and print(number). Do not mention while, range, break, score, or conditionals. "
            "Write exactly five scenes in this order and match each scene kind exactly: intro, loop, iteration, iteration, summary. "
            "Scene 1 introduces a for-loop over the three-item list. Scene 2 explains the exact for line. Scene 3 explains number=1, scene 4 explains number=2, and scene 5 says number also becomes 3 and then the loop stops. "
            "Each narration should explain what the loop does, how each value is processed, and the takeaway. "
            "The example object must be exactly variable number and values [1, 2, 3]. Every narration must agree with this same example."
            if lesson_type == "loop" else
            "Include one valid simple Python comparison written as identifier operator literal, never a natural-language question. Use numeric values and short outputs. "
            "The condition must match the schema pattern, for example score >= 50; narrations and displayed example must all agree. "
            "Each narration should explain what the condition checks, use a concrete input, explain why it is true or false, identify the executed branch, and state the takeaway. "
            "Reference example shape: " + json.dumps(FALLBACK_EXAMPLE, ensure_ascii=False)
        )
        if extra_guidance:
            example_instructions = (example_instructions + " " + extra_guidance).strip()
        prompt = (
            f"Create a concise beginner {language} Python {lesson_type} lesson about: "
            + json.dumps(topic, ensure_ascii=False)
            + ". Keep the lesson strictly about this requested Python concept. Return five scenes with kinds in this exact order: "
            + ", ".join(scene_kinds) + ". " + example_instructions
            + " Every narration must use the same example shown by the visuals. Keep narration educational, accurate, and easy to say aloud. "
            "Return JSON only, no markdown or executable code. Follow this schema exactly: "
            + json.dumps(schema, ensure_ascii=False)
        )
    def normalize_text(value):
        if isinstance(value, dict): return {key: normalize_text(item) for key,item in value.items()}
        if isinstance(value, list): return [normalize_text(item) for item in value]
        if isinstance(value, str): return "".join(char for char in value.replace("\ufffd", "'").replace("\r", " ").replace("\n", " ").replace("\t", " ") if ord(char)>=32)
        return value
    attempts=2 if lesson_type=="concept" else 1
    for attempt in range(attempts):
        payload = json.dumps({
            "model": OLLAMA_MODEL, "prompt": prompt, "format": schema, "stream": False,
            "options": {"temperature": 0, "num_predict": 1400 if lesson_type == "concept" else 900},
        }).encode("utf-8")
        request = urllib.request.Request(OLLAMA_URL, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=120) as response:
            result = json.load(response)
        generated = result.get("response")
        if not isinstance(generated, str):
            raise ValueError("Ollama response did not contain lesson JSON")
        try:
            try:
                parsed = json.loads(generated)
            except json.JSONDecodeError as error:
                prefix = " ".join(generated.split())[:160]
                prefix = "".join(char if char.isprintable() else "?" for char in prefix)
                print(f"Ollama JSON parse diagnostic: response_length={len(generated)} prefix={prefix!r}", file=sys.stderr)
                raise error
            plan=normalize_text(parsed)
            return validate_lesson_plan(plan, expected_type=lesson_type, requested_topic=topic, expected_language=language)
        except (ValueError, json.JSONDecodeError) as error:
            if attempt+1==attempts: raise
            print(f"Concept plan rejected ({error}); requesting one local correction.",file=sys.stderr)
            prompt=(
                "Regenerate the entire lesson plan from scratch for topic " + json.dumps(topic,ensure_ascii=False) + ". "
                + "Your previous plan failed validation: " + str(error) + ". Correct each listed failure in the indicated scene and do not repeat it. "
                + "Keep the requested lesson language " + language + " and set the JSON language field to that exact value. "
                + topic_guidance + " Use 3 to 5 scenes, only supported visual types cards, sequence, compare, cycle, and details under 40 characters. Every narration must be a complete sentence of at least 8 words; do not use fragments. "
                + "Return JSON only and follow this schema exactly: " + json.dumps(schema,ensure_ascii=False)
            )

def load_lesson_plan(topic, level="Auto", duration=45, language="English", visual_preferences=None):
    lesson_type = detect_lesson_type(topic)
    try:
        lesson = generate_lesson_plan(topic, level=level, duration=duration, language=language, visual_preferences=visual_preferences)
    except Exception as error:
        message = f"Gemma lesson generation failed: {error}"
        print(message, file=sys.stderr)
        # Offline fallback is only meaningful for Python conditional topics, where
        # FALLBACK_LESSON (the fixed if-else lesson) actually teaches the request.
        if lesson_type == "conditional" and language == "English":
            print("Using the local if-else fallback lesson because the model is unavailable.", file=sys.stderr)
            return FALLBACK_LESSON, True
        raise LessonGenerationError(
            "Could not generate a lesson for this topic: the local model is unavailable "
            "and no relevant offline fallback exists for it."
        ) from error
    print(f"Lesson plan generated with local model {OLLAMA_MODEL}.")
    return lesson, False

def wav_seconds(p):
    with wave.open(str(p), "rb") as f: return f.getnframes() / f.getframerate()
def rounded(draw, box, radius, fill, outline=None, width=1): draw.rounded_rectangle(box, radius, fill, outline, width)
def wrap(draw, text, face, width):
    words=text.split(); lines=[]; cur=""
    for word in words:
        test=(cur+" "+word).strip()
        if draw.textbbox((0,0),test,font=face)[2] <= width: cur=test
        else:
            if cur: lines.append(cur)
            cur=word
    if cur: lines.append(cur)
    return lines
def centered(draw, text, y, face, fill, maxwidth=1120):
    box=draw.textbbox((0,0),text,font=face); draw.text(((W-(box[2]-box[0]))/2,y),text,font=face,fill=fill)
def centered_at(draw, cx, text, y, face, fill):
    box=draw.textbbox((0,0),text,font=face); draw.text((cx-(box[2]-box[0])/2,y),text,font=face,fill=fill)
def draw_code(draw, lines, active, result=None):
    x,y,w,h=280,278,720,300
    rounded(draw,(x,y,x+w,y+h),18,"#121a27",outline="#334057",width=2)
    for i,c in enumerate(("#ff7878","#f2c879","#65dfa8")): draw.ellipse((x+22+i*19,y+19,x+31+i*19,y+28),fill=c)
    for i,line in enumerate(lines):
        yy=y+62+i*37
        if i==active: rounded(draw,(x+14,yy-5,x+w-14,yy+31),8,"#203249")
        color=BLUE if "if " in line or "else" in line else INK
        draw.text((x+35,yy),line,font=font(24,True),fill=color)
    if result:
        rounded(draw,(x+22,y+h-53,x+w-22,y+h-18),8,"#1c302e")
        draw.text((x+38,y+h-50),result,font=font(17,True),fill=GREEN)
def draw_concept_visual(draw, visual):
    items=visual["items"]
    mode=visual["type"]
    colors=[BLUE,GREEN,GOLD,"#c5a5ff"]
    if mode == "sequence":
        count=len(items); gap=24; card_w=min(250,(1060-gap*(count-1))//count); total=card_w*count+gap*(count-1); left=(W-total)//2
        y=325; card_h=205
        for i,item in enumerate(items):
            x=left+i*(card_w+gap)
            rounded(draw,(x,y,x+card_w,y+card_h),16,PANEL,outline=colors[i%len(colors)],width=2)
            # Step badge for sequence diagrams
            badge=f"Step {i+1}"
            bw=draw.textbbox((0,0),badge,font=font(12,True))[2]+16
            rounded(draw,(x+10,y+10,x+10+bw,y+30),8,colors[i%len(colors)])
            centered_at(draw,x+10+bw/2,badge,y+12,font(11,True),"#101522")
            centered_at(draw,x+card_w/2,item["label"],y+40,font(17,True),INK)
            lines=wrap(draw,item["detail"],font(14),card_w-28)[:3]
            for j,line in enumerate(lines): centered_at(draw,x+card_w/2,line,y+78+j*20,font(14),MUTED)
            if i<count-1: centered_at(draw,x+card_w+gap/2,"→",y+92,font(27,True),BLUE)
    else:
        gap_x=28; gap_y=12; card_w=470; card_h=125; left=(W-(2*card_w+gap_x))//2; top=275
        for i,item in enumerate(items):
            row,col=divmod(i,2); x=left+col*(card_w+gap_x); y=top+row*(card_h+gap_y)
            outline=colors[i%len(colors)] if mode in ("compare","cycle") else "#35445a"
            rounded(draw,(x,y,x+card_w,y+card_h),16,PANEL,outline=outline,width=2)
            dfont=font(18,True); label_lines=wrap(draw,item["label"],dfont,card_w-32)[:1]
            for j,line in enumerate(label_lines): centered_at(draw,x+card_w/2,line,y+20+j*23,dfont,colors[i%len(colors)])
            detail_lines=wrap(draw,item["detail"],font(14),card_w-34)[:2]
            for j,line in enumerate(detail_lines): centered_at(draw,x+card_w/2,line,y+65+j*20,font(14),MUTED)
        if mode == "cycle":
            rounded(draw,(514,548,766,575),9,"#203249")
            centered_at(draw,640,"CYCLE · REPEATS",551,font(12,True),BLUE)
        elif mode == "compare":
            centered(draw,"Compare the connected ideas.",245,font(18),MUTED)

def render_frame(scene, idx, n, lesson_idx, total):
    im=Image.new("RGB",(W,H),BG); d=ImageDraw.Draw(im)
    d.ellipse((950,-230,1450,270),fill="#172944"); d.ellipse((-230,520,190,940),fill="#15243a")
    d.text((66,42),"BLU  /  VISUAL TEACHER",font=font(14,True),fill=BLUE)
    lesson_type=LESSON.get("lesson_type", "conditional")
    lesson_label={"loop":"LOOPS", "conditional":"IF"+chr(0x2013)+"ELSE"}.get(lesson_type, "CONCEPT")
    subject=LESSON.get("subject", "Topic").upper()[:28]
    d.text((66,77),subject+"     "+chr(0x00b7)+"     "+lesson_label,font=font(13,True),fill=MUTED)
    # Progress is derived from voice clip durations; scene changes exactly at narration boundaries.
    d.text((1130,48),f"{lesson_idx+1:02d} / {total:02d}",font=font(14,True),fill=MUTED)
    d.line((65,111,1215,111),fill="#2a3548",width=2)
    title_font=font(34,True)
    for line_index,line in enumerate(wrap(d,scene["title"],title_font,1080)[:2]):
        centered(d,line,137+line_index*39,title_font,INK)
    kind=scene["kind"]
    if LESSON.get("lesson_type") == "concept":
        visual=scene["visual"]
        draw_concept_visual(d,visual)
    elif LESSON.get("lesson_type") == "loop":
        example=LESSON["example"]
        variable=example["variable"]
        values=example["values"]
        values_text=", ".join(str(value) for value in values)
        code=[f"for {variable} in [{values_text}]:",f"    print({variable})"]
        if kind == "intro":
            centered(d,"Repeat one instruction for each value.",212,font(22),MUTED)
            rounded(d,(190,300,1090,490),20,PANEL,outline="#34516a",width=2)
            centered_at(d,640,"VALUES",326,font(16,True),BLUE)
            centered_at(d,640,"   →   ".join(str(value) for value in values),365,font(32,True),INK)
            centered_at(d,640,f"Do the same work with {variable} each time.",435,font(20),MUTED)
        elif kind == "loop":
            centered(d,"The for line takes one value at a time.",210,font(21),MUTED)
            draw_code(d,code,0,"THREE VALUES  →  THREE ITERATIONS")
        elif kind == "iteration":
            step=max(0,min(len(values)-1,lesson_idx-2))
            value=values[step]
            centered(d,f"Iteration {step+1}: {variable} receives {value}.",210,font(21),MUTED)
            draw_code(d,code,1,f"OUTPUT     {value}")
        else:
            centered(d,"After the final value, the loop stops.",210,font(21),MUTED)
            rounded(d,(170,305,1110,485),20,PANEL,outline="#34516a",width=2)
            centered_at(d,640,"   →   ".join(str(value) for value in values),345,font(31,True),GREEN)
            centered_at(d,640,"Each value was handled once; no values remain.",420,font(19),MUTED)
    else:
        example=LESSON.get("example", FALLBACK_EXAMPLE)
        variable=re.match(r"([A-Za-z_][A-Za-z0-9_]*)", example["condition"]).group(1)
        true_print=f"    print({json.dumps(example['true_output'], ensure_ascii=False)})"
        false_print=f"    print({json.dumps(example['false_output'], ensure_ascii=False)})"
        if kind=="intro":
            centered(d,"Choose the branch that matches the condition.",212,font(22),MUTED)
            rounded(d,(150,295,610,515),22,PANEL,outline="#34516a",width=2)
            rounded(d,(670,295,1130,515),22,PANEL,outline="#514638",width=2)
            centered_at(d,380,"if",318,font(34,True),BLUE)
            centered_at(d,900,"else",318,font(34,True),GOLD)
            centered_at(d,380,example["condition"],372,font(21,True),INK)
            centered_at(d,900,"otherwise",372,font(21,True),INK)
            centered_at(d,380,example["true_output"],414,font(19),MUTED)
            centered_at(d,900,example["false_output"],414,font(19),MUTED)
            rounded(d,(270,462,490,498),10,"#1c302e")
            rounded(d,(790,462,1010,498),10,"#332d22")
            centered_at(d,380,"TRUE",470,font(15,True),GREEN)
            centered_at(d,900,"FALSE",470,font(15,True),GOLD)
        elif kind=="condition":
            centered(d,"Ask a question that has a true or false answer.",210,font(21),MUTED)
            draw_code(d,[f"{variable} = {example['true_value']}",f"if {example['condition']}:",true_print,"else:",false_print],1,example["condition"] + "   " + chr(0x2192) + "   TRUE")
        elif kind=="true":
            centered(d,f"{example['true_value']} makes the condition true, so the if branch runs.",210,font(21),MUTED)
            draw_code(d,[f"{variable} = {example['true_value']}",f"if {example['condition']}:",true_print,"else:",false_print],2,"OUTPUT     " + example["true_output"])
        elif kind=="false":
            centered(d,f"{example['false_value']} makes the condition false, so Python takes else.",210,font(21),MUTED)
            draw_code(d,[f"{variable} = {example['false_value']}",f"if {example['condition']}:",true_print,"else:",false_print],4,"OUTPUT     " + example["false_output"])
        else:
            centered(d,"One condition. Two possible paths.",210,font(21),MUTED)
            rounded(d,(150,295,610,515),22,"#1b2b39",outline="#34516a",width=2)
            rounded(d,(670,295,1130,515),22,"#26251f",outline="#514638",width=2)
            centered_at(d,380,"if",326,font(38,True),BLUE)
            centered_at(d,900,"else",326,font(38,True),GOLD)
            centered_at(d,380,"condition is true",390,font(19),MUTED)
            centered_at(d,900,"condition is false",390,font(19),MUTED)
            rounded(d,(255,445,505,485),10,"#203249")
            rounded(d,(775,445,1025,485),10,"#332d22")
            centered_at(d,380,example["true_output"],454,font(17,True),INK)
            centered_at(d,900,example["false_output"],454,font(17,True),INK)
            centered(d,"Check the condition, then follow the matching path.",544,font(19,True),INK)
    # Captions are contained in a dedicated three-line-safe area.
    rounded(d,(70,600,1210,694),12,"#0b101a",outline="#2c374a")
    cap=font(16); lines=wrap(d,scene["narration"],cap,1080)
    if len(lines)>3:
        cap=font(14); lines=wrap(d,scene["narration"],cap,1080)
    if len(lines)>3:
        lines=lines[:3]
        if lines and not lines[-1].endswith("…"):
            lines[-1]=lines[-1].rstrip()+ "…"
    for j,line in enumerate(lines[:3]):
        box=d.textbbox((0,0),line,font=cap); d.text(((W-(box[2]-box[0]))/2,607+j*25),line,font=cap,fill="#d9e1ee")
    # Thin progress line advances through this scene based on its exact clip duration.
    d.rounded_rectangle((65,710,1215,716),radius=3,fill="#2b3548")
    d.rounded_rectangle((65,710,65+int(1150*(idx/max(1,n-1))),716),radius=3,fill=BLUE)
    return im

def build_lesson(topic=None, level="Auto", duration=45, language="English", visual_preferences=None):
    global LESSON, CURRENT_PREVIEW_ID, CURRENT_LANGUAGE
    CURRENT_LANGUAGE = language or "English"
    _t0 = time.perf_counter()
    if topic:
        _tp = time.perf_counter()
        LESSON, used_fallback = load_lesson_plan(topic, level=level, duration=duration, language=language, visual_preferences=visual_preferences)
        print("TIMING planning=%.3fs" % (time.perf_counter()-_tp), file=sys.stderr)
    else:
        LESSON, used_fallback = FALLBACK_LESSON, True
    if language not in ("English", "Hindi", "Bengali", "Tamil"):
        raise ValueError("Unsupported language: %s" % language)
    if language and isinstance(LESSON, dict):
        LESSON["language"] = language
    if level and isinstance(LESSON, dict):
        LESSON["level"] = level
    ffmpeg=shutil.which("ffmpeg")
    if not ffmpeg: raise RuntimeError("ffmpeg is required and must be on PATH")
    WORK.mkdir(exist_ok=True)
    voice_script=ROOT/"src"/"generate_voice.py"
    build_dir=Path(tempfile.mkdtemp(prefix="blu_build_",dir=WORK))
    preview_id=uuid.uuid4().hex
    preview_path=WORK/f"lesson-preview-{preview_id}.mp4"
    registered=False
    try:
        voice_lesson=build_dir/"voice_lesson.json"
        voice_lesson.write_text(json.dumps(LESSON,ensure_ascii=False),encoding="utf-8")
        _tv = time.perf_counter()
        cmd=[sys.executable,str(voice_script),"--lesson",str(voice_lesson),"--output-dir",str(build_dir),"--fallback-script",str(ROOT/"src"/"generate_voice.ps1"),"--language",str(language)]
        subprocess.run(cmd,check=True)
        print("TIMING tts=%.3fs" % (time.perf_counter()-_tv), file=sys.stderr)
        clips=[build_dir/f"voice_{i:02d}.wav" for i in range(len(LESSON["scenes"]))]
        speech_durations=[wav_seconds(p) for p in clips]
        pause=0.24
        target_duration=max(30,min(90,int(duration))) if duration else 45
        if len(clips) > 1 and sum(speech_durations)+pause*(len(clips)-1) < target_duration:
            pause=max(pause,(target_duration-sum(speech_durations))/(len(clips)-1))
        durations=[duration+(pause if i < len(clips)-1 else 0) for i,duration in enumerate(speech_durations)]
        if any(d<=0 for d in durations): raise RuntimeError("A narration clip was empty")
        frame_no=0
        _tr = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="blu_render_", dir=build_dir) as td:
            tmp=Path(td); frame_dir=tmp/"frames"; frame_dir.mkdir()
            for sidx,(scene,duration) in enumerate(zip(LESSON["scenes"],durations)):
                count=max(1,math.ceil(duration*FPS))
                for i in range(count):
                    im=render_frame(scene,i,count,sidx,len(LESSON["scenes"]))
                    im.save(frame_dir/f"frame_{frame_no:06d}.png",optimize=False)
                    frame_no+=1
            audio=tmp/"narration.wav"
            with wave.open(str(clips[0]),"rb") as src:
                params=src.getparams()
            silence_frames=int(pause*params.framerate)
            frames=[]
            for i,clip in enumerate(clips):
                with wave.open(str(clip),"rb") as src:
                    if src.getparams()[:3] != params[:3]: raise RuntimeError("Narration clips use incompatible audio settings")
                    frames.append(src.readframes(src.getnframes()))
                if i < len(clips)-1: frames.append(b"\0" * silence_frames * params.nchannels * params.sampwidth)
            with wave.open(str(audio),"wb") as dst:
                dst.setparams(params); dst.writeframes(b"".join(frames))
            rendered_output=build_dir/"lesson_render.mp4"
            _te = time.perf_counter()
            subprocess.run([ffmpeg,"-y","-framerate",str(FPS),"-i",str(frame_dir/"frame_%06d.png"),"-i",str(audio),"-c:v","libx264","-preset","fast","-crf","22","-pix_fmt","yuv420p","-af",f"volume=0.86,afade=t=in:st=0:d=0.2,afade=t=out:st={sum(durations)-0.3:.3f}:d=0.25","-c:a","aac","-b:a","128k","-shortest","-movflags","+faststart",str(rendered_output)],check=True)
            print("TIMING ffmpeg=%.3fs" % (time.perf_counter()-_te), file=sys.stderr)
        shutil.move(str(rendered_output),str(preview_path))
        print("TIMING render_total=%.3fs frames=%d" % (time.perf_counter()-_tr, frame_no), file=sys.stderr)
        with PREVIEW_LOCK:
            old_id=CURRENT_PREVIEW_ID
            old_path=PREVIEW_FILES.pop(old_id,None) if old_id else None
            PREVIEW_FILES[preview_id]=preview_path
            CURRENT_PREVIEW_ID=preview_id
        registered=True
        if old_path:
            try: old_path.unlink(missing_ok=True)
            except OSError: pass
    finally:
        shutil.rmtree(build_dir,ignore_errors=True)
        if not registered: preview_path.unlink(missing_ok=True)
    print("TIMING total=%.3fs" % (time.perf_counter()-_t0), file=sys.stderr)
    print(f"Prepared local preview {preview_id} ({sum(durations):.1f}s voice-led lesson, {frame_no} frames).")
    return {"lesson": LESSON, "fallback": used_fallback, "duration": round(sum(durations), 2), "preview_url": f"/api/preview/{preview_id}", "export_url": f"/api/export/{preview_id}"}

class LessonRequestHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB), **kwargs)

    def do_GET(self):
        path=urlsplit(self.path).path
        for route in ("/api/preview/", "/api/export/"):
            if path.startswith(route):
                token=path[len(route):]
                if not re.fullmatch(r"[a-f0-9]{32}",token):
                    self.send_error(404)
                    return
                self._serve_preview(token,download=route=="/api/export/")
                return
        super().do_GET()

    def do_HEAD(self):
        path=urlsplit(self.path).path
        for route in ("/api/preview/", "/api/export/"):
            if path.startswith(route):
                token=path[len(route):]
                if not re.fullmatch(r"[a-f0-9]{32}",token):
                    self.send_error(404)
                    return
                self._serve_preview(token,download=route=="/api/export/",head=True)
                return
        super().do_HEAD()

    def _serve_preview(self,token,download=False,head=False):
        with PREVIEW_LOCK:
            path=PREVIEW_FILES.get(token)
        if path is None or not path.is_file():
            self.send_error(404,"This generated preview is no longer available")
            return
        size=path.stat().st_size
        start,end=0,size-1
        partial=False
        requested=self.headers.get("Range")
        if requested:
            match=re.fullmatch(r"bytes=(\d*)-(\d*)",requested.strip())
            if not match or (not match.group(1) and not match.group(2)):
                self.send_response(416)
                self.send_header("Content-Range",f"bytes */{size}")
                self.send_header("Content-Length","0")
                self.end_headers()
                return
            if match.group(1):
                start=int(match.group(1)); end=int(match.group(2)) if match.group(2) else size-1
            else:
                suffix=int(match.group(2)); start=max(0,size-suffix)
            if start>=size or end<start:
                self.send_response(416)
                self.send_header("Content-Range",f"bytes */{size}")
                self.send_header("Content-Length","0")
                self.end_headers()
                return
            end=min(end,size-1)
            partial=True
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type","video/mp4")
        self.send_header("Accept-Ranges","bytes")
        self.send_header("Content-Length",str(end-start+1))
        self.send_header("Cache-Control","no-store")
        if partial:
            self.send_header("Content-Range",f"bytes {start}-{end}/{size}")
        if download:
            self.send_header("Content-Disposition",'attachment; filename="blu-visual-lesson.mp4"')
        self.end_headers()
        if head:
            return
        try:
            with path.open("rb") as video:
                video.seek(start)
                remaining=end-start+1
                while remaining:
                    chunk=video.read(min(64*1024,remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining-=len(chunk)
        except (BrokenPipeError,ConnectionResetError):
            pass

    def do_POST(self):
        if urlsplit(self.path).path != "/api/generate":
            self._send_json(404, {"error": "Unknown local API endpoint"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= 4096:
                raise ValueError("Enter a topic up to 120 characters long")
            request_data = json.loads(self.rfile.read(length))
            topic = request_data.get("topic") if isinstance(request_data, dict) else None
            if not isinstance(topic, str) or not topic.strip() or len(topic.strip()) > 120 or any(ord(c) < 32 for c in topic):
                raise ValueError("Enter a topic up to 120 characters long")
            level = request_data.get("level", "Auto") if isinstance(request_data, dict) else "Auto"
            if level not in ("Auto", "Beginner", "Intermediate", "Advanced"):
                raise ValueError("Study level must be Auto, Beginner, Intermediate, or Advanced")
            try:
                duration = int(request_data.get("duration", 45)) if isinstance(request_data, dict) else 45
            except (TypeError, ValueError):
                raise ValueError("Duration must be a number")
            if duration < 30 or duration > 90:
                raise ValueError("Duration must be between 30 and 90 seconds")
            language = request_data.get("language", "English") if isinstance(request_data, dict) else "English"
            if language not in ("English", "Hindi", "Bengali", "Tamil"):
                raise ValueError("Language must be English, Hindi, Bengali, or Tamil")
            visual_preferences = request_data.get("visual_preferences", []) if isinstance(request_data, dict) else []
            if not isinstance(visual_preferences, list) or any(not isinstance(v, str) or v not in ("cards", "sequence", "compare", "cycle") for v in visual_preferences):
                raise ValueError("Visual preferences must be supported visual types")
            with BUILD_LOCK:
                result = build_lesson(topic.strip(), level=level, duration=duration, language=language, visual_preferences=visual_preferences)
            self._send_json(200, result)
        except LessonGenerationError as error:
            self._send_json(502, {"error": str(error)})
        except (ValueError, json.JSONDecodeError) as error:
            self._send_json(400, {"error": str(error)})
        except Exception as error:
            self._send_json(500, {"error": str(error)})

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

def run_server():
    server = ThreadingHTTPServer(("127.0.0.1", 8000), LessonRequestHandler)
    print("Blu Visual Teacher is ready at http://127.0.0.1:8000")
    try:
        server.serve_forever()
    finally:
        server.server_close()
        with PREVIEW_LOCK:
            previews=list(PREVIEW_FILES.values())
            PREVIEW_FILES.clear()
        for preview in previews:
            try: preview.unlink(missing_ok=True)
            except OSError: pass

if __name__ == "__main__":
    if "--serve" in sys.argv:
        run_server()
    else:
        build_lesson()
