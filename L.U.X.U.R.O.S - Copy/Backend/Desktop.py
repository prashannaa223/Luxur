"""Desktop control: apps, windows, browser, media, mouse, keyboard, screen.

Windows is the supported platform. The `not IS_WINDOWS` branches are a thin
Linux shim for testing only (needs an X11/XWayland session) and aren't maintained.
Every function returns a short human-readable result string for the agent.
"""
import glob
import io
import os
import re
import shutil
import subprocess
import sys
import time
import types
import webbrowser
from urllib.parse import quote_plus

import requests

from Backend.Common import IS_WINDOWS, Browser

if IS_WINDOWS:
    import winreg

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

_pyautogui = None


def gui():
    """Import pyautogui lazily (it needs a display)."""
    global _pyautogui
    if _pyautogui is None:
        if not IS_WINDOWS and "mouseinfo" not in sys.modules:
            # pyautogui imports mouseinfo, which exits the process when tkinter is
            # missing on Linux. It's only used for the MouseInfo helper window.
            sys.modules["mouseinfo"] = types.ModuleType("mouseinfo")
        import pyautogui
        pyautogui.FAILSAFE = True   # slam the mouse into a screen corner to abort
        pyautogui.PAUSE = 0.05
        _pyautogui = pyautogui
    return _pyautogui


# --------------------------------------------------------------------------
# Browsers
# --------------------------------------------------------------------------

# Preference order for Browser=auto: privacy browsers first.
BROWSER_ORDER = ["zen", "helium", "librewolf", "firefox", "brave", "vivaldi", "chrome", "edge"]
# name -> (Windows exe name for the App Paths registry, Linux commands)
BROWSERS = {
    "zen": ("zen.exe", ("zen-browser", "zen")),
    "helium": ("helium.exe", ("helium", "helium-browser")),
    "librewolf": ("librewolf.exe", ("librewolf",)),
    "firefox": ("firefox.exe", ("firefox",)),
    "brave": ("brave.exe", ("brave", "brave-browser")),
    "vivaldi": ("vivaldi.exe", ("vivaldi", "vivaldi-stable")),
    "chrome": ("chrome.exe", ("google-chrome-stable", "google-chrome", "chromium")),
    "edge": ("msedge.exe", ("microsoft-edge", "microsoft-edge-stable")),
}
BROWSER_ALIASES = {"google chrome": "chrome", "microsoft edge": "edge", "zen browser": "zen", "mozilla firefox": "firefox",
                   "brave browser": "brave", "helium browser": "helium"}


def _windows_browser_candidates(name):
    local, pf, pf86 = (os.path.expandvars(v) for v in ("%LOCALAPPDATA%", "%PROGRAMFILES%", "%PROGRAMFILES(X86)%"))
    return {
        "zen": [rf"{pf}\Zen Browser\zen.exe", rf"{local}\Zen Browser\zen.exe"],
        "helium": [rf"{local}\imput\Helium\Application\chrome.exe", rf"{local}\Helium\Application\helium.exe",
                   rf"{pf}\Helium\Application\helium.exe"],
        "librewolf": [rf"{pf}\LibreWolf\librewolf.exe", rf"{local}\LibreWolf\librewolf.exe"],
        "firefox": [rf"{pf}\Mozilla Firefox\firefox.exe", rf"{pf86}\Mozilla Firefox\firefox.exe"],
        "brave": [rf"{pf}\BraveSoftware\Brave-Browser\Application\brave.exe",
                  rf"{local}\BraveSoftware\Brave-Browser\Application\brave.exe"],
        "vivaldi": [rf"{local}\Vivaldi\Application\vivaldi.exe", rf"{pf}\Vivaldi\Application\vivaldi.exe"],
        "chrome": [rf"{pf}\Google\Chrome\Application\chrome.exe", rf"{pf86}\Google\Chrome\Application\chrome.exe",
                   rf"{local}\Google\Chrome\Application\chrome.exe"],
        "edge": [rf"{pf86}\Microsoft\Edge\Application\msedge.exe", rf"{pf}\Microsoft\Edge\Application\msedge.exe"],
    }.get(name, [])


_browser_cache = {}


def BrowserPath(name="auto"):
    """Path to an installed browser. name='auto' picks the first installed one in BROWSER_ORDER."""
    name = BROWSER_ALIASES.get(name.lower().strip(), name.lower().strip())
    if name == "auto":
        return next((path for path in map(BrowserPath, BROWSER_ORDER) if path), None)
    if name not in BROWSERS:
        return None
    if name not in _browser_cache:
        exe, commands = BROWSERS[name]
        if IS_WINDOWS:
            found = next((c for c in _windows_browser_candidates(name) if os.path.exists(c)), None)
            found = found or _find_in_app_paths(exe[:-4])
        else:
            found = next((path for path in map(shutil.which, commands) if path), None)
        _browser_cache[name] = found
    return _browser_cache[name]


def OpenUrl(url, browser=None):
    """Open a URL in the named browser, else the Browser setting (.env), else the system default."""
    if not re.match(r"^[a-z]+://", url):
        url = "https://" + url
    browser = (browser or Browser).lower()
    path = None if browser == "default" else (BrowserPath(browser) or BrowserPath(Browser) or BrowserPath("auto"))
    if path:
        subprocess.Popen([path, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        webbrowser.open(url)
    return f"Opened {url}"


def OpenBrowser(browser=None):
    browser = browser or (Browser if Browser != "default" else "auto")
    path = BrowserPath(browser) or BrowserPath("auto")
    if not path:
        webbrowser.open("about:blank")
        return f"{browser} not found; opened the default browser"
    subprocess.Popen([path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return f"Opened {browser}"


SEARCH_URLS = {
    "google": "https://www.google.com/search?q={}",
    "youtube": "https://www.youtube.com/results?search_query={}",
    "bing": "https://www.bing.com/search?q={}",
    "github": "https://github.com/search?q={}",
    "amazon": "https://www.amazon.com/s?k={}",
    "wikipedia": "https://en.wikipedia.org/w/index.php?search={}",
}


def Search(query, site="google"):
    site = site.lower().strip()
    template = SEARCH_URLS.get(site)
    if template:
        return OpenUrl(template.format(quote_plus(query)))
    return OpenUrl(SEARCH_URLS["google"].format(quote_plus(f"site:{site} {query}")))


def FirstYoutubeVideo(query):
    html = requests.get(SEARCH_URLS["youtube"].format(quote_plus(query)),
                        headers={"User-Agent": USER_AGENT}, timeout=10).text
    match = re.search(r'"videoId":"([\w-]{11})"', html) or re.search(r"watch\?v=([\w-]{11})", html)
    return f"https://www.youtube.com/watch?v={match.group(1)}" if match else None


def PlayYoutube(query):
    """Play the first YouTube result for `query` (or open the results page)."""
    try:
        url = FirstYoutubeVideo(query)
    except requests.RequestException:
        url = None
    if url:
        OpenUrl(url)
        return f"Playing the first YouTube result for '{query}': {url}"
    Search(query, "youtube")
    return f"Opened YouTube results for '{query}'"


BROWSER_KEYS = {
    "new tab": ("ctrl", "t"), "close tab": ("ctrl", "w"), "reopen tab": ("ctrl", "shift", "t"),
    "next tab": ("ctrl", "tab"), "previous tab": ("ctrl", "shift", "tab"),
    "refresh": ("f5",), "back": ("alt", "left"), "forward": ("alt", "right"),
    "address bar": ("ctrl", "l"), "find": ("ctrl", "f"),
}


def BrowserAction(action):
    keys = BROWSER_KEYS.get(action.lower().strip())
    if not keys:
        return f"Unknown browser action '{action}'. Use one of: {', '.join(BROWSER_KEYS)}"
    gui().hotkey(*keys)
    return f"Browser: {action}"


# --------------------------------------------------------------------------
# Media
# --------------------------------------------------------------------------

MEDIA_KEYS = {
    "play": "playpause", "pause": "playpause", "play pause": "playpause", "toggle": "playpause",
    "next": "nexttrack", "skip": "nexttrack", "previous": "prevtrack",
    "volume up": "volumeup", "volume down": "volumedown", "mute": "volumemute", "unmute": "volumemute",
}


def Media(action):
    action = action.lower().strip()
    if action in ("rewind", "back 10"):
        gui().press("j")      # YouTube: back 10 s (works in most web players: left = 5 s)
        return "Rewound"
    if action in ("forward", "fast forward"):
        gui().press("l")
        return "Fast-forwarded"
    if action in ("close", "stop", "close media"):
        gui().hotkey("ctrl", "w")
        return "Closed the media tab"
    key = MEDIA_KEYS.get(action)
    if not key:
        return f"Unknown media action '{action}'"
    if not IS_WINDOWS and shutil.which("pactl") and key.startswith("volume"):
        args = {"volumeup": ["set-sink-volume", "@DEFAULT_SINK@", "+10%"],
                "volumedown": ["set-sink-volume", "@DEFAULT_SINK@", "-10%"],
                "volumemute": ["set-sink-mute", "@DEFAULT_SINK@", "toggle"]}[key]
        subprocess.run(["pactl", *args], check=False)
    else:
        gui().press(key)
    return f"Media: {action}"


# --------------------------------------------------------------------------
# Apps
# --------------------------------------------------------------------------

_SHORTCUTS = None


def _shortcut_index():
    global _SHORTCUTS
    if _SHORTCUTS is None:
        dirs = [r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs",
                os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs")]
        _SHORTCUTS = [(os.path.splitext(os.path.basename(p))[0].lower(), p)
                      for d in dirs for p in glob.glob(os.path.join(d, "**", "*.lnk"), recursive=True)]
    return _SHORTCUTS


def _find_shortcut(app):
    app = app.lower().strip()
    index = _shortcut_index()
    for test in (lambda n: n == app, lambda n: app in n, lambda n: n in app):
        for name, path in index:
            if test(name):
                return path
    return None


def _find_in_app_paths(app):
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            key_path = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{app.strip()}.exe"
            with winreg.OpenKey(root, key_path) as key:
                exe, _ = winreg.QueryValueEx(key, "")
                if exe and os.path.exists(exe):
                    return exe
        except OSError:
            continue
    return None


def OpenApp(app):
    app = app.strip()
    name = BROWSER_ALIASES.get(app.lower(), app.lower())
    if name in BROWSERS or name in ("browser", "web browser"):
        return OpenBrowser(None if name in ("browser", "web browser") else name)
    if IS_WINDOWS:
        lnk = _find_shortcut(app)
        if lnk:
            os.startfile(lnk)
            return f"Opened {app}"
        exe = _find_in_app_paths(app)
        if exe:
            subprocess.Popen([exe])
            return f"Opened {app}"
        try:
            from AppOpener import open as appopen
            appopen(app, match_closest=True, output=False, throw_errors=True)
            return f"Opened {app}"
        except Exception:
            pass
    else:
        exe = shutil.which(app.lower().replace(" ", "-")) or shutil.which(app.lower().replace(" ", ""))
        if exe:
            subprocess.Popen([exe], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return f"Opened {app}"
    # Not installed: treat it as a website.
    domain = app.lower().replace(" ", "")
    return OpenUrl(domain if "." in domain else f"{domain}.com") + f" (no installed app named '{app}')"


def CloseApp(app):
    app = app.strip()
    if IS_WINDOWS:
        try:
            from AppOpener import close as appclose
            appclose(app, match_closest=True, output=False, throw_errors=True)
            return f"Closed {app}"
        except Exception:
            pass
        lnk = _find_shortcut(app)
        exe_name = os.path.splitext(os.path.basename(lnk))[0] if lnk else app
        result = subprocess.run(["taskkill", "/IM", f"{exe_name}.exe"], capture_output=True)
        return f"Closed {app}" if result.returncode == 0 else f"Couldn't find a running '{app}'"
    result = subprocess.run(["pkill", "-i", "-x", app.lower().replace(" ", "")], check=False)
    return f"Closed {app}" if result.returncode == 0 else f"Couldn't find a running '{app}'"


# --------------------------------------------------------------------------
# Windows (minimize / maximize / switch)
# --------------------------------------------------------------------------

def ActiveWindowTitle():
    try:
        if IS_WINDOWS:
            import pygetwindow
            window = pygetwindow.getActiveWindow()
            return window.title if window else ""
        if shutil.which("xdotool"):
            return subprocess.run(["xdotool", "getactivewindow", "getwindowname"],
                                  capture_output=True, text=True, timeout=2).stdout.strip()
    except Exception:
        pass
    return ""


def ListWindows():
    if IS_WINDOWS:
        import pygetwindow
        return [t for t in pygetwindow.getAllTitles() if t.strip()]
    if shutil.which("wmctrl"):
        out = subprocess.run(["wmctrl", "-l"], capture_output=True, text=True).stdout
        return [line.split(None, 3)[-1] for line in out.splitlines() if len(line.split(None, 3)) == 4]
    return []


def _find_window(title):
    import pygetwindow
    title = title.lower().strip()
    matches = [w for w in pygetwindow.getAllWindows() if w.title and title in w.title.lower()]
    return matches[0] if matches else None


def WindowAction(action, title=""):
    """action: minimize | maximize | restore | focus | close | minimize all"""
    action = action.lower().strip()
    if action in ("minimize all", "show desktop"):
        gui().hotkey("win", "d") if IS_WINDOWS else gui().hotkey("super", "d")
        return "Showed the desktop"
    if action == "switch":
        gui().hotkey("alt", "tab")
        return "Switched window"
    if not IS_WINDOWS:
        if not shutil.which("wmctrl"):
            return "Window control needs wmctrl on Linux"
        flag = {"minimize": ["-r", title, "-b", "add,hidden"], "maximize": ["-r", title, "-b", "add,maximized_vert,maximized_horz"],
                "restore": ["-r", title, "-b", "remove,maximized_vert,maximized_horz"], "focus": ["-a", title], "close": ["-c", title]}.get(action)
        if not flag:
            return f"Unknown window action '{action}'"
        subprocess.run(["wmctrl", *flag], check=False)
        return f"{action} '{title}'"
    window = _find_window(title) if title else None
    if window is None:
        import pygetwindow
        window = pygetwindow.getActiveWindow() if not title else None
    if window is None:
        return f"No window matching '{title}'. Open windows: {', '.join(ListWindows()[:15])}"
    if action == "minimize":
        window.minimize()
    elif action == "maximize":
        window.maximize()
    elif action == "restore":
        window.restore()
    elif action == "focus":
        if window.isMinimized:
            window.restore()
        window.activate()
    elif action == "close":
        window.close()
    else:
        return f"Unknown window action '{action}'"
    return f"{action.capitalize()}d '{window.title}'" if action != "focus" else f"Focused '{window.title}'"


# --------------------------------------------------------------------------
# Mouse & keyboard
# --------------------------------------------------------------------------

def MouseInCorner(margin=2):
    """True when the mouse sits in a screen corner: the user's emergency stop."""
    try:
        g = gui()
        x, y = g.position()
        w, h = g.size()
    except Exception:
        return False
    return (x <= margin or x >= w - 1 - margin) and (y <= margin or y >= h - 1 - margin)


def ScreenSize():
    return tuple(gui().size())


def MouseMove(x, y):
    gui().moveTo(int(x), int(y), duration=0.2)
    return f"Moved the mouse to ({int(x)}, {int(y)})"


def MouseClick(x=None, y=None, button="left", clicks=1):
    if x is not None and y is not None:
        gui().click(int(x), int(y), clicks=int(clicks), interval=0.1, button=button)
        return f"{button.capitalize()}-clicked x{clicks} at ({int(x)}, {int(y)})"
    gui().click(clicks=int(clicks), interval=0.1, button=button)
    return f"{button.capitalize()}-clicked x{clicks} at the cursor"


def MouseDrag(x1, y1, x2, y2):
    g = gui()
    g.moveTo(int(x1), int(y1), duration=0.2)
    g.dragTo(int(x2), int(y2), duration=0.5, button="left")
    return f"Dragged from ({int(x1)}, {int(y1)}) to ({int(x2)}, {int(y2)})"


def Scroll(amount):
    gui().scroll(int(amount))
    return f"Scrolled {'up' if int(amount) > 0 else 'down'} {abs(int(amount))}"


def TypeText(text):
    """Type text. Non-ASCII text is pasted via the clipboard."""
    if text.isascii():
        gui().write(text, interval=0.02)
    else:
        from PyQt5.QtWidgets import QApplication
        clipboard = QApplication.clipboard() if QApplication.instance() else None
        if clipboard is None:
            return "Can't type non-ASCII text without the GUI clipboard"
        clipboard.setText(text)
        gui().hotkey("ctrl", "v")
    return f"Typed {len(text)} characters"


def PressKeys(keys):
    """'enter', 'ctrl+l', 'alt+tab', 'win+d' ..."""
    parts = [k.strip().lower() for k in re.split(r"[+ ]+", keys) if k.strip()]
    aliases = {"control": "ctrl", "windows": "win", "escape": "esc", "return": "enter", "del": "delete"}
    parts = [aliases.get(p, p) for p in parts]
    gui().hotkey(*parts) if len(parts) > 1 else gui().press(parts[0])
    return f"Pressed {'+'.join(parts)}"


# --------------------------------------------------------------------------
# Screen
# --------------------------------------------------------------------------

MAX_SHOT_WIDTH = 1280


def Screenshot():
    """PNG bytes of the primary screen, downscaled to <= 1280 px wide, and the scale factor."""
    import mss
    from PIL import Image
    with mss.mss() as sct:
        raw = sct.grab(sct.monitors[1])
        image = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
    scale = 1.0
    if image.width > MAX_SHOT_WIDTH:
        scale = image.width / MAX_SHOT_WIDTH
        image = image.resize((MAX_SHOT_WIDTH, round(image.height / scale)))
    buffer = io.BytesIO()
    image.save(buffer, "PNG", optimize=True)
    return buffer.getvalue(), scale


def UiElements(limit=60):
    """Clickable controls of the foreground window with screen coordinates (Windows only)."""
    if not IS_WINDOWS:
        return []
    try:
        from pywinauto import Desktop as UiaDesktop
        import pygetwindow
        active = pygetwindow.getActiveWindow()
        if not active:
            return []
        window = UiaDesktop(backend="uia").window(handle=active._hWnd)
        wanted = {"Button", "Hyperlink", "MenuItem", "Edit", "ListItem", "TabItem", "CheckBox", "ComboBox", "TreeItem"}
        elements = []
        for ctrl in window.descendants():
            info = ctrl.element_info
            if info.control_type not in wanted or not info.visible:
                continue
            name = (info.name or "").strip()
            rect = info.rectangle
            if not name or rect.width() <= 0 or rect.height() <= 0:
                continue
            elements.append({"id": len(elements), "type": info.control_type, "name": name[:80],
                             "x": rect.left + rect.width() // 2, "y": rect.top + rect.height() // 2})
            if len(elements) >= limit:
                break
        return elements
    except Exception as e:
        print(f"[Desktop] UI automation unavailable: {e}")
        return []


def Wait(seconds):
    time.sleep(max(0.0, min(float(seconds), 15.0)))
    return f"Waited {float(seconds):.1f}s"
