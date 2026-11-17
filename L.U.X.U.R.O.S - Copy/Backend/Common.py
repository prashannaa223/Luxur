"""Shared paths, settings and helpers used by the backend and the GUI.

Paths are resolved from this file's location, so the app works no matter which
directory it is launched from. Windows is the target; Linux works for testing.
"""
import datetime
import json
import socket
import sys
import threading
import time
import uuid
from pathlib import Path

from dotenv import dotenv_values

# Prefer IPv4: on some networks IPv6 is advertised but broken, which made
# web requests hang. (Moved here from Automation.py so it applies everywhere.)
_orig_getaddrinfo = socket.getaddrinfo


def _ipv4_first_getaddrinfo(*args, **kwargs):
    results = _orig_getaddrinfo(*args, **kwargs)
    ipv4 = [r for r in results if r[0] == socket.AF_INET]
    return ipv4 or results


socket.getaddrinfo = _ipv4_first_getaddrinfo
if socket.getdefaulttimeout() is None:
    socket.setdefaulttimeout(20)

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "Data"
FILES_DIR = ROOT_DIR / "Frontend" / "Files"
GRAPHICS_DIR = ROOT_DIR / "Frontend" / "Graphics"
ENV_PATH = ROOT_DIR / ".env"
CHATLOG_PATH = DATA_DIR / "ChatLog.json"

IS_WINDOWS = sys.platform.startswith("win")

DATA_DIR.mkdir(exist_ok=True)
FILES_DIR.mkdir(parents=True, exist_ok=True)

_env = dotenv_values(ENV_PATH)


def Env(*keys, default=None):
    """Return the first non-empty value among `keys` from .env."""
    for key in keys:
        value = _env.get(key)
        if value and value.strip():
            return value.strip()
    return default


def RequireEnv(*keys):
    value = Env(*keys)
    if not value:
        raise RuntimeError(f"'{keys[0]}' is missing from {ENV_PATH}. See .env.example.")
    return value


Username = Env("Username", default="User")
Assistantname = Env("Assistantname", "AssistantName", default="Luxur")
InputLanguage = Env("InputLanguage", default="en")
AssistantVoice = Env("AssistantVoice", default="en-CA-LiamNeural")

# Models (all overridable in .env)
GroqModel = Env("GroqModel", default="openai/gpt-oss-120b")        # chat, search answers, agent
GroqFastModel = Env("GroqFastModel", default="openai/gpt-oss-20b")  # quick yes/no checks, memory
CohereModel = Env("CohereModel", default="command-a-plus-05-2026")  # router fallback + screen reading
GroqVisionModel = Env("GroqVisionModel", default="qwen/qwen3.8-27b")  # screen-reading fallback
LocalModel = Env("LocalModel", default="qwen2.5:1.5b")              # Ollama router (optional)
OllamaURL = Env("OllamaURL", default="http://localhost:11434")
ImageModel = Env("ImageModel", default="stabilityai/stable-diffusion-xl-base-1.0")

# Browser LUXUR opens pages in: auto (first installed of zen, helium, librewolf, firefox, brave,
# vivaldi, chrome, edge) | any of those names | default (the system default browser).
Browser = Env("Browser", default="auto").lower()

# Privacy: "on" lets the desktop agent send screenshots to Cohere during a task
# you asked for; "off" disables screen reading entirely.
ScreenReading = Env("ScreenReading", default="on").lower()

# How many past messages are sent to the LLM. The full log is kept in Pulse Hub.
HISTORY_LIMIT = 20


# --------------------------------------------------------------------------
# File-based state shared between the GUI thread and the assistant thread.
# --------------------------------------------------------------------------

def TempDirectoryPath(Filename):
    return FILES_DIR / Filename


def GraphicsDirectoryPath(Filename):
    return GRAPHICS_DIR / Filename


def _read(Filename, default=""):
    try:
        return TempDirectoryPath(Filename).read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return default


def _write(Filename, Text):
    TempDirectoryPath(Filename).write_text(Text, encoding="utf-8")


def _read_flag(Filename):
    return _read(Filename).strip() == "True"


def SetMicrophoneStatus(Command):
    _write("Mic.data", Command)


def GetMicrophoneStatus():
    return _read("Mic.data", "False").strip()


def SetAssistantStatus(Status):
    _write("Status.data", Status)


def GetAssistantStatus():
    return _read("Status.data").strip()


def ShowTextToScreen(Text):
    _write("Response.data", Text)


def Interrupted():
    return _read_flag("Interrupt.data")


def ClearInterrupt():
    _write("Interrupt.data", "False")


def SubmitTypedQuery(Text):
    if GetMicrophoneStatus() != "True":
        _write("TypedRestoreMic.data", "True")  # switch the mic back off afterwards
    _write("TypedInput.data", Text)
    SetMicrophoneStatus("True")  # wakes the assistant loop


def TypedPending():
    return bool(_read("TypedInput.data").strip())


def RestoreMicAfterTyped():
    if _read_flag("TypedRestoreMic.data"):
        _write("TypedRestoreMic.data", "False")
        SetMicrophoneStatus("False")


def ReadTypedQuery():
    text = _read("TypedInput.data").strip()
    if text:
        _write("TypedInput.data", "")
    return text


# --------------------------------------------------------------------------
# Spoken notices from background services (Focus warnings, reminders).
# They are queued and spoken between turns, so the speech recognizer never
# hears LUXUR's own voice as a command.
# --------------------------------------------------------------------------

_announce_lock = threading.Lock()


def QueueAnnouncement(text):
    with _announce_lock:
        try:
            queue = json.loads(_read("Announce.data") or "[]")
        except json.JSONDecodeError:
            queue = []
        queue.append(text)
        _write("Announce.data", json.dumps(queue))


def AnnouncementPending():
    return _read("Announce.data").strip() not in ("", "[]")


def PopAnnouncements():
    with _announce_lock:
        try:
            queue = json.loads(_read("Announce.data") or "[]")
        except json.JSONDecodeError:
            queue = []
        _write("Announce.data", "[]")
    return queue


# --------------------------------------------------------------------------
# Focus Mode state (read by the GUI to turn the theme red)
# --------------------------------------------------------------------------

def GetFocusState():
    try:
        state = json.loads(_read("Focus.data") or "{}")
        return state if isinstance(state, dict) else {}
    except json.JSONDecodeError:
        return {}


def SetFocusState(state):
    _write("Focus.data", json.dumps(state))


def FocusActive():
    return bool(GetFocusState().get("active"))


# --------------------------------------------------------------------------
# Confirmation requests: the assistant thread asks, the GUI answers.
# Each request has a unique id so a stale answer can never approve a new action.
# --------------------------------------------------------------------------

CONFIRM_TIMEOUT = 60


def RequestConfirmation(question, timeout=CONFIRM_TIMEOUT):
    """Ask the user (via a GUI dialog) to approve an action. Denies on timeout."""
    request_id = uuid.uuid4().hex
    _write("ConfirmAnswer.data", "")
    _write("Confirm.data", json.dumps({"id": request_id, "question": question, "ts": time.time()}))
    deadline = time.time() + timeout
    try:
        while time.time() < deadline:
            try:
                answer = json.loads(_read("ConfirmAnswer.data") or "{}")
            except json.JSONDecodeError:
                answer = {}
            if answer.get("id") == request_id:
                return answer.get("approved") is True
            if Interrupted():
                return False
            time.sleep(0.2)
        return False
    finally:
        _write("Confirm.data", "")


def PendingConfirmation():
    try:
        request = json.loads(_read("Confirm.data") or "{}")
        return request if request.get("id") else None
    except json.JSONDecodeError:
        return None


def AnswerConfirmation(request_id, approved):
    _write("ConfirmAnswer.data", json.dumps({"id": request_id, "approved": bool(approved)}))


# --------------------------------------------------------------------------
# Chat log (Pulse Hub is the source of truth; ChatLog.json mirrors it for the chat screen)
# --------------------------------------------------------------------------

def LoadChatLog():
    try:
        with open(CHATLOG_PATH, "r", encoding="utf-8-sig") as f:
            content = f.read().strip()
        data = json.loads(content) if content else []
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def SaveChatLog(messages):
    with open(CHATLOG_PATH, "w", encoding="utf-8") as f:
        json.dump(messages, f, indent=4, ensure_ascii=False)


# --------------------------------------------------------------------------
# Text helpers
# --------------------------------------------------------------------------

def AnswerModifier(Answer):
    return "\n".join(line for line in Answer.split("\n") if line.strip())


QUESTION_WORDS = ["how", "what", "who", "where", "when", "why", "which", "whose",
                  "whom", "can you", "would you", "what's", "where's", "how's"]


def QueryModifier(Query):
    new_query = Query.lower().strip()
    if not new_query:
        return new_query

    is_question = any(new_query.startswith(w + " ") or f" {w} " in new_query for w in QUESTION_WORDS)
    end = "?" if is_question else "."
    if new_query[-1] in ".?!":
        new_query = new_query[:-1]
    return (new_query + end).capitalize()


def RealtimeInformation():
    now = datetime.datetime.now()
    return (
        "Use this real-time information if needed:\n"
        f"Day: {now:%A}\nDate: {now:%d}\nMonth: {now:%B}\nYear: {now:%Y}\n"
        f"Time: {now:%H} hours, {now:%M} minutes, {now:%S} seconds.\n"
    )
