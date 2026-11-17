"""Focus Mode.

"Focus" (optionally "focus on <goal>") turns it on; "focus off" turns it off.
While on, the UI turns red, LUXUR answers strictly, off-topic requests get the
stock reply, and a watcher checks the active window title (only the title,
never a screenshot) and warns about, then minimizes, distractions.
"""
import re
import threading
import time

from Backend import Desktop, LLM
from Backend.Common import Assistantname, GetFocusState, SetFocusState

OFF_TOPIC_REPLY = "Focus, bitch. No bullshit."
CHECK_INTERVAL = 8     # seconds between window checks
BLOCK_AFTER = 25       # seconds a distraction may stay up after the warning

ON_PHRASES = ("focus", "focus mode", "focus mode on", "focus on", "start focus", "start focus mode",
              "enable focus mode", "turn on focus mode", "activate focus mode")
OFF_PHRASES = ("focus off", "focus mode off", "stop focus", "stop focus mode", "end focus", "end focus mode",
               "disable focus mode", "turn off focus mode", "deactivate focus mode", "unfocus")

DISTRACTIONS = re.compile(
    r"(youtube|netflix|prime video|hotstar|disney\+|hulu|twitch|tiktok|instagram|facebook|reddit|twitter|"
    r"\bx\.com\b|9gag|pinterest|snapchat|discord|steam|epic games|roblox|minecraft|valorant|fortnite|"
    r"league of legends|counter-strike|pubg|free fire|spotify)", re.I)
# Titles that are fine even though they match the list above.
FOCUS_FRIENDLY = re.compile(r"(lo-?fi|focus music|study (music|with me)|deep focus|white noise|brown noise|"
                            r"tutorial|lecture|course|documentation)", re.I)


def ParseCommand(query):
    """('on', goal) | ('off', None) | None"""
    q = re.sub(r"[.!?]+$", "", query.lower().strip())
    q = re.sub(rf"^(hey |ok |okay )?({Assistantname.lower()}[, ]+)?", "", q).strip()
    if q in OFF_PHRASES:
        return ("off", None)
    if q in ON_PHRASES:
        return ("on", "")
    match = re.match(r"^(?:start |enable |turn on )?focus(?: mode)? (?:on|for) (.+)$", q)
    if match and match.group(1) not in ("off",):
        return ("on", match.group(1).strip())
    return None


def State():
    return GetFocusState()


def Active():
    return bool(State().get("active"))


def Goal():
    return State().get("goal") or ""


def Activate(goal=""):
    SetFocusState({"active": True, "goal": goal, "started": time.time(), "distractions": 0, "blocked": 0})
    return ("Focus mode on. " + (f"We're working on {goal}. " if goal else "") +
            "No distractions. Say 'focus off' when you're done.")


def Deactivate(hub=None):
    state = State()
    if not state.get("active"):
        return "Focus mode is already off."
    minutes = int((time.time() - state.get("started", time.time())) / 60)
    distractions = state.get("distractions", 0)
    SetFocusState({"active": False})
    summary = f"{minutes} minute focus session" + (f" on {state['goal']}" if state.get("goal") else "") + \
              f", {distractions} distraction{'s' if distractions != 1 else ''}"
    if hub is not None:
        hub.add_memory_with_links(f"Focus session: {summary}", kind="episodic", source="verified", importance=3)
    return f"Focus mode off. {summary[0].upper() + summary[1:]}. Good work."


def StrictPrompt():
    goal = Goal()
    return ("FOCUS MODE IS ON. Be strict, direct and brief. No jokes, no small talk, no tangents. "
            + (f"The user is working on: {goal}. Steer every answer back to it." if goal else
               "Keep the user on their current work."))


def IsOffTopic(query):
    """True if, in Focus Mode, this request is a distraction (joke, random chat, off-topic)."""
    goal = Goal() or "their current work or study"
    try:
        verdict = LLM.Judge(
            f"The user turned on Focus Mode to work on: {goal}.\nTheir request: \"{query}\"\n"
            "Is this request useful for that work, or a practical command (open/close an app, volume, "
            "set a reminder, remember something, focus music)? Answer ON.\n"
            "Is it a joke, random chat, entertainment, gossip, or unrelated curiosity? Answer OFF.\n"
            "Reply with exactly one word: ON or OFF.", max_tokens=200)
    except Exception as e:
        print(f"[Focus] judge failed, allowing request: {e}")
        return False
    return verdict.strip().upper().startswith("OFF")


def _is_distraction(title, cache):
    if not title or Assistantname.lower() in title.lower():
        return False
    goal = Goal().lower()
    if FOCUS_FRIENDLY.search(title):
        return False
    match = DISTRACTIONS.search(title)
    if match:
        return match.group(0).lower() not in goal
    # Unknown window: judge it only with the local model, so titles stay on this PC.
    if goal and LLM.LocalAvailable():
        if title not in cache:
            try:
                cache[title] = LLM.LocalChat([{"role": "user", "content":
                    f"Someone is focusing on: {goal}. Their active window is titled \"{title}\". "
                    "Is that window a distraction from the work? Reply YES or NO."}], max_tokens=5).upper().startswith("YES")
            except Exception:
                cache[title] = False
        return cache[title]
    return False


class Watcher:
    """Background thread that warns about and then minimizes distracting windows."""

    def __init__(self, warn, desktop=Desktop, interval=CHECK_INTERVAL, block_after=BLOCK_AFTER):
        self.warn = warn
        self.desktop = desktop
        self.interval = interval
        self.block_after = block_after
        self._cache = {}
        self._warned = {}   # title -> time of warning
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()
        return self

    def stop(self):
        self._stop.set()

    def check_once(self):
        if not Active():
            self._warned.clear()
            return None
        title = self.desktop.ActiveWindowTitle()
        if not _is_distraction(title, self._cache):
            return None
        now = time.time()
        state = State()
        if title not in self._warned:
            self._warned[title] = now
            state["distractions"] = state.get("distractions", 0) + 1
            SetFocusState(state)
            self.warn(f"That's a distraction: {title[:60]}. Get back to work.")
            return "warned"
        if now - self._warned[title] >= self.block_after:
            try:
                self.desktop.WindowAction("minimize", title)
            except Exception as e:
                print(f"[Focus] couldn't minimize '{title}': {e}")
            state["blocked"] = state.get("blocked", 0) + 1
            SetFocusState(state)
            self._warned[title] = now
            self.warn("I minimized it. Back to work.")
            return "blocked"
        return None

    def _loop(self):
        while not self._stop.wait(self.interval):
            try:
                self.check_once()
            except Exception as e:
                print(f"[Focus] watcher error: {e}")
