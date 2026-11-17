"""Single-step commands from the router: open/close apps, play, search, content, system controls.
Multi-step tasks go to the desktop agent (Backend/Agent.py) instead."""
import asyncio
import re
import subprocess

from rich import print

from Backend import Desktop, LLM
from Backend.Common import DATA_DIR, IS_WINDOWS, Assistantname, Username

ContentWriterSystem = [{
    "role": "system",
    "content": f"Hello, I am {Username}. You are {Assistantname}, a content writer. You write content such as letters, "
               "code, applications, essays, notes, songs and poems. Write the requested content directly, with proper "
               "grammar and formatting.",
}]


def _hub():
    from Backend.Chatbot import Hub
    return Hub()


def OpenInEditor(path):
    if IS_WINDOWS:
        subprocess.Popen(["notepad.exe", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def Content(Topic):
    Topic = Topic.strip()
    text = LLM.Chat(ContentWriterSystem + [{"role": "user", "content": Topic}], max_tokens=2048)
    safe_name = re.sub(r"[^a-z0-9]+", "_", Topic.lower()).strip("_")[:60] or "content"
    path = DATA_DIR / f"{safe_name}.txt"
    path.write_text(text, encoding="utf-8")
    OpenInEditor(path)
    return f"Wrote {path.name}"


def OpenApp(app):
    _hub().record_usage("app", app)
    return Desktop.OpenApp(app)


def System(command):
    """Simple one-step computer controls ('system <command>')."""
    c = command.strip().lower().replace("volumeup", "volume up").replace("volumedown", "volume down")
    nudge = {"mouse left": (-400, 0), "move mouse left": (-400, 0), "mouse right": (400, 0), "move mouse right": (400, 0),
             "mouse up": (0, -400), "move mouse up": (0, -400), "mouse down": (0, 400), "move mouse down": (0, 400)}
    if c in ("mute", "unmute", "volume up", "volume down", "play", "pause", "play pause", "next", "next track",
             "skip", "previous", "previous track", "rewind", "forward"):
        return Desktop.Media(c.replace(" track", ""))
    if c in Desktop.BROWSER_KEYS or c in ("go back", "go forward"):
        return Desktop.BrowserAction(c.replace("go ", ""))
    if c in ("click", "left click"):
        return Desktop.MouseClick()
    if c == "double click":
        return Desktop.MouseClick(clicks=2)
    if c == "right click":
        return Desktop.MouseClick(button="right")
    if c in ("scroll up", "scroll down"):
        return Desktop.Scroll(6 if c.endswith("up") else -6)
    if c in nudge:
        g = Desktop.gui()
        g.moveRel(*nudge[c], duration=0.25)
        return "Moved the mouse"
    if c in ("mouse center", "center mouse"):
        w, h = Desktop.ScreenSize()
        return Desktop.MouseMove(w // 2, h // 2)
    if c.startswith("move mouse to "):
        parts = re.findall(r"\d+", c)
        return Desktop.MouseMove(parts[0], parts[1]) if len(parts) >= 2 else "Say 'move mouse to X Y'"
    if c.startswith("type "):
        return Desktop.TypeText(command.strip()[5:])
    if c.startswith("press "):
        return Desktop.PressKeys(c[6:])
    if c in ("show desktop", "minimize all"):
        return Desktop.WindowAction("minimize all")
    if c in ("minimize window", "minimize", "maximize window", "maximize", "restore window", "switch window"):
        return Desktop.WindowAction(c.split()[0])
    if c == "task view":
        return Desktop.PressKeys("win+tab")
    if c == "screenshot":
        return Desktop.PressKeys("win+printscreen") if IS_WINDOWS else "Screenshot shortcut is Windows-only"
    if c == "lock":
        return Desktop.PressKeys("win+l") if IS_WINDOWS else "Lock is Windows-only"
    print(f"[yellow]Unknown system command: {command}[/yellow]")
    return f"Unknown system command: {command}"


# Desktop functions are looked up at call time (so they can be swapped in tests).
HANDLERS = [
    ("google search ", lambda q: Desktop.Search(q, "google")),
    ("youtube search ", lambda q: Desktop.Search(q, "youtube")),
    ("open ", lambda q: OpenApp(q)),
    ("close ", lambda q: Desktop.CloseApp(q)),
    ("play ", lambda q: Desktop.PlayYoutube(q)),
    ("content ", lambda q: Content(q)),
    ("system ", lambda q: System(q)),
]

NOT_AUTOMATION = ("general", "realtime", "generate image", "reminder", "remember", "agent", "exit")


async def TranslateAndExecute(commands: list):
    funcs = []
    for command in commands:
        if command.startswith(NOT_AUTOMATION) or command in ("open it", "open file"):
            continue
        for prefix, handler in HANDLERS:
            if command.startswith(prefix):
                funcs.append(asyncio.to_thread(handler, command.removeprefix(prefix)))
                break
        else:
            print(f"[red]Unknown command: {command}[/red]")
    for result in await asyncio.gather(*funcs, return_exceptions=True):
        if isinstance(result, Exception):
            print(f"[red]Automation error: {result}[/red]")
        yield result


async def Automation(commands: list):
    results = [r async for r in TranslateAndExecute(commands)]
    return results
