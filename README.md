# Forge — Engineering AI Companion

A free, local-first engineering assistant. Forge has no subscription, account, API key, analytics, or third-party service. The interface runs in your browser; responses come from an Ollama model on your own computer. Chats and project context are saved in browser storage.

## Start on Windows

1. Install [Ollama](https://ollama.com/download) and Python 3.
2. Open PowerShell or Command Prompt and download a model once:

   ```powershell
   ollama pull qwen2.5:3b
   ollama pull qwen2.5vl:3b
   py -3 -m pip install --target .forge_packages pypdf pymupdf vosk
   Invoke-WebRequest https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip -OutFile vosk.zip
   Expand-Archive vosk.zip -DestinationPath .
   Remove-Item vosk.zip
   ```

   Choose a smaller or larger Ollama model to match your computer's memory and speed. Model downloads use disk space and require internet access the first time.

3. Double-click `start_forge.bat`. It opens Forge at `http://127.0.0.1:8765` and runs the local web server. Ollama must also be running.
4. In **Model settings**, enter the model name you downloaded if you chose a different one.

## What it does

- Engineering-oriented chat for explaining concepts, scoping projects, reviewing ideas, troubleshooting, and planning experiments.
- Deterministic local calculator for simple axial stress prompts with force in N/kN and area in mm²/cm²/m²; it displays the SI conversions and requested Pa/kPa/MPa/GPa result.
- Answers stream into the conversation as the local model generates them.
- Image understanding: attach up to four images; Forge compresses them in the browser and sends them to the configured Ollama vision model (default `qwen2.5vl:3b`). The vision model must be downloaded separately.
- Document reading: TXT, Markdown, and CSV are read in the browser; DOCX and PDF are extracted locally. Searchable PDF text is read directly; scanned pages are rendered locally and transcribed by the vision model (up to five scanned pages per PDF).
- Voice: the Voice button captures up to 60 seconds from the microphone, transcribes locally with Vosk English, then speaks Forge's answer using the installed Windows voice. Audio is sent only to Forge on localhost. Microphone access is requested by the browser.
- Optional project context is included with each request to help the model stay focused on your current project.
- Recent conversations and project context are stored in this browser's local storage. Clearing browser storage removes them.
- The AI is only as capable as the model you install. Qwen2.5-VL is a compact local vision model and may miss visual details. Forge does not browse the web or verify standards or citations. The local calculator covers the axial-stress case above; verify other calculations independently, especially before design or safety decisions.

## Troubleshooting

- If the app says **Ollama not connected**, open/start Ollama and leave it running.
- If it says **Model not downloaded**, run `ollama pull MODEL_NAME` and check **Model settings** for the matching name.
- If `py -3` is unavailable, install Python 3 or launch `python server.py` from this folder.

Forge's web server listens only on `127.0.0.1`; it is intended for use on this computer. Publishing this source code to GitHub does not publish a working Forge service. The app depends on Ollama running on the same computer, local model files, and Windows SAPI/Vosk for its current voice implementation. GitHub Pages can host static files only and cannot run this Python/Ollama backend. Do not expose the local server directly to the internet: it has no authentication and is designed for loopback-only use.

## Model status

Forge uses the pretrained Qwen 2.5 3B model through Ollama. This setup downloads and runs that model locally; it does not train or fine-tune a new engineering model on your project files. Project context is included with chat requests but remains in this browser's storage.

Image requests use the separate pretrained Qwen2.5-VL 3B vision model by default. Configure both model names under **Model settings** if you use alternatives. Offline English voice recognition uses Vosk's small English model; speech output uses the installed Microsoft Zira Desktop voice through Windows SAPI. Voice audio stays on the computer.

### PDF support

Forge uses `pypdf` for text extraction and PyMuPDF to render scanned pages for local vision-model OCR. The setup command above installs both into Forge's local package directory.

```powershell
py -3 -m pip install pypdf
```

