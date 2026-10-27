# 🎬 Recap Cinema — မြန်မာ Movie Recap Studio

Mobile-first movie recap video maker. Upload a video → AI transcribes & rewrites it
in natural Myanmar recap style → add logo, subtitle mask, Myanmar voice, background
music → render the final MP4. All UI text in Myanmar (မြန်မာ).

ဖုန်းနဲ့ အသုံးပြုရ လွယ်ကူသော မြန်မာ Movie Recap ဗီဒီယို ဖန်တီးသည့် Website ဖြစ်ပါသည်။

## ✨ Features / လုပ်ဆောင်ချက်များ

1. **📤 ဗီဒီယို တင်ရန်** — MP4 / MOV / WEBM (၁၀ မိနစ်အထိ), instant preview
2. **🎙️ စာသားထုတ် + မြန်မာလိုပြန်ရေး** — AssemblyAI transcription (Chinese) →
   Gemini rewrite in natural Myanmar recap style (not literal translation,
   fillers removed)
3. **✏️ စာသားပြင်ဆင်ရန်** — editable segments ([၁], [၂], …) before voice generation
4. **🈲 စာတန်းဖယ်ရန်** — subtitle mask with sliders (X / Y / width / height),
   Blur or Cover mode
5. **🖼️ လိုဂို** — upload PNG/JPG/WEBP, 6 position presets + fine-tune sliders,
   size + opacity sliders, applied to whole video
6. **🔊 မူရင်းစကားပြောသံ** — toggle (default ON): original voice removed,
   background/ambient kept at ~15% under narration
7. **🎤 မြန်မာအသံ** — Edge TTS မင်းခန့် (my-MM-ThihaNeural) / နီလာ (my-MM-NilarNeural),
   or upload your own MP3/WAV
8. **🎵 နောက်ခံတေးဂီတ** — optional upload, separate voice/music volume sliders
9. **👁️ Preview** — test first segment with all effects before final render
10. **🎬 မြန်မာ Movie Recap ပြုလုပ်မည်** — one big button → progress in Myanmar →
    final MP4 + **💾 ဗီဒီယိုသိမ်းရန်** download

## 🔑 API Keys (never hardcoded)

Enter keys in the app's **🔑 API Keys** section, or set environment variables:

```bash
export ASSEMBLYAI_API_KEY="your-key"   # speech-to-text
export GEMINI_API_KEY="your-key"       # Myanmar recap rewrite
```

> ⚠️ Key များကို code ထဲမှာ hardcode မထားပါ — textbox (သို့မဟုတ်) environment
> variable ဖြင့်သာ ထည့်သွင်းပါသည်။

## 🚀 Run locally

```bash
# 1. ffmpeg (Ubuntu/Debian)
sudo apt install ffmpeg

# 2. Python packages
pip install -r requirements.txt

# 3. Run
python app.py
# → http://localhost:7860
```

Run self-tests:

```bash
python app.py --test
```

## 🐳 HuggingFace Spaces (Docker)

Ready to deploy — `Dockerfile` builds with ffmpeg + Myanmar fonts:

1. Create a Space with **Docker** SDK
2. Upload all files (`app.py`, `requirements.txt`, `Dockerfile`, `README.md`,
   `.dockerignore`)
3. The Space builds and serves on port 7860 automatically

> ⚠️ Note: HuggingFace requires a **PRO subscription (~$9/month)** for
> Gradio/Docker Spaces with compute. Free accounts can only host Static Spaces.

## 🛠️ Tech

- **Framework:** Gradio 5.42 (single `app.py`, mobile-first cinema dark theme —
  black/gold, big touch buttons)
- **STT:** AssemblyAI (Chinese source)
- **Rewrite:** Google Gemini (natural Myanmar recap style)
- **TTS:** Edge TTS (`my-MM-ThihaNeural`, `my-MM-NilarNeural`)
- **Video:** ffmpeg (subtitle mask, logo overlay, audio mixing, concat)

## 📁 Files

| File | Description |
|------|-------------|
| `app.py` | Whole app — pipeline + render engine + UI + self-tests |
| `requirements.txt` | Python dependencies |
| `Dockerfile` | HF Spaces Docker build (port 7860) |
| `README.md` | This file |
| `.dockerignore` | Docker build exclusions |

## ⚠️ Limitations / ကန့်သတ်ချက်များ

- အရင်းဗီဒီယို ၁၀ မိနစ်အထိသာ
- Render အချိန် စက်စွမ်းရည်ပေါ် မူတည်၍ မိနစ်အနည်းငယ် ကြာနိုင်
- Edge TTS / AssemblyAI / Gemini အတွက် အင်တာနက်လိုပါသည်
