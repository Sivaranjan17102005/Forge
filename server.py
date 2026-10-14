"""Local web server, Ollama proxy, and document/voice helpers for Forge."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import json
import math
import re
import threading
import webbrowser
import base64
import io
import zipfile
import xml.etree.ElementTree as ET
import sys
import os
import subprocess
import tempfile
import wave
import importlib.util

ROOT = Path(__file__).resolve().parent
LOCAL_PACKAGES = ROOT / ".forge_packages"
if LOCAL_PACKAGES.exists():
    sys.path.insert(0, str(LOCAL_PACKAGES))
OLLAMA = "http://127.0.0.1:11434"
VOSK_MODEL = ROOT / "vosk-model-small-en-us-0.15"
_speech_model = None
def local_stress_answer(text):
    """Calculate common axial-stress prompts from their original values and units."""
    if not re.search(r"\b(?:stress|sigma|pressure)\b|σ", text, re.IGNORECASE):
        return None
    if re.search(r"\b(?:in|to|as)\s+(?:psi|ksi|bar)\b", text, re.IGNORECASE):
        return None

    number = r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?)"
    force_re = re.compile(number + r"\s*(kN|N|kilonewtons?|newtons?)\b", re.IGNORECASE)
    area_re = re.compile(
        number + r"\s*(mm|cm|m)\s*(?:\^\s*2|2|²)(?![A-Za-z0-9])",
        re.IGNORECASE,
    )
    forces = list(force_re.finditer(text))
    areas = list(area_re.finditer(text))
    if len(forces) != 1 or len(areas) != 1:
        return None

    force_value = float(forces[0].group(1))
    force_unit = forces[0].group(2).lower()
    area_value = float(areas[0].group(1))
    area_unit = areas[0].group(2).lower()
    if not math.isfinite(force_value) or not math.isfinite(area_value) or area_value <= 0:
        return None

    force_n = force_value * (1000 if force_unit in {"kn", "kilonewton", "kilonewtons"} else 1)
    area_m2 = area_value * {"mm": 1e-6, "cm": 1e-4, "m": 1}[area_unit]
    stress_pa = force_n / area_m2
    requested = re.search(r"\b(GPa|MPa|kPa|Pa)\b", text)
    output_unit = requested.group(1) if requested else "MPa"
    scales = {"Pa": 1, "kPa": 1e3, "MPa": 1e6, "GPa": 1e9}
    value = stress_pa / scales[output_unit]

    def fmt(number):
        return format(number, ".10g")

    answer = (
        "Forge's local calculator: nominal axial stress = F/A. "
        f"{fmt(force_value)} {forces[0].group(2)} = {fmt(force_n)} N; "
        f"{fmt(area_value)} {area_unit}² = {fmt(area_m2)} m². "
        f"σ = {fmt(force_n)} N / {fmt(area_m2)} m² = {fmt(stress_pa)} Pa = {fmt(value)} {output_unit}."
    )
    return {"message": {"role": "assistant", "content": answer}, "done": True}


class Handler(BaseHTTPRequestHandler):
    def respond(self, status, body, content_type="application/json; charset=utf-8"):
        encoded = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        if self.path == "/api/status":
            self.respond(200, json.dumps({
                "document_pdf": importlib.util.find_spec("pypdf") is not None,
                "speech_recognition": VOSK_MODEL.exists() and LOCAL_PACKAGES.exists(),
                "speech_output": os.name == "nt",
                "scanned_pdf_ocr": importlib.util.find_spec("fitz") is not None,
            }))
        elif self.path == "/api/tags":
            self.proxy("/api/tags", None)
        elif self.path in ("/", "/index.html"):
            try:
                self.respond(200, (ROOT / "index.html").read_bytes(), "text/html; charset=utf-8")
            except OSError as error:
                self.respond(500, json.dumps({"error": str(error)}))
        else:
            self.respond(404, json.dumps({"error": "Not found"}))

    def do_POST(self):
        if self.path == "/api/extract":
            return self.extract_document()
        if self.path == "/api/transcribe":
            return self.transcribe_audio()
        if self.path == "/api/speak":
            return self.speak_text()
        if self.path != "/api/chat":
            return self.respond(404, json.dumps({"error": "Not found"}))
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            stream = bool(payload.get("stream", False))
            payload["stream"] = stream
            payload.setdefault("keep_alive", "1m")
        except (ValueError, json.JSONDecodeError) as error:
            return self.respond(400, json.dumps({"error": f"Invalid request: {error}"}))
        user_message = next(
            (message.get("content", "") for message in reversed(payload.get("messages", []))
             if message.get("role") == "user"),
            "",
        )
        local_answer = local_stress_answer(user_message)
        if local_answer:
            return self.respond(
                200,
                json.dumps(local_answer, ensure_ascii=False) + "\n",
                "application/x-ndjson; charset=utf-8",
            )
        self.proxy("/api/chat", json.dumps(payload).encode("utf-8"), stream)

    def extract_document(self):
        """Extract text locally from supported document formats."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 16 * 1024 * 1024:
                payload = json.loads(self.rfile.read(length))
            else:
                return self.respond(413, json.dumps({"error": "Document is larger than the 16 MB limit."}))
            name = Path(payload.get("name", "document")).name
            raw = base64.b64decode(payload.get("data", ""), validate=True)
            suffix = Path(name).suffix.lower()
            if suffix == ".docx":
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    root = ET.fromstring(archive.read("word/document.xml"))
                ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
                text = "\n".join("".join(node.itertext()) for node in root.findall(".//w:p", ns))
            elif suffix == ".pdf":
                try:
                    from pypdf import PdfReader
                except ImportError:
                    return self.respond(501, json.dumps({"error": "PDF text extraction needs pypdf. In PowerShell run: py -3 -m pip install pypdf"}))
                reader = PdfReader(io.BytesIO(raw))
                page_text = [page.extract_text() or "" for page in reader.pages]
                if any(not value.strip() for value in page_text):
                    try:
                        import fitz
                    except ImportError:
                        return self.respond(501, json.dumps({"error": "Scanned-page OCR needs PyMuPDF. Run: py -3 -m pip install --target .forge_packages pymupdf"}))
                    missing = [i for i, value in enumerate(page_text) if not value.strip()]
                    if len(missing) > 5:
                        return self.respond(413, json.dumps({"error": "OCR is limited to five scanned pages per PDF. Split the document into smaller files."}))
                    pdf = fitz.open(stream=raw, filetype="pdf")
                    model = payload.get("visionModel", "qwen2.5vl:3b")
                    for i in missing:
                        pixmap = pdf[i].get_pixmap(matrix=fitz.Matrix(1.35, 1.35), alpha=False)
                        image = base64.b64encode(pixmap.tobytes("png")).decode("ascii")
                        ocr_request = Request(
                            OLLAMA + "/api/chat",
                            data=json.dumps({"model": model, "stream": False, "keep_alive": "1m", "messages": [{
                                "role": "user",
                                "content": "Transcribe the visible document text on this page. Preserve headings, numbers, and table rows as plainly as possible. Mark illegible words; do not invent text.",
                                "images": [image],
                            }]}).encode("utf-8"),
                            headers={"Content-Type": "application/json"},
                        )
                        try:
                            with urlopen(ocr_request, timeout=600) as response:
                                result = json.loads(response.read())
                            page_text[i] = result.get("message", {}).get("content", "")
                        except (HTTPError, URLError, TimeoutError, OSError) as error:
                            return self.respond(502, json.dumps({"error": f"Scanned PDF OCR failed on page {i + 1}: {error}"}))
                    pdf.close()
                text = "\n\n".join(page_text)
            else:
                return self.respond(415, json.dumps({"error": "Supported documents: PDF, DOCX, TXT, Markdown, and CSV."}))
            text = text.strip()
            if not text:
                return self.respond(422, json.dumps({"error": "No readable text was found in this document."}))
            self.respond(200, json.dumps({"name": name, "text": text[:50000], "truncated": len(text) > 50000}, ensure_ascii=False))
        except (ValueError, zipfile.BadZipFile, KeyError, ET.ParseError) as error:
            self.respond(400, json.dumps({"error": f"Could not read this document: {error}"}))

    def transcribe_audio(self):
        global _speech_model
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 12 * 1024 * 1024:
                return self.respond(413, json.dumps({"error": "Audio is larger than the 12 MB limit."}))
            payload = json.loads(self.rfile.read(length))
            audio = base64.b64decode(payload.get("data", ""), validate=True)
            if not VOSK_MODEL.exists():
                return self.respond(503, json.dumps({"error": "Offline speech model is not installed."}))
            if _speech_model is None:
                from vosk import Model, SetLogLevel
                SetLogLevel(-1)
                _speech_model = Model(str(VOSK_MODEL))
            with wave.open(io.BytesIO(audio), "rb") as wav:
                if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getframerate() != 16000:
                    return self.respond(400, json.dumps({"error": "Audio must be mono 16 kHz PCM."}))
                from vosk import KaldiRecognizer
                recognizer = KaldiRecognizer(_speech_model, 16000)
                while True:
                    chunk = wav.readframes(8000)
                    if not chunk:
                        break
                    recognizer.AcceptWaveform(chunk)
                result = json.loads(recognizer.FinalResult())
            self.respond(200, json.dumps({"text": result.get("text", "")}, ensure_ascii=False))
        except (ValueError, wave.Error) as error:
            self.respond(400, json.dumps({"error": f"Could not transcribe audio: {error}"}))
        except ImportError:
            self.respond(503, json.dumps({"error": "Vosk is not installed in Forge's local packages."}))

    def speak_text(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 64 * 1024:
                return self.respond(413, json.dumps({"error": "Speech text is too long."}))
            text = json.loads(self.rfile.read(length)).get("text", "").strip()[:5000]
            if not text:
                return self.respond(400, json.dumps({"error": "No text to speak."}))
            script = """$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Speech
$raw=[Console]::In.ReadToEnd()
$text=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($raw))
$speaker=New-Object System.Speech.Synthesis.SpeechSynthesizer
$speaker.SetOutputToWaveFile($env:FORGE_SPEECH_OUTPUT)
$speaker.Speak($text)
$speaker.Dispose()
"""
            encoded_script = base64.b64encode(script.encode("utf-16le")).decode("ascii")
            encoded_text = base64.b64encode(text.encode("utf-8")).decode("ascii")
            with tempfile.TemporaryDirectory(prefix="forge-speech-") as folder:
                output = Path(folder) / "reply.wav"
                env = os.environ.copy()
                env["FORGE_SPEECH_OUTPUT"] = str(output)
                subprocess.run(
                    ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded_script],
                    input=encoded_text, text=True, encoding="ascii", stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE, timeout=60, check=True, env=env,
                )
                self.respond(200, output.read_bytes(), "audio/wav")
        except (OSError, subprocess.SubprocessError) as error:
            self.respond(500, json.dumps({"error": f"Windows speech output failed: {error}"}))

    def proxy(self, path, data, stream=False):
        request = Request(OLLAMA + path, data=data, headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=600) as response:
                if stream:
                    self.send_response(response.status)
                    self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Accel-Buffering", "no")
                    self.end_headers()
                    while True:
                        # Ollama emits one newline-delimited JSON object per token;
                        # read a line at a time so small token chunks flush immediately.
                        chunk = response.readline()
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        self.wfile.flush()
                else:
                    self.respond(response.status, response.read())
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            self.respond(error.code, detail or json.dumps({"error": str(error)}))
        except (URLError, TimeoutError, OSError) as error:
            self.respond(502, json.dumps({"error": f"Cannot reach Ollama at {OLLAMA}: {error}"}))

    def log_message(self, fmt, *args):
        print(f"[Forge] {self.address_string()} - {fmt % args}")


if __name__ == "__main__":
    address = ("127.0.0.1", 8765)
    print(f"Forge is running at http://{address[0]}:{address[1]}")
    print("Close this window to stop Forge.")
    server = ThreadingHTTPServer(address, Handler)
    threading.Timer(0.8, lambda: webbrowser.open(f"http://{address[0]}:{address[1]}")).start()
    server.serve_forever()
