#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Recap Cinema — mobile-first Myanmar Movie Recap video maker.

Workflow (single page, phone friendly):
  1. Upload video (MP4/MOV/WEBM) -> instant preview
  2. "စာသားထုတ် + မြန်မာလိုပြန်မည်" -> AssemblyAI transcription (Chinese)
     -> Gemini rewrite in natural Myanmar recap style -> editable segments
  3. Settings: subtitle mask (sliders), logo (6 presets + sliders),
     original-voice toggle, Myanmar voice (Edge TTS or own upload),
     background music (optional, separate volumes)
  4. "အစမ်းကြည့်မည်" -> preview player with all effects
  5. "မြန်မာ Movie Recap ပြုလုပ်မည်" -> TTS + ffmpeg render -> final MP4
  6. "ဗီဒီယိုသိမ်းရန်" -> download

API keys are NEVER hardcoded: use the textboxes in the UI or the
ASSEMBLYAI_API_KEY / GEMINI_API_KEY environment variables.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

APP_NAME = "🎬 Recap Cinema"
MAX_VIDEO_SECONDS = 600          # 10-minute source limit
ALLOWED_VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
ALLOWED_LOGO_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
ALLOWED_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".ogg", ".aac"}
AUDIO_SAMPLE_RATE = 16000

# Edge TTS Myanmar voices
VOICES = {
    "မင်းခန့် (အမျိုးသား)": "my-MM-ThihaNeural",
    "နီလာ (အမျိုးသမီး)": "my-MM-NilarNeural",
}
DEFAULT_VOICE_LABEL = "မင်းခန့် (အမျိုးသား)"

# Logo position presets -> ffmpeg overlay x:y expressions (12px margin)
LOGO_PRESETS = {
    "⬆️ အပေါ်-ဘယ်": ("12", "12"),
    "⬆️ အပေါ်-အလယ်": ("(W-w)/2", "12"),
    "⬆️ အပေါ်-ညာ": ("W-w-12", "12"),
    "⬇️ အောက်-ဘယ်": ("12", "H-h-12"),
    "⬇️ အောက်-အလယ်": ("(W-w)/2", "H-h-12"),
    "⬇️ အောက်-ညာ": ("W-w-12", "H-h-12"),
}
DEFAULT_LOGO_PRESET = "⬆️ အပေါ်-ညာ"

AAI_BASE = "https://api.assemblyai.com/v2"

# ----------------------------------------------------------------------------
# Myanmar messages
# ----------------------------------------------------------------------------

E_NO_VIDEO = "❌ ဗီဒီယိုဖိုင် မရွေးထားပါ။ အရင် ဗီဒီယို တင်ပေးပါ။"
E_BAD_EXT = "❌ ဖိုင်အမျိုးအစား မမှန်ပါ။ MP4 / MOV / WEBM သာ လက်ခံပါသည်။"
E_TOO_LONG = "❌ ဗီဒီယိုကြာချိန် {dur:.0f} စက္ကန့်ရှိပါသည်။ ၁၀ မိနစ် (၆၀၀ စက္ကန့်) ထက် မကျော်ရပါ။"
E_NO_VSTREAM = "❌ ဒီဖိုင်မှာ ဗီဒီယို stream မတွေ့ပါ။"
E_NO_ASTREAM = "❌ ဒီဖိုင်မှာ အသံ stream မတွေ့ပါ။ စာသားထုတ်ဖို့ အသံလိုပါသည်။"
E_PROBE_FAIL = "❌ ဗီဒီယိုဖိုင် ဖတ်မရပါ။ ဖိုင် ပျက်နေနိုင်ပါသည်။"
E_NO_AAI_KEY = "❌ AssemblyAI API key ထည့်ပေးပါ (🔑 API Keys အကွက်မှာ)။"
E_AAI_UPLOAD = "❌ အသံဖိုင် တင်မရပါ (AssemblyAI)။\nအသေးစိတ်: {detail}"
E_AAI_REQUEST = "❌ Transcription တောင်းမရပါ။\nအသေးစိတ်: {detail}"
E_AAI_FAILED = "❌ Transcription မအောင်မြင်ပါ။\nအသေးစိတ်: {detail}"
E_AAI_TIMEOUT = "❌ Transcription အချိန်ကြာလွန်းပါသည်။ ထပ်စမ်းကြည့်ပါ။"
E_NO_TRANSCRIPT = "❌ အရင် 'စာသားထုတ် + မြန်မာလိုပြန်မည်' ကို နှိပ်ပါ။"
E_BAD_JSON = "❌ JSON ပုံစံ မမှန်ပါ။\nအသေးစိတ်: {detail}"
E_JSON_NOT_LIST = "❌ JSON သည် array [...] ပုံစံ ဖြစ်ရပါမည်။"
E_JSON_EMPTY = "❌ JSON array အလွတ်ဖြစ်နေပါသည်။"
E_JSON_ITEM = "❌ အပိုင်း {i}: start / end / narration ပါရပါမည်။"
E_JSON_TIME = "❌ အပိုင်း {i}: အချိန် မမှန်ပါ။"
E_JSON_NARR_EMPTY = "❌ အပိုင်း {i}: narration စာသား အလွတ်ဖြစ်နေပါသည်။"
E_JSON_OVERLAP = "❌ အပိုင်း {i} နှင့် {j} အချိန်ထပ်နေပါသည်။"
E_NO_RECAP = "❌ recap စာသား မရှိသေးပါ။ အရင် စာသားထုတ်ပါ။"
E_NO_GEMINI_KEY = "❌ Gemini API key မရှိပါ — 🔑 API Keys မှာ ထည့်ပါ၊ သို့မဟုတ် JSON ကို ကိုယ်တိုင် paste ထည့်ပါ။"
E_GEMINI_FAIL = "❌ Gemini မှ စာသား ထုတ်မရပါ။\nအသေးစိတ်: {detail}"
E_TTS_FAIL = "❌ အသံထုတ်မရပါ။\nအသေးစိတ်: {detail}"
E_NO_NARRATION = "❌ မြန်မာအသံ မရှိသေးပါ။"
E_FFMPEG_FAIL = "❌ ဗီဒီယို render မရပါ။\nအသေးစိတ်: {detail}"
E_LOGO_BAD = "❌ လိုဂိုဖိုင် ပုံမဟုတ်ပါ။ PNG / JPG / WEBP သာ လက်ခံပါသည်။"
E_AUDIO_BAD = "❌ အသံဖိုင် မမှန်ပါ။ MP3 / WAV သာ လက်ခံပါသည်။"
E_NETWORK = "❌ အင်တာနက်ချိတ်ဆက်မှု ပြဿနာ။\nအသေးစိတ်: {detail}"

OK_VIDEO = "✅ ဗီဒီယို လက်ခံပါသည် ({dur:.1f} စက္ကန့်)။"
OK_TRANSCRIBE = "✅ စာသား ထုတ်ပြီးပါပြီ — {n} ပိုင်း ရရှိပါသည်။"
OK_REWRITE = "✅ မြန်မာလို ပြန်ရေးပြီးပါပြီ — {n} ပိုင်း။ အောက်မှာ ပြင်ဆင်နိုင်ပါသည်။"
OK_TTS = "✅ အသံ ထုတ်ပြီးပါပြီ — {n}/{total} ပိုင်း။"
OK_RENDER = "✅ Render ပြီးပါပြီ!"

# ----------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------

MY_DIGITS = "၀၁၂၃၄၅၆၇၈၉"


def my_num(n):
    """Convert int to Myanmar numerals, e.g. 12 -> '၁၂'."""
    return "".join(MY_DIGITS[int(d)] for d in str(int(n)))


def run_cmd(args, timeout=300):
    """Run a subprocess, return (ok, stdout, stderr). Never raises."""
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.returncode == 0, p.stdout.strip(), p.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "", "အချိန်ကုန်သွားပါသည် (timeout)"
    except FileNotFoundError:
        return False, "", "command မတွေ့ပါ: {}".format(args[0])
    except Exception as ex:  # noqa: BLE001
        return False, "", str(ex)


def get_api_key(textbox_val, env_name):
    v = (textbox_val or "").strip()
    if v:
        return v
    return (os.environ.get(env_name, "") or "").strip()


def safe_float(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def fmt_time(s):
    s = max(0.0, float(s))
    m, sec = divmod(s, 60)
    return "{:02d}:{:05.2f}".format(int(m), sec)


def probe_media(path):
    """Return dict with duration/width/height/fps/has_audio, or (None, errmsg)."""
    ok, out, _err = run_cmd([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-show_entries", "stream=width,height,avg_frame_rate,codec_type",
        "-of", "json", path,
    ], timeout=60)
    if not ok:
        return None, E_PROBE_FAIL
    try:
        info = json.loads(out)
    except Exception:  # noqa: BLE001
        return None, E_PROBE_FAIL
    try:
        duration = float(info.get("format", {}).get("duration", 0) or 0)
    except (TypeError, ValueError):
        return None, E_PROBE_FAIL
    width = height = 0
    fps = 30.0
    has_video = has_audio = False
    for s in info.get("streams", []):
        if s.get("codec_type") == "video" and not has_video:
            has_video = True
            width = int(s.get("width", 0) or 0)
            height = int(s.get("height", 0) or 0)
            fr = s.get("avg_frame_rate", "30/1") or "30/1"
            try:
                num, den = fr.split("/")
                fps = float(num) / float(den) if float(den) else 30.0
            except Exception:  # noqa: BLE001
                fps = 30.0
        elif s.get("codec_type") == "audio":
            has_audio = True
    if not has_video:
        return None, E_NO_VSTREAM
    return {
        "duration": duration, "width": width, "height": height,
        "fps": fps if fps > 0 else 30.0, "has_audio": has_audio,
    }, ""


def validate_video_file(path):
    """Validate uploaded video. Returns (media_info, errmsg)."""
    if not isinstance(path, str) or not path or not os.path.isfile(path):
        return None, E_NO_VIDEO
    ext = os.path.splitext(path)[1].lower()
    if ext not in ALLOWED_VIDEO_EXTS:
        return None, E_BAD_EXT
    info, err = probe_media(path)
    if info is None:
        return None, err
    if not info["has_audio"]:
        return None, E_NO_ASTREAM
    if info["duration"] <= 0:
        return None, E_PROBE_FAIL
    if info["duration"] > MAX_VIDEO_SECONDS:
        return None, E_TOO_LONG.format(dur=info["duration"])
    return info, ""


def validate_audio_file(path, what="အသံဖိုင်"):
    if not isinstance(path, str) or not path or not os.path.isfile(path):
        return E_AUDIO_BAD + " ({})".format(what)
    ext = os.path.splitext(path)[1].lower()
    if ext not in ALLOWED_AUDIO_EXTS:
        return E_AUDIO_BAD + " ({})".format(what)
    return ""


def probe_duration(path):
    """Audio/video duration in seconds, or -1 on failure."""
    ok, out, _ = run_cmd([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "csv=p=0", path,
    ], timeout=60)
    if not ok:
        return -1
    try:
        return float(out)
    except (TypeError, ValueError):
        return -1


def ensure_workdir(workdir):
    if workdir and os.path.isdir(workdir):
        return workdir
    return tempfile.mkdtemp(prefix="recap_cinema_")

# ----------------------------------------------------------------------------
# Audio extraction + AssemblyAI transcription
# ----------------------------------------------------------------------------

def extract_audio(video_path, out_wav):
    """Extract mono 16kHz wav for transcription. Returns errmsg or ''."""
    ok, _, err = run_cmd([
        "ffmpeg", "-y", "-v", "error",
        "-i", video_path,
        "-vn", "-ac", "1", "-ar", str(AUDIO_SAMPLE_RATE),
        "-acodec", "pcm_s16le",
        out_wav,
    ], timeout=300)
    if not ok or not os.path.isfile(out_wav):
        return E_FFMPEG_FAIL.format(detail=err or "အသံထုတ်မရပါ")
    return ""


def aai_upload(api_key, wav_path):
    """Upload audio to AssemblyAI. Returns (upload_url, errmsg)."""
    try:
        with open(wav_path, "rb") as f:
            r = requests.post(
                AAI_BASE + "/upload",
                headers={"authorization": api_key},
                data=f, timeout=180,
            )
    except requests.RequestException as ex:
        return "", E_NETWORK.format(detail=str(ex)[:200])
    except Exception as ex:  # noqa: BLE001
        return "", E_AAI_UPLOAD.format(detail=str(ex)[:200])
    if r.status_code not in (200, 201):
        return "", E_AAI_UPLOAD.format(detail="HTTP {}: {}".format(r.status_code, r.text[:200]))
    try:
        return r.json()["upload_url"], ""
    except Exception as ex:  # noqa: BLE001
        return "", E_AAI_UPLOAD.format(detail=str(ex)[:200])


def aai_request_transcript(api_key, audio_url):
    """Start transcription job. Returns (transcript_id, errmsg)."""
    try:
        r = requests.post(
            AAI_BASE + "/transcript",
            headers={"authorization": api_key, "content-type": "application/json"},
            json={"audio_url": audio_url, "language_code": "zh"},
            timeout=60,
        )
    except requests.RequestException as ex:
        return "", E_NETWORK.format(detail=str(ex)[:200])
    except Exception as ex:  # noqa: BLE001
        return "", E_AAI_REQUEST.format(detail=str(ex)[:200])
    if r.status_code not in (200, 201):
        return "", E_AAI_REQUEST.format(detail="HTTP {}: {}".format(r.status_code, r.text[:200]))
    try:
        tid = r.json()["id"]
    except Exception as ex:  # noqa: BLE001
        return "", E_AAI_REQUEST.format(detail=str(ex)[:200])
    if not tid:
        return "", E_AAI_REQUEST.format(detail="transcript id မရပါ")
    return tid, ""


def aai_poll(api_key, transcript_id, timeout_s=600):
    """Poll until completed/error. Returns (status, errmsg)."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            r = requests.get(
                AAI_BASE + "/transcript/" + transcript_id,
                headers={"authorization": api_key}, timeout=30,
            )
        except requests.RequestException as ex:
            return "error", E_NETWORK.format(detail=str(ex)[:200])
        except Exception as ex:  # noqa: BLE001
            return "error", E_AAI_FAILED.format(detail=str(ex)[:200])
        if r.status_code != 200:
            return "error", E_AAI_REQUEST.format(
                detail="HTTP {}: {}".format(r.status_code, r.text[:200]))
        data = r.json()
        status = data.get("status", "")
        if status == "completed":
            return "completed", ""
        if status == "error":
            return "error", E_AAI_FAILED.format(
                detail=str(data.get("error", "unknown"))[:200])
        time.sleep(3)
    return "error", E_AAI_TIMEOUT


def aai_fetch_sentences(api_key, transcript_id):
    """Fetch timestamped sentences. Returns (segments, '')."""
    segments = []
    try:
        r = requests.get(
            AAI_BASE + "/transcript/" + transcript_id + "/sentences",
            headers={"authorization": api_key}, timeout=60,
        )
        if r.status_code == 200:
            for s in r.json().get("sentences", []):
                text = (s.get("text") or "").strip()
                if not text:
                    continue
                segments.append({
                    "start": round(float(s.get("start", 0)) / 1000.0, 2),
                    "end": round(float(s.get("end", 0)) / 1000.0, 2),
                    "text": text,
                })
    except Exception:  # noqa: BLE001
        segments = []
    if segments:
        return segments, ""
    # Fallback: chunk words by punctuation / duration
    try:
        r = requests.get(
            AAI_BASE + "/transcript/" + transcript_id,
            headers={"authorization": api_key}, timeout=60,
        )
        words = r.json().get("words", []) if r.status_code == 200 else []
    except Exception:  # noqa: BLE001
        words = []
    buf, seg_start = [], None
    last_end = 0.0
    for w in words:
        txt = w.get("text", "")
        ws = float(w.get("start", 0)) / 1000.0
        we = float(w.get("end", 0)) / 1000.0
        if seg_start is None:
            seg_start = ws
        buf.append(txt)
        last_end = we
        if re.search(r"[。！？；]$", txt) or (we - seg_start) >= 8.0:
            segments.append({"start": round(seg_start, 2),
                             "end": round(we, 2),
                             "text": "".join(buf).strip()})
            buf, seg_start = [], None
    if buf:
        segments.append({"start": round(seg_start or 0.0, 2),
                         "end": round(last_end, 2),
                         "text": "".join(buf).strip()})
    return segments, ""


def transcribe_pipeline(video_path, api_key, progress=None):
    """Extract audio + AssemblyAI transcription. Returns (segments, errmsg)."""
    if progress is None:
        def progress(x, desc=""):  # noqa: D103
            pass
    progress(0.05, "🎬 ဗီဒီယိုကို ဖတ်နေသည်...")
    info, err = validate_video_file(video_path)
    if info is None:
        return None, err
    workdir = ensure_workdir("")
    wav_path = os.path.join(workdir, "audio.wav")
    progress(0.10, "🔊 စကားပြောသံကို ရှာဖွေနေသည်...")
    err = extract_audio(video_path, wav_path)
    if err:
        return None, err
    progress(0.20, "☁️ AssemblyAI သို့ အသံ တင်နေသည်...")
    upload_url, err = aai_upload(api_key, wav_path)
    if err:
        return None, err
    tid, err = aai_request_transcript(api_key, upload_url)
    if err:
        return None, err
    progress(0.35, "📝 စာသားအဖြစ် ပြောင်းနေသည်... (ခနစောင့်ပါ)")
    # poll with progress updates
    deadline = time.time() + 600
    status, err = "processing", ""
    while time.time() < deadline:
        try:
            r = requests.get(
                AAI_BASE + "/transcript/" + tid,
                headers={"authorization": api_key}, timeout=30,
            )
        except requests.RequestException as ex:
            return None, E_NETWORK.format(detail=str(ex)[:200])
        if r.status_code != 200:
            return None, E_AAI_REQUEST.format(detail="HTTP {}".format(r.status_code))
        data = r.json()
        status = data.get("status", "")
        if status == "completed":
            break
        if status == "error":
            return None, E_AAI_FAILED.format(detail=str(data.get("error", ""))[:200])
        progress(0.45, "📝 စာသားအဖြစ် ပြောင်းနေသည်... (ခနစောင့်ပါ)")
        time.sleep(4)
    else:
        return None, E_AAI_TIMEOUT
    progress(0.70, "📝 စာကြောင်းများ ရယူနေသည်...")
    segments, _ = aai_fetch_sentences(api_key, tid)
    if not segments:
        return None, E_AAI_FAILED.format(detail="စာသား အလွတ်ပြန်ပါသည်")
    progress(1.0, "✅ ပြီးပါပြီ")
    return segments, ""

# ----------------------------------------------------------------------------
# Gemini rewrite — natural Myanmar movie-recap style
# ----------------------------------------------------------------------------

PROMPT_TEMPLATE = """You are a Myanmar movie-recap narrator for a popular YouTube recap channel.

INPUT: Chinese transcript lines of a video clip, each with a timestamp [MM:SS → MM:SS].

TASK: Write the Myanmar recap narration — ONE entry per input segment, in the SAME order.

STYLE RULES (very important):
- Natural spoken Myanmar, vivid and engaging like a human recap YouTuber.
- NOT a literal/direct translation — retell it the way a Myanmar person would narrate the scene.
- Each narration must match what is said/shown in its exact time range (chronological, beat-for-beat).
- REMOVE filler words, repeated lines, and meaningless interjections (e.g. 嗯, 啊, 哦 used alone).
- Merge nothing: keep one entry per input segment, even if the narration is short.
- Do NOT invent events that are not in the transcript.

OUTPUT FORMAT (very important):
- Output ONLY a valid JSON array. No markdown fences, no commentary, no extra text.
- Each item: {{"start": <seconds as number>, "end": <seconds as number>, "narration": "<Myanmar narration>"}}
- Keep the same start/end numbers as the input segments.

TRANSCRIPT:
{transcript_lines}
"""


def build_full_prompt(segments):
    lines = []
    for s in segments:
        lines.append("[{} → {}] {}".format(fmt_time(s["start"]), fmt_time(s["end"]), s["text"]))
    return PROMPT_TEMPLATE.replace("{transcript_lines}", "\n".join(lines))


def strip_code_fences(text):
    t = (text or "").strip()
    m = re.match(r"^```(?:json)?\s*\n?(.*?)\n?\s*```$", t, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return t


def parse_recap_json(text, video_duration):
    """Parse + validate recap JSON. Returns (segments, errmsg)."""
    cleaned = strip_code_fences(text)
    if not cleaned:
        return None, E_BAD_JSON.format(detail="စာသား အလွတ်ဖြစ်နေပါသည်")
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as ex:
        return None, E_BAD_JSON.format(detail="JSON parse error: {}".format(ex))
    except Exception as ex:  # noqa: BLE001
        return None, E_BAD_JSON.format(detail=str(ex)[:200])
    if not isinstance(data, list):
        return None, E_JSON_NOT_LIST
    if not data:
        return None, E_JSON_EMPTY
    dur = float(video_duration or 0)
    segments = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            return None, E_JSON_ITEM.format(i=i + 1)
        if "start" not in item or "end" not in item or "narration" not in item:
            return None, E_JSON_ITEM.format(i=i + 1)
        start = safe_float(item["start"], None)
        end = safe_float(item["end"], None)
        narration = item["narration"]
        if start is None or end is None or not isinstance(narration, str):
            return None, E_JSON_ITEM.format(i=i + 1)
        narration = narration.strip()
        if not narration:
            return None, E_JSON_NARR_EMPTY.format(i=i + 1)
        if not (0 <= start < end):
            return None, E_JSON_TIME.format(i=i + 1)
        if dur > 0 and start > dur:
            return None, E_JSON_TIME.format(i=i + 1)
        end = min(end, dur) if dur > 0 else end
        if end <= start:
            return None, E_JSON_TIME.format(i=i + 1)
        segments.append({"start": round(start, 2), "end": round(end, 2),
                         "narration": narration})
    segments.sort(key=lambda s: s["start"])
    for i in range(1, len(segments)):
        if segments[i]["start"] < segments[i - 1]["end"] - 0.01:
            return None, E_JSON_OVERLAP.format(i=i, j=i + 1)
    return segments, ""


def gemini_generate(api_key, prompt):
    """Generate recap JSON via Gemini API. Returns (text, errmsg)."""
    api_key = (api_key or "").strip()
    if not api_key:
        return "", E_NO_GEMINI_KEY
    try:
        import google.generativeai as genai
    except ImportError:
        return "", E_GEMINI_FAIL.format(detail="google-generativeai library မရှိပါ")
    try:
        genai.configure(api_key=api_key)
        last_err = ""
        for model_name in ("gemini-2.0-flash", "gemini-1.5-flash"):
            try:
                model = genai.GenerativeModel(model_name)
                resp = model.generate_content(prompt)
                text = (getattr(resp, "text", "") or "").strip()
                if text:
                    return text, ""
                last_err = "Gemini က စာသား အလွတ်ပြန်ပါသည်"
            except Exception as ex:  # noqa: BLE001
                last_err = str(ex)[:200]
                continue
        return "", E_GEMINI_FAIL.format(detail=last_err or "model မရပါ")
    except Exception as ex:  # noqa: BLE001
        return "", E_GEMINI_FAIL.format(detail=str(ex)[:200])


def rewrite_pipeline(transcript_segments, gemini_key, video_duration, progress=None):
    """Gemini rewrite to natural Myanmar recap style. Returns (recap, errmsg)."""
    if progress is None:
        def progress(x, desc=""):  # noqa: D103
            pass
    if not transcript_segments:
        return None, E_NO_TRANSCRIPT
    if not (gemini_key or "").strip():
        return None, E_NO_GEMINI_KEY
    progress(0.1, "🇲🇲 မူရင်းဘာသာစကားကို နားလည်နေသည်...")
    prompt = build_full_prompt(transcript_segments)
    progress(0.3, "✍️ မြန်မာလို ပြန်ရေးနေသည်...")
    raw, err = gemini_generate(gemini_key, prompt)
    if err:
        return None, err
    progress(0.8, "🔍 စာသား စစ်ဆေးနေသည်...")
    recap, verr = parse_recap_json(raw, video_duration)
    if verr:
        return None, ("⚠️ Gemini ပြန်ပေးသော JSON ပုံစံမမှန်ပါ — "
                       "ကိုယ်တိုင် ပြင်ပြီး အောက်က JSON box မှာ paste ထည့်ပါ။\n" + verr)
    progress(1.0, "✅ ပြီးပါပြီ")
    return recap, ""

# ----------------------------------------------------------------------------
# Edge TTS narration
# ----------------------------------------------------------------------------

def tts_one(text, voice_id, out_path):
    """Synthesize one narration mp3. Returns errmsg or ''."""
    try:
        import asyncio
        import edge_tts
    except ImportError:
        return "edge-tts library မရှိပါ"

    async def _run():
        comm = edge_tts.Communicate(text, voice_id)
        await comm.save(out_path)

    try:
        asyncio.run(_run())
    except RuntimeError:
        try:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(_run())
            loop.close()
        except Exception as ex:  # noqa: BLE001
            return str(ex)[:200]
    except Exception as ex:  # noqa: BLE001
        return str(ex)[:200]
    if not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
        return "အသံဖိုင် ဗလာဖြစ်နေပါသည်"
    return ""


def generate_narration(recap_segments, voice_label, workdir, progress=None):
    """Generate narration mp3 per segment (parallel). Returns (paths, msg)."""
    if progress is None:
        def progress(x, desc=""):  # noqa: D103
            pass
    if not recap_segments:
        return None, E_NO_RECAP
    voice_id = VOICES.get(voice_label, VOICES[DEFAULT_VOICE_LABEL])
    audio_dir = os.path.join(workdir, "narration")
    os.makedirs(audio_dir, exist_ok=True)
    total = len(recap_segments)
    paths = [None] * total
    failures = []

    def _gen(idx_seg):
        idx, seg = idx_seg
        text = (seg.get("narration") or "").strip()
        out_path = os.path.join(audio_dir, "seg_{:03d}.mp3".format(idx))
        if not text:
            return idx, None, True
        err = tts_one(text, voice_id, out_path)
        if err:
            return idx, None, True
        return idx, out_path, False

    workers = min(5, max(1, total))
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_gen, (i, s)): i for i, s in enumerate(recap_segments)}
        for fut in as_completed(futs):
            try:
                idx, p, failed = fut.result()
            except Exception:  # noqa: BLE001
                idx, p, failed = futs[fut], None, True
            paths[idx] = p
            if failed:
                failures.append(idx + 1)
            done += 1
            progress(done / total, "🎤 မြန်မာအသံ ဖန်တီးနေသည်... ({}/{})".format(done, total))
    progress(1.0, "✅ ပြီးပါပြီ")
    ok_count = sum(1 for p in paths if p)
    msg = OK_TTS.format(n=ok_count, total=total)
    if failures:
        msg += ("\n⚠️ အပိုင်း {} အသံမရပါ — မူရင်းအသံ သုံးပါမည်။"
                .format(", ".join(map(str, failures))))
    return paths, msg


# ----------------------------------------------------------------------------
# Render engine — mask + logo + audio mix per segment
# ----------------------------------------------------------------------------

def _even(n):
    n = int(n)
    return n if n % 2 == 0 else n + 1


def build_video_filter(seg, mask_cfg, logo_cfg, logo_input_label, w, h, fps):
    """Build video filter_complex snippet: [0:v] -> [vout].

    mask_cfg: {on, x, y, ww, hh (percent 0-100), mode: 'blur'|'cover'}
    logo_cfg: {on, path, preset, dx, dy (percent offset), size_px, opacity (0-1)}
    logo_input_label: e.g. '2:v' or None
    """
    start, end = float(seg["start"]), float(seg["end"])
    clip_dur = max(0.1, end - start)
    we, he = _even(w), _even(h)

    chain = ["trim=start={:.3f}:end={:.3f}".format(start, end), "setpts=PTS-STARTPTS"]
    # (stretch/pad handled by caller via target_dur — video base first)
    filt = "[0:v]" + ",".join(chain)
    filt += ",fps={:.2f},scale={}:{},format=yuv420p".format(fps, we, he)
    filt += "[vbase]"

    # --- subtitle mask ---
    vcur = "vbase"
    if mask_cfg.get("on"):
        mx = int(we * safe_float(mask_cfg.get("x", 0)) / 100.0)
        my = int(he * safe_float(mask_cfg.get("y", 85)) / 100.0)
        mw = max(8, int(we * safe_float(mask_cfg.get("ww", 100)) / 100.0))
        mh = max(8, int(he * safe_float(mask_cfg.get("hh", 12)) / 100.0))
        mx = max(0, min(we - mw, mx))
        my = max(0, min(he - mh, my))
        if mask_cfg.get("mode", "blur") == "cover":
            filt += (";[vbase]drawbox=x={}:y={}:w={}:h={}"
                     ":color=black@0.7:t=fill[v1]".format(mx, my, mw, mh))
        else:
            filt += (";[vbase]split[vb1][vb2];"
                     "[vb2]crop=w={}:h={}:x={}:y={},boxblur=5:2[vm];"
                     "[vb1][vm]overlay={}:{}[v1]".format(mw, mh, mx, my, mx, my))
        vcur = "v1"

    # --- logo ---
    if logo_cfg.get("on") and logo_input_label:
        size = max(24, min(400, int(logo_cfg.get("size_px", 90))))
        op = max(0.05, min(1.0, safe_float(logo_cfg.get("opacity", 0.9))))
        preset = logo_cfg.get("preset", DEFAULT_LOGO_PRESET)
        bx, by = LOGO_PRESETS.get(preset, LOGO_PRESETS[DEFAULT_LOGO_PRESET])
        ox = int(we * safe_float(logo_cfg.get("dx", 0)) / 100.0)
        oy = int(he * safe_float(logo_cfg.get("dy", 0)) / 100.0)
        x_expr = "{}{:+d}".format(bx, ox)
        y_expr = "{}{:+d}".format(by, oy)
        filt += (";[{}]format=rgba,colorchannelmixer=aa={:.2f},"
                 "scale={}:-1[logo];"
                 "[{}][logo]overlay={}:{}[vout]"
                 .format(logo_input_label, op, size, vcur, x_expr, y_expr))
        vcur = "vout"

    return filt, "[" + vcur + "]", we, he, clip_dur


def apply_stretch_pad(filter_str, vlabel_in, clip_dur, target_dur):
    """Insert stretch/tpad after [vbase] creation. Returns updated filter.

    Simpler approach: rebuild — we append setpts/tpad right after vbase label
    by replacing '[vbase]' definition. To keep it robust, the caller builds the
    base chain with stretch included. This helper is unused; stretch is applied
    in render_segment directly.
    """
    return filter_str


def render_segment(video_path, seg, nar_path, voice_upload_path,
                   logo_path, logo_cfg, mask_cfg, music_path,
                   remove_voice, vol_voice, vol_music,
                   media_info, out_path):
    """Render one segment with all effects. Returns errmsg or ''."""
    start, end = float(seg["start"]), float(seg["end"])
    clip_dur = max(0.1, end - start)
    w, h, fps = media_info["width"], media_info["height"], media_info["fps"]

    # --- narration source & target duration ---
    use_voice_upload = bool(voice_upload_path and os.path.isfile(voice_upload_path))
    nar_dur = -1
    if not use_voice_upload and nar_path and os.path.isfile(nar_path):
        nar_dur = probe_duration(nar_path)
    if nar_dur > 0:
        target_dur = max(clip_dur, nar_dur)
    else:
        target_dur = clip_dur

    # --- inputs ---
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", video_path]
    n_inputs = 1
    a_nar_idx = v_logo_idx = a_mus_idx = None
    nar_input_path = None
    if use_voice_upload:
        nar_input_path = voice_upload_path
    elif nar_path and os.path.isfile(nar_path):
        nar_input_path = nar_path
    if nar_input_path:
        cmd += ["-i", nar_input_path]
        a_nar_idx = n_inputs
        n_inputs += 1
    has_logo = bool(logo_path and os.path.isfile(logo_path) and logo_cfg.get("on"))
    if has_logo:
        cmd += ["-i", logo_path]
        v_logo_idx = n_inputs
        n_inputs += 1
    has_music = bool(music_path and os.path.isfile(music_path))
    if has_music:
        cmd += ["-stream_loop", "-1", "-i", music_path]
        a_mus_idx = n_inputs
        n_inputs += 1

    # --- video filter ---
    vfilt, vout_label, we, he, _ = build_video_filter(
        seg, mask_cfg, logo_cfg,
        ("{}:v".format(v_logo_idx) if v_logo_idx is not None else None),
        w, h, fps)

    # stretch video if narration longer (cap 2x), then pad
    stretch = target_dur / clip_dur
    stretch = min(max(stretch, 1.0), 2.0)
    stretched_dur = clip_dur * stretch
    pad_dur = max(0.0, target_dur - stretched_dur)
    # inject stretch+pad right after [vbase] is defined: rebuild prefix
    prefix = "[0:v]trim=start={:.3f}:end={:.3f},setpts=PTS-STARTPTS".format(start, end)
    if stretch > 1.001:
        prefix += ",setpts=PTS*{:.4f}".format(stretch)
    prefix += ",fps={:.2f},scale={}:{},format=yuv420p".format(fps, we, he)
    if pad_dur > 0.01:
        prefix += ",tpad=stop_mode=clone:stop_duration={:.3f}".format(pad_dur)
    prefix += "[vbase]"
    # replace the auto-generated prefix (everything up to first "[vbase]")
    vfilt = prefix + vfilt.split("[vbase]", 1)[1]

    # --- audio filter ---
    orig_vol = 0.15 if remove_voice else 1.0
    vv = max(0.0, min(1.5, safe_float(vol_voice, 1.0)))
    mv = max(0.0, min(1.0, safe_float(vol_music, 0.2)))
    af = ("[0:a]atrim=start={:.3f}:end={:.3f},asetpts=PTS-STARTPTS,"
          "aresample=44100,volume={:.2f},apad,atrim=0:{:.3f}[a0];"
          .format(start, end, orig_vol, target_dur))
    alabels = ["[a0]"]
    if a_nar_idx is not None:
        if use_voice_upload:
            af += ("[{}:a]atrim=start={:.3f}:end={:.3f},asetpts=PTS-STARTPTS,"
                   "aresample=44100,volume={:.2f},apad,atrim=0:{:.3f}[a1];"
                   .format(a_nar_idx, start, end, vv, target_dur))
        else:
            af += ("[{}:a]aresample=44100,volume={:.2f},apad,atrim=0:{:.3f}[a1];"
                   .format(a_nar_idx, vv, target_dur))
        alabels.append("[a1]")
    if a_mus_idx is not None and mv > 0.001:
        af += ("[{}:a]atrim=start={:.3f}:end={:.3f},asetpts=PTS-STARTPTS,"
               "aresample=44100,volume={:.2f},apad,atrim=0:{:.3f}[a2];"
               .format(a_mus_idx, start, start + target_dur, mv, target_dur))
        alabels.append("[a2]")
    if len(alabels) == 1:
        af += "[a0]anull[aout]"
    else:
        af += "".join(alabels) + "amix=inputs={}:duration=first:dropout_transition=0[aout]".format(len(alabels))

    filt = vfilt + ";" + af
    cmd += ["-filter_complex", filt,
            "-map", vout_label, "-map", "[aout]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-t", "{:.3f}".format(target_dur),
            out_path]
    ok, _, err = run_cmd(cmd, timeout=900)
    if not ok or not os.path.isfile(out_path):
        return E_FFMPEG_FAIL.format(detail=(err or "segment render")[:300])
    return ""


def concat_segments(seg_paths, out_path):
    """Concatenate rendered segments (stream copy). Returns errmsg or ''."""
    if not seg_paths:
        return E_FFMPEG_FAIL.format(detail="render လုပ်ထားသော အပိုင်း မရှိပါ")
    lst = os.path.join(os.path.dirname(out_path), "concat.txt")
    try:
        with open(lst, "w", encoding="utf-8") as f:
            for p in seg_paths:
                f.write("file '{}'\n".format(p))
    except Exception as ex:  # noqa: BLE001
        return E_FFMPEG_FAIL.format(detail=str(ex)[:200])
    ok, _, err = run_cmd([
        "ffmpeg", "-y", "-v", "error",
        "-f", "concat", "-safe", "0", "-i", lst,
        "-c", "copy", "-movflags", "+faststart", out_path,
    ], timeout=600)
    if not ok or not os.path.isfile(out_path):
        return E_FFMPEG_FAIL.format(detail=(err or "concat")[:300])
    return ""


def render_final(video_path, recap_segments, narration_paths, voice_upload_path,
                 logo_path, logo_cfg, mask_cfg, music_path,
                 remove_voice, vol_voice, vol_music,
                 media_info, workdir, progress=None):
    """Render all segments + concat. Returns (final_path, msg).

    NOTE: `total` is defined here (a previous version missed it and crashed).
    """
    if progress is None:
        def progress(x, desc=""):  # noqa: D103
            pass
    if not recap_segments:
        return None, E_NO_RECAP
    total = len(recap_segments)
    if total == 0:
        return None, E_NO_RECAP

    seg_dir = os.path.join(workdir, "segments")
    os.makedirs(seg_dir, exist_ok=True)
    seg_paths = [None] * total
    render_err = [None]

    def _render_one(i):
        seg = recap_segments[i]
        out_path = os.path.join(seg_dir, "seg_{:03d}.mp4".format(i))
        nar = narration_paths[i] if narration_paths and i < len(narration_paths) else None
        err = render_segment(
            video_path, seg, nar, voice_upload_path,
            logo_path, logo_cfg, mask_cfg, music_path,
            remove_voice, vol_voice, vol_music,
            media_info, out_path)
        return i, out_path, err

    done = 0
    with ThreadPoolExecutor(max_workers=2) as ex:
        futs = {ex.submit(_render_one, i): i for i in range(total)}
        for fut in as_completed(futs):
            try:
                i, out_path, err = fut.result()
            except Exception as e:  # noqa: BLE001
                i, out_path, err = futs[fut], None, str(e)
            if err:
                render_err[0] = "အပိုင်း {}: {}".format(i + 1, err)
                for f in futs:
                    f.cancel()
                break
            seg_paths[i] = out_path
            done += 1
            progress(0.05 + 0.85 * done / total,
                     "🎞️ ဗီဒီယိုကို ပြန်လည်တည်ဆောက်နေသည်... ({}/{})".format(done, total))
    if render_err[0]:
        return None, render_err[0]
    progress(0.92, "🔗 အပိုင်းများ ဆက်နေသည်...")
    final_path = os.path.join(workdir, "recap_final.mp4")
    err = concat_segments(seg_paths, final_path)
    if err:
        return None, err
    progress(1.0, "✅ ပြီးပါပြီ")
    msg = OK_RENDER
    try:
        size_mb = os.path.getsize(final_path) / 1e6
        dur = probe_duration(final_path)
        msg += "\n📁 {:.1f} MB · ⏱ {:.1f} စက္ကန့်".format(size_mb, dur)
    except Exception:  # noqa: BLE001
        pass
    return final_path, msg


def render_preview_segment(video_path, seg, nar_path, voice_upload_path,
                           logo_path, logo_cfg, mask_cfg, music_path,
                           remove_voice, vol_voice, vol_music,
                           media_info, workdir):
    """Render just one segment as a quick preview. Returns (path, errmsg)."""
    out_path = os.path.join(workdir, "preview.mp4")
    err = render_segment(
        video_path, seg, nar_path, voice_upload_path,
        logo_path, logo_cfg, mask_cfg, music_path,
        remove_voice, vol_voice, vol_music,
        media_info, out_path)
    if err:
        return None, err
    return out_path, ""

# ----------------------------------------------------------------------------
# Gradio UI — mobile-first cinema theme
# ----------------------------------------------------------------------------

CSS = """
.gradio-container {
  background: #0a0a0f !important;
  color: #f5f2e9 !important;
  font-family: "Noto Sans Myanmar", ui-sans-serif, system-ui, sans-serif !important;
  max-width: 700px !important;
  margin: 0 auto !important;
  padding: 0 12px 40px !important;
}
.cinema-header {
  text-align: center;
  padding: 30px 16px 20px;
  background: linear-gradient(180deg, #241314 0%, #0a0a0f 100%);
  border-bottom: 3px solid #f5c518;
  margin: 0 -12px 18px;
}
.cinema-header .logo { font-size: 56px; line-height: 1; }
.cinema-header h1 {
  color: #f5c518 !important; font-size: 30px !important; font-weight: 900 !important;
  letter-spacing: 1px !important; margin: 8px 0 4px !important;
}
.cinema-header p { color: #b8b0a0 !important; font-size: 14px !important; margin: 0 !important; }
.main-title {
  text-align: center; font-size: 26px !important; font-weight: 800 !important;
  color: #ffffff !important; margin: 6px 0 18px !important;
}
.section-card {
  background: #14141c !important;
  border: 1px solid #2b2b36 !important;
  border-radius: 18px !important;
  padding: 18px !important;
  margin-bottom: 16px !important;
}
.section-card h3 {
  color: #f5c518 !important; font-size: 18px !important; font-weight: 800 !important;
  margin: 0 0 12px !important;
}
.section-card .hint { color: #9a937f !important; font-size: 13px !important; margin: -6px 0 12px !important; }
button {
  min-height: 56px !important;
  font-size: 17px !important;
  font-weight: 700 !important;
  border-radius: 14px !important;
}
.big-action button {
  min-height: 78px !important;
  font-size: 22px !important;
  font-weight: 900 !important;
  border-radius: 18px !important;
  background: linear-gradient(135deg, #f5c518 0%, #d99400 100%) !important;
  color: #1a1405 !important;
  border: none !important;
  box-shadow: 0 6px 24px rgba(245,197,24,.35) !important;
}
.big-action button:disabled { opacity: .55 !important; box-shadow: none !important; }
button.primary {
  background: #f5c518 !important; color: #1a1405 !important; border: none !important;
}
.status-box {
  background: #0e0e14 !important;
  border: 1px solid #2b2b36 !important;
  border-radius: 12px !important;
  padding: 12px 14px !important;
  white-space: pre-wrap !important;
  font-size: 14px !important;
  color: #f5f2e9 !important;
}
.gr-input, .gr-textbox, textarea, input[type=text], input[type=password] {
  border-radius: 12px !important;
  background: #0e0e14 !important;
  border: 1px solid #2b2b36 !important;
  color: #f5f2e9 !important;
  font-size: 16px !important;
}
label { color: #cfc8b4 !important; font-size: 15px !important; }
footer { display: none !important; }
.gr-accordion { border-radius: 14px !important; }
"""

HEADER_HTML = """
<div class="cinema-header">
  <div class="logo">🎬</div>
  <h1>Recap Cinema</h1>
  <p>မြန်မာ Movie Recap ဖန်တီးရန် စတူဒီယို</p>
</div>
<div class="main-title">🎞️ Movie Recap ဖန်တီးရန်</div>
"""


def new_state():
    return {
        "video": "", "workdir": "", "duration": 0.0,
        "w": 0, "h": 0, "fps": 30.0,
        "transcript": [], "recap": [], "narration": [],
        "logo": "", "voice_upload": "", "music": "",
    }


def seg_rows(recap):
    rows = []
    for i, s in enumerate(recap):
        rows.append([my_num(i + 1), s.get("narration", "")])
    return rows


def build_ui():
    import gradio as gr

    with gr.Blocks(title="Recap Cinema — မြန်မာ Movie Recap", css=CSS) as demo:
        gr.HTML(HEADER_HTML)
        st = gr.State(new_state())

        # ============ 1. Upload ============
        with gr.Group(elem_classes=["section-card"]):
            gr.HTML("<h3>① ဗီဒီယို တင်ရန်</h3>")
            video_in = gr.Video(label="ဗီဒီယိုထည့်ရန် (MP4 / MOV / WEBM, ၁၀ မိနစ်အထိ)",
                                sources=["upload"])
            info_md = gr.Markdown("", elem_classes=["status-box"])
            status_up = gr.Markdown("", elem_classes=["status-box"])

        # ============ API keys ============
        with gr.Accordion("🔑 API Keys (AssemblyAI + Gemini)", open=False,
                          elem_classes=["section-card"]):
            gr.HTML("<p class='hint'>Key များကို ဒီမှာ ထည့်ပါ။ Code ထဲမှာ သိမ်းမထားပါ။ "
                    "ASSEMBLYAI_API_KEY / GEMINI_API_KEY environment variable လည်း သုံးနိုင်ပါသည်။</p>")
            aai_key = gr.Textbox(label="AssemblyAI API Key (စာသားထုတ်ရန်)",
                                 type="password", placeholder="API key ထည့်ပါ…")
            gemini_key = gr.Textbox(label="Gemini API Key (မြန်မာလို ပြန်ရေးရန်)",
                                    type="password", placeholder="API key ထည့်ပါ…")

        # ============ 2. Transcribe + rewrite ============
        with gr.Group(elem_classes=["section-card"]):
            gr.HTML("<h3>② စာသား ထုတ် + မြန်မာလို ပြန်ရေး</h3>"
                    "<p class='hint'>AI က စကားပြောသံ ရှာမယ် → စာသားပြောင်းမယ် → "
                    "သဘာဝကျတဲ့ မြန်မာ recap ဟန်နဲ့ ပြန်ရေးမယ်</p>")
            btn_transcribe = gr.Button("🎙️ စာသားထုတ် + မြန်မာလိုပြန်မည်", variant="primary")
            status_tr = gr.Markdown("အဆင်သင့်။", elem_classes=["status-box"])
            json_paste = gr.Textbox(
                label="Recap JSON ကိုယ်တိုင်ထည့်ရန် (Gemini key မရှိပါက)",
                lines=4, max_lines=8,
                placeholder='[{"start": 0, "end": 5.2, "narration": "…"}]')
            btn_json = gr.Button("📥 JSON ထည့်မည်")

        # ============ 3. Edit segments ============
        with gr.Group(elem_classes=["section-card"]):
            gr.HTML("<h3>③ မြန်မာစာသား ပြင်ဆင်ရန်</h3>"
                    "<p class='hint'>စာသားကို နှိပ်၍ ကိုယ်တိုင် ပြင်နိုင်ပါသည်</p>")
            seg_df = gr.Dataframe(headers=["အပိုင်း", "မြန်မာစာသား"],
                                  label="Recap စာသားများ",
                                  interactive=True, wrap=True, row_count=0)
            btn_save_edits = gr.Button("✏️ ပြင်ဆင်ချက်များ သိမ်းမည်")
            status_ed = gr.Markdown("", elem_classes=["status-box"])

        # ============ 4. Subtitle mask ============
        with gr.Group(elem_classes=["section-card"]):
            gr.HTML("<h3>④ စာတန်းဖယ်ရန်</h3>"
                    "<p class='hint'>ဗီဒီယိုပေါ်က မူရင်းစာတန်း နေရာကို slider နဲ့ ရွေးပါ</p>")
            mask_on = gr.Checkbox(value=True, label="စာတန်းဖယ်ရန် ဖွင့်ထားမည်")
            mask_mode = gr.Radio(choices=["🫧 Blur (မှုန်ဝါးစေမည်)", "⬛ Cover (ဖုံးမည်)"],
                                 value="🫧 Blur (မှုန်ဝါးစေမည်)", label="ပုံစံ")
            mask_y = gr.Slider(0, 100, value=85, step=1, label="အပေါ်/အောက် နေရာ (%)")
            mask_x = gr.Slider(0, 100, value=0, step=1, label="ဘယ်/ညာ နေရာ (%)")
            mask_w = gr.Slider(10, 100, value=100, step=1, label="အကျယ် (%)")
            mask_h = gr.Slider(4, 40, value=14, step=1, label="အမြင့် (%)")

        # ============ 5. Logo ============
        with gr.Group(elem_classes=["section-card"]):
            gr.HTML("<h3>⑤ လိုဂို</h3>")
            logo_in = gr.Image(label="လိုဂို တင်ရန် (PNG / JPG / WEBP)",
                               sources=["upload"], type="filepath")
            logo_preset = gr.Radio(choices=list(LOGO_PRESETS.keys()),
                                   value=DEFAULT_LOGO_PRESET, label="နေရာ")
            logo_dx = gr.Slider(-20, 20, value=0, step=1, label="ဘယ်/ညာ ချိန်ညှိရန် (%)")
            logo_dy = gr.Slider(-20, 20, value=0, step=1, label="အပေါ်/အောက် ချိန်ညှိရန် (%)")
            logo_size = gr.Slider(24, 200, value=90, step=2, label="အရွယ်အစား (px)")
            logo_op = gr.Slider(10, 100, value=90, step=5, label="Opacity (%)")
            status_logo = gr.Markdown("", elem_classes=["status-box"])

        # ============ 6. Original voice ============
        with gr.Group(elem_classes=["section-card"]):
            gr.HTML("<h3>⑥ မူရင်းစကားပြောသံ</h3>")
            rm_voice = gr.Checkbox(value=True,
                                   label="မူရင်းစကားပြောသံ ဖယ်ရှားရန် (နောက်ခံအသံကို 15% ဖြင့် ထိန်းသိမ်းမည်)")

        # ============ 7. Myanmar voice ============
        with gr.Group(elem_classes=["section-card"]):
            gr.HTML("<h3>⑦ မြန်မာအသံ ထည့်ရန်</h3>")
            voice_mode = gr.Radio(choices=["🤖 AI အသံ", "🎤 ကိုယ်ပိုင်အသံ တင်မည်"],
                                  value="🤖 AI အသံ", label="အသံ အမျိုးအစား")
            with gr.Group(visible=True) as voice_ai_box:
                voice_dd = gr.Dropdown(choices=list(VOICES.keys()),
                                       value=DEFAULT_VOICE_LABEL,
                                       label="AI အသံ ရွေးပါ")
            with gr.Group(visible=False) as voice_up_box:
                voice_up = gr.Audio(label="ကိုယ်ပိုင်အသံ (MP3 / WAV) — ဗီဒီယိုတစ်လျှောက် သုံးမည်",
                                    sources=["upload"], type="filepath")
            status_voice = gr.Markdown("", elem_classes=["status-box"])

        # ============ 8. Background music ============
        with gr.Group(elem_classes=["section-card"]):
            gr.HTML("<h3>⑧ နောက်ခံတေးဂီတ (ရွေးချယ်ခွင့်)</h3>")
            music_up = gr.Audio(label="တေးဂီတ တင်ရန် (MP3 / WAV) — မထည့်လဲ ရပါသည်",
                                sources=["upload"], type="filepath")
            vol_voice = gr.Slider(20, 150, value=100, step=5, label="🎤 အသံ အတိုးအကျယ် (%)")
            vol_music = gr.Slider(0, 100, value=20, step=5, label="🎵 တေးဂီတ အတိုးအကျယ် (%)")

        # ============ 9. Preview ============
        with gr.Group(elem_classes=["section-card"]):
            gr.HTML("<h3>⑨ အစမ်းကြည့်ရန်</h3>"
                    "<p class='hint'>ပထမအပိုင်းကို အသံ + လိုဂို + စာတန်းဖယ် + တေးဂီတ ဖြင့် အစမ်းပြမည်</p>")
            btn_preview = gr.Button("👁️ အစမ်းကြည့်မည်")
            preview_vid = gr.Video(label="Preview")
            status_pv = gr.Markdown("", elem_classes=["status-box"])

        # ============ 10. Final ============
        with gr.Group(elem_classes=["section-card"]):
            gr.HTML("<h3>⑩ အပြီးသတ် ဗီဒီယို</h3>")
            with gr.Group(elem_classes=["big-action"]):
                btn_big = gr.Button("🎬 မြန်မာ Movie Recap ပြုလုပ်မည်")
            status_big = gr.Markdown("အဆင်သင့်။", elem_classes=["status-box"])
            final_vid = gr.Video(label="အပြီးသတ် ဗီဒီယို")
            final_file = gr.File(label="💾 ဗီဒီယိုသိမ်းရန်")

        # ================= handlers =================

        def ui_upload(video, state):
            state = dict(state)
            info, err = validate_video_file(video)
            if info is None:
                return state, "", err
            wd = ensure_workdir(state.get("workdir"))
            ext = os.path.splitext(video)[1].lower()
            dst = os.path.join(wd, "source" + ext)
            try:
                if os.path.abspath(video) != os.path.abspath(dst):
                    shutil.copy(video, dst)
            except Exception:  # noqa: BLE001
                dst = video
            state.update({"video": dst, "workdir": wd,
                          "duration": info["duration"],
                          "w": info["width"], "h": info["height"],
                          "fps": info["fps"]})
            info_md = ("📹 {}×{} · {:.1f} fps · ⏱ {}"
                       .format(info["width"], info["height"],
                               info["fps"], fmt_time(info["duration"])))
            return state, info_md, OK_VIDEO.format(dur=info["duration"])

        video_in.change(ui_upload, inputs=[video_in, st],
                        outputs=[st, info_md, status_up])

        def ui_transcribe(aai_k, gem_k, state, progress=gr.Progress(track_tqdm=True)):
            state = dict(state)
            aai_key = get_api_key(aai_k, "ASSEMBLYAI_API_KEY")
            gem_key = get_api_key(gem_k, "GEMINI_API_KEY")
            if not aai_key:
                yield {status_tr: E_NO_AAI_KEY, st: state,
                       seg_df: gr.update()}
                return
            if not state.get("video"):
                yield {status_tr: E_NO_VIDEO, st: state, seg_df: gr.update()}
                return
            yield {btn_transcribe: gr.update(interactive=False, value="⏳ လုပ်ဆောင်နေသည်..."),
                   status_tr: "🎬 ဗီဒီယိုကို ဖတ်နေသည်..."}
            try:
                segments, err = transcribe_pipeline(state["video"], aai_key, progress)
                if err:
                    yield {btn_transcribe: gr.update(interactive=True,
                                                     value="🎙️ စာသားထုတ် + မြန်မာလိုပြန်မည်"),
                           status_tr: err, st: state, seg_df: gr.update()}
                    return
                state["transcript"] = segments
                wd = ensure_workdir(state.get("workdir"))
                state["workdir"] = wd
                yield {status_tr: "✍️ မြန်မာလို ပြန်ရေးနေသည်..."}
                recap, err = rewrite_pipeline(segments, gem_key, state["duration"], progress)
                if err:
                    # transcription ok, rewrite failed — keep transcript, let user paste JSON
                    msg = OK_TRANSCRIBE.format(n=len(segments)) + "\n\n" + err
                    yield {btn_transcribe: gr.update(interactive=True,
                                                     value="🎙️ စာသားထုတ် + မြန်မာလိုပြန်မည်"),
                           status_tr: msg, st: state, seg_df: gr.update()}
                    return
                state["recap"] = recap
                state["narration"] = []
                yield {btn_transcribe: gr.update(interactive=True,
                                                 value="🎙️ စာသားထုတ် + မြန်မာလိုပြန်မည်"),
                       status_tr: OK_REWRITE.format(n=len(recap)),
                       st: state, seg_df: gr.update(value=seg_rows(recap))}
            except Exception:  # noqa: BLE001
                yield {btn_transcribe: gr.update(interactive=True,
                                                 value="🎙️ စာသားထုတ် + မြန်မာလိုပြန်မည်"),
                       status_tr: "❌ မမျှော်လင့်သော အမှား:\n{}".format(traceback.format_exc()[:500]),
                       st: state, seg_df: gr.update()}

        btn_transcribe.click(ui_transcribe,
                             inputs=[aai_key, gemini_key, st],
                             outputs=[btn_transcribe, status_tr, st, seg_df])

        def ui_paste_json(txt, state):
            state = dict(state)
            if not state.get("video"):
                return state, gr.update(), E_NO_VIDEO
            segs, err = parse_recap_json(txt, state.get("duration", 0))
            if err:
                return state, gr.update(), err
            state["recap"] = segs
            state["narration"] = []
            return state, gr.update(value=seg_rows(segs)), OK_REWRITE.format(n=len(segs))

        btn_json.click(ui_paste_json, inputs=[json_paste, st],
                       outputs=[st, seg_df, status_tr])

        def ui_save_edits(rows, state):
            state = dict(state)
            recap = state.get("recap") or []
            if not rows:
                return state, gr.update(), E_JSON_EMPTY
            if len(rows) != len(recap):
                return state, gr.update(), "❌ အပိုင်းအရေအတွက် မကိုက်ပါ ({} ရသင့်သည်)".format(len(recap))
            for i, r in enumerate(rows):
                try:
                    txt = str(r[1] if len(r) > 1 else "").strip()
                except (TypeError, IndexError):
                    txt = ""
                if not txt:
                    return state, gr.update(), E_JSON_NARR_EMPTY.format(i=i + 1)
                recap[i]["narration"] = txt
            state["recap"] = recap
            state["narration"] = []  # edits invalidate old narration
            return state, gr.update(value=seg_rows(recap)), "✅ ပြင်ဆင်ချက်များ သိမ်းပြီးပါပြီ။"

        btn_save_edits.click(ui_save_edits, inputs=[seg_df, st],
                             outputs=[st, seg_df, status_ed])

        def ui_logo(img, state):
            state = dict(state)
            if not isinstance(img, str) or not img:
                state["logo"] = ""
                return state, "လိုဂို မရွေးထားပါ။"
            ext = os.path.splitext(img)[1].lower()
            if ext not in ALLOWED_LOGO_EXTS:
                return state, E_LOGO_BAD
            wd = ensure_workdir(state.get("workdir"))
            state["workdir"] = wd
            dst = os.path.join(wd, "logo" + ext)
            try:
                shutil.copy(img, dst)
            except Exception as ex:  # noqa: BLE001
                return state, "❌ လိုဂို သိမ်းမရပါ: {}".format(ex)
            state["logo"] = dst
            return state, "✅ လိုဂို လက်ခံပါသည်"

        logo_in.change(ui_logo, inputs=[logo_in, st], outputs=[st, status_logo])

        def _toggle_voice(mode):
            is_ai = mode.startswith("🤖")
            return gr.update(visible=is_ai), gr.update(visible=not is_ai)

        voice_mode.change(_toggle_voice, inputs=[voice_mode],
                          outputs=[voice_ai_box, voice_up_box])

        def ui_voice_upload(audio, state):
            state = dict(state)
            if not isinstance(audio, str) or not audio:
                state["voice_upload"] = ""
                return state, "ကိုယ်ပိုင်အသံ မရွေးထားပါ။"
            err = validate_audio_file(audio, "ကိုယ်ပိုင်အသံ")
            if err:
                return state, err
            wd = ensure_workdir(state.get("workdir"))
            state["workdir"] = wd
            dst = os.path.join(wd, "voice_upload" + os.path.splitext(audio)[1].lower())
            try:
                shutil.copy(audio, dst)
            except Exception as ex:  # noqa: BLE001
                return state, "❌ အသံဖိုင် သိမ်းမရပါ: {}".format(ex)
            state["voice_upload"] = dst
            return state, "✅ ကိုယ်ပိုင်အသံ လက်ခံပါသည်"

        voice_up.change(ui_voice_upload, inputs=[voice_up, st],
                        outputs=[st, status_voice])

        def ui_music_upload(audio, state):
            state = dict(state)
            if not isinstance(audio, str) or not audio:
                state["music"] = ""
                return state, ""
            err = validate_audio_file(audio, "နောက်ခံတေးဂီတ")
            if err:
                return state, err
            wd = ensure_workdir(state.get("workdir"))
            state["workdir"] = wd
            dst = os.path.join(wd, "music" + os.path.splitext(audio)[1].lower())
            try:
                shutil.copy(audio, dst)
            except Exception as ex:  # noqa: BLE001
                return state, "❌ သိမ်းမရပါ: {}".format(ex)
            state["music"] = dst
            return state, "✅ တေးဂီတ လက်ခံပါသည်"

        music_up.change(ui_music_upload, inputs=[music_up, st],
                        outputs=[st, status_voice])

        def _cfgs(mask_on_v, mask_mode_v, mx, my, mw, mh,
                  logo_preset_v, ldx, ldy, lsz, lop,
                  rm_v, vmode, vlabel, vvol, mvol, state):
            mask_cfg = {
                "on": bool(mask_on_v),
                "mode": "cover" if str(mask_mode_v).startswith("⬛") else "blur",
                "x": safe_float(mx), "y": safe_float(my),
                "ww": safe_float(mw), "hh": safe_float(mh),
            }
            logo_cfg = {
                "on": bool(state.get("logo")),
                "preset": logo_preset_v, "dx": safe_float(ldx),
                "dy": safe_float(ldy), "size_px": int(lsz),
                "opacity": safe_float(lop) / 100.0,
            }
            return mask_cfg, logo_cfg, bool(rm_v), vmode, vlabel, \
                safe_float(vvol) / 100.0, safe_float(mvol) / 100.0

        def ui_preview(mask_on_v, mask_mode_v, mx, my, mw, mh,
                       logo_preset_v, ldx, ldy, lsz, lop,
                       rm_v, vmode, vlabel, vvol, mvol, state,
                       progress=gr.Progress(track_tqdm=True)):
            state = dict(state)
            if not state.get("video"):
                yield {status_pv: E_NO_VIDEO, preview_vid: None}
                return
            recap = state.get("recap") or []
            if not recap:
                yield {status_pv: E_NO_RECAP, preview_vid: None}
                return
            mask_cfg, logo_cfg, rm_voice, _, _, vv, mv = _cfgs(
                mask_on_v, mask_mode_v, mx, my, mw, mh,
                logo_preset_v, ldx, ldy, lsz, lop,
                rm_v, vmode, vlabel, vvol, mvol, state)
            yield {btn_preview: gr.update(interactive=False, value="⏳ ..."),
                   status_pv: "🎤 အစမ်းအသံ ဖန်တီးနေသည်..."}
            try:
                wd = ensure_workdir(state.get("workdir"))
                state["workdir"] = wd
                use_own = str(vmode).startswith("🎤")
                voice_up_path = state.get("voice_upload") if use_own else ""
                nar_path = None
                if not use_own:
                    seg0 = recap[0]
                    tmp = os.path.join(wd, "preview_nar.mp3")
                    voice_id = VOICES.get(vlabel, VOICES[DEFAULT_VOICE_LABEL])
                    err = tts_one(seg0["narration"], voice_id, tmp)
                    if err:
                        yield {btn_preview: gr.update(interactive=True, value="👁️ အစမ်းကြည့်မည်"),
                               status_pv: E_TTS_FAIL.format(detail=err), preview_vid: None}
                        return
                    nar_path = tmp
                elif not voice_up_path:
                    yield {btn_preview: gr.update(interactive=True, value="👁️ အစမ်းကြည့်မည်"),
                           status_pv: "❌ ကိုယ်ပိုင်အသံဖိုင် တင်ပေးပါ။", preview_vid: None}
                    return
                yield {status_pv: "🎞️ Preview render နေသည်..."}
                media_info = {"duration": state["duration"], "width": state["w"],
                              "height": state["h"], "fps": state["fps"]}
                ppath, err = render_preview_segment(
                    state["video"], recap[0], nar_path, voice_up_path,
                    state.get("logo"), logo_cfg, mask_cfg, state.get("music") or "",
                    rm_voice, vv, mv, media_info, wd)
                yield {btn_preview: gr.update(interactive=True, value="👁️ အစမ်းကြည့်မည်"),
                       status_pv: "✅ Preview အဆင်သင့်" if not err else err,
                       preview_vid: ppath, st: state}
            except Exception:  # noqa: BLE001
                yield {btn_preview: gr.update(interactive=True, value="👁️ အစမ်းကြည့်မည်"),
                       status_pv: "❌ မမျှော်လင့်သော အမှား:\n{}".format(traceback.format_exc()[:500]),
                       preview_vid: None}

        btn_preview.click(
            ui_preview,
            inputs=[mask_on, mask_mode, mask_x, mask_y, mask_w, mask_h,
                    logo_preset, logo_dx, logo_dy, logo_size, logo_op,
                    rm_voice, voice_mode, voice_dd, vol_voice, vol_music, st],
            outputs=[btn_preview, status_pv, preview_vid, st])

        def ui_big(aai_k, gem_k,
                   mask_on_v, mask_mode_v, mx, my, mw, mh,
                   logo_preset_v, ldx, ldy, lsz, lop,
                   rm_v, vmode, vlabel, vvol, mvol, state,
                   progress=gr.Progress(track_tqdm=True)):
            state = dict(state)
            if not state.get("video"):
                yield {status_big: E_NO_VIDEO}
                return
            recap = state.get("recap") or []
            if not recap:
                yield {status_big: E_NO_RECAP}
                return
            mask_cfg, logo_cfg, rm_voice, _, _, vv, mv = _cfgs(
                mask_on_v, mask_mode_v, mx, my, mw, mh,
                logo_preset_v, ldx, ldy, lsz, lop,
                rm_v, vmode, vlabel, vvol, mvol, state)
            use_own = str(vmode).startswith("🎤")
            voice_up_path = state.get("voice_upload") if use_own else ""
            if use_own and not voice_up_path:
                yield {status_big: "❌ ကိုယ်ပိုင်အသံဖိုင် တင်ပေးပါ (သို့မဟုတ် 🤖 AI အသံ ရွေးပါ)။"}
                return
            yield {btn_big: gr.update(interactive=False, value="⏳ လုပ်ဆောင်နေသည်..."),
                   status_big: "🎬 ဗီဒီယိုကို ဖတ်နေသည်..."}
            try:
                wd = ensure_workdir(state.get("workdir"))
                state["workdir"] = wd
                # --- TTS (AI mode only) ---
                nar_paths = []
                if not use_own:
                    yield {status_big: "🎤 မြန်မာအသံ ဖန်တီးနေသည်..."}
                    paths, msg = generate_narration(recap, vlabel, wd, progress)
                    if paths is None:
                        yield {btn_big: gr.update(interactive=True,
                                                  value="🎬 မြန်မာ Movie Recap ပြုလုပ်မည်"),
                               status_big: msg}
                        return
                    nar_paths = paths
                    yield {status_big: msg + "\n🎞️ ဗီဒီယိုကို ပြန်လည်တည်ဆောက်နေသည်..."}
                else:
                    yield {status_big: "🎞️ ဗီဒီယိုကို ပြန်လည်တည်ဆောက်နေသည်..."}
                media_info = {"duration": state["duration"], "width": state["w"],
                              "height": state["h"], "fps": state["fps"]}
                final_path, msg = render_final(
                    state["video"], recap, nar_paths, voice_up_path,
                    state.get("logo"), logo_cfg, mask_cfg, state.get("music") or "",
                    rm_voice, vv, mv, media_info, wd, progress)
                if final_path is None:
                    yield {btn_big: gr.update(interactive=True,
                                              value="🎬 မြန်မာ Movie Recap ပြုလုပ်မည်"),
                           status_big: msg}
                    return
                state["final"] = final_path
                yield {btn_big: gr.update(interactive=True,
                                          value="🎬 မြန်မာ Movie Recap ပြုလုပ်မည်"),
                       status_big: msg, final_vid: final_path,
                       final_file: final_path, st: state}
            except Exception:  # noqa: BLE001
                yield {btn_big: gr.update(interactive=True,
                                          value="🎬 မြန်မာ Movie Recap ပြုလုပ်မည်"),
                       status_big: "❌ မမျှော်လင့်သော အမှား:\n{}".format(traceback.format_exc()[:500])}

        btn_big.click(
            ui_big,
            inputs=[aai_key, gemini_key,
                    mask_on, mask_mode, mask_x, mask_y, mask_w, mask_h,
                    logo_preset, logo_dx, logo_dy, logo_size, logo_op,
                    rm_voice, voice_mode, voice_dd, vol_voice, vol_music, st],
            outputs=[btn_big, status_big, final_vid, final_file, st])

    return demo


# ----------------------------------------------------------------------------
# Self-tests
# ----------------------------------------------------------------------------

def run_tests():
    """Core logic unit tests. Raises AssertionError on failure."""
    # my_num
    assert my_num(1) == "၁", my_num(1)
    assert my_num(12) == "၁၂", my_num(12)
    assert my_num(0) == "၀"

    # parse_recap_json — valid
    good = json.dumps([
        {"start": 0, "end": 5.2, "narration": "စတင်ပါပြီ"},
        {"start": 5.2, "end": 10, "narration": "ဆက်ဖြစ်ပါတယ်"},
    ])
    segs, err = parse_recap_json(good, 60)
    assert err == "" and len(segs) == 2, err
    assert segs[0]["narration"] == "စတင်ပါပြီ"

    # with code fences
    segs, err = parse_recap_json("```json\n" + good + "\n```", 60)
    assert err == "" and len(segs) == 2, err

    # invalid: not a list
    _, err = parse_recap_json('{"a": 1}', 60)
    assert err != ""
    # invalid: empty narration
    _, err = parse_recap_json(json.dumps([{"start": 0, "end": 1, "narration": " "}]), 60)
    assert err != ""
    # invalid: overlap
    _, err = parse_recap_json(json.dumps([
        {"start": 0, "end": 5, "narration": "a"},
        {"start": 4, "end": 8, "narration": "b"},
    ]), 60)
    assert err != ""
    # invalid: bad times
    _, err = parse_recap_json(json.dumps([{"start": 9, "end": 5, "narration": "a"}]), 60)
    assert err != ""

    # build_full_prompt keeps timestamps
    segs_in = [{"start": 0.0, "end": 5.2, "text": "你好"}]
    p = build_full_prompt(segs_in)
    assert "00:00.00" in p and "你好" in p and "JSON array" in p

    # video filter builder — mask blur + logo
    seg = {"start": 0, "end": 5, "narration": "test"}
    mask_cfg = {"on": True, "mode": "blur", "x": 0, "y": 85, "ww": 100, "hh": 14}
    logo_cfg = {"on": True, "preset": DEFAULT_LOGO_PRESET, "dx": 0, "dy": 0,
                "size_px": 90, "opacity": 0.9}
    vfilt, vlabel, we, he, cd = build_video_filter(seg, mask_cfg, logo_cfg, "2:v", 720, 1280, 30)
    assert "boxblur" in vfilt and "overlay" in vfilt and "colorchannelmixer" in vfilt, vfilt
    assert vlabel == "[vout]" and we == 720 and he == 1280

    # mask cover mode, no logo
    mask_cfg2 = dict(mask_cfg, mode="cover")
    logo_cfg2 = dict(logo_cfg, on=False)
    vfilt2, vlabel2, _, _, _ = build_video_filter(seg, mask_cfg2, logo_cfg2, None, 721, 1281, 30)
    assert "drawbox" in vfilt2 and vlabel2 == "[v1]", vfilt2
    # odd dims get evened
    assert "scale=722:1282" in vfilt2, vfilt2

    # mask off entirely
    vfilt3, vlabel3, _, _, _ = build_video_filter(
        seg, {"on": False}, {"on": False}, None, 640, 480, 30)
    assert vlabel3 == "[vbase]" and "boxblur" not in vfilt3 and "drawbox" not in vfilt3

    # logo presets all render
    for name in LOGO_PRESETS:
        lc = dict(logo_cfg, preset=name)
        f, _, _, _, _ = build_video_filter(seg, {"on": False}, lc, "2:v", 640, 480, 30)
        assert "overlay=" in f, name

    # API key helper: textbox wins, env fallback
    os.environ["RECAP_TEST_KEY"] = "env-key"
    assert get_api_key("  box-key ", "RECAP_TEST_KEY") == "box-key"
    assert get_api_key("", "RECAP_TEST_KEY") == "env-key"
    assert get_api_key(None, "RECAP_TEST_KEY_MISSING") == ""
    del os.environ["RECAP_TEST_KEY"]

    # fmt_time
    assert fmt_time(65.5) == "01:05.50"

    print("All self-tests passed ✅")


def main():
    import sys
    if "--test" in sys.argv:
        run_tests()
        return
    demo = build_ui()
    demo.launch(server_name="0.0.0.0", server_port=7860, show_error=True)


if __name__ == "__main__":
    main()
