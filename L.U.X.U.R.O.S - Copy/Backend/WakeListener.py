"""Wakes the GUI when minimized.

Triggers:
- Saying "LUXUR" / "wake up" (headless-Chrome speech recognition, active only
  while the window is minimized and the mic button is off).
- Clapping three times (adaptive-threshold onset detection on the mic).
"""
import threading
import time
from collections import deque

from Backend.Common import DATA_DIR, GetMicrophoneStatus, _read, _write

WAKE_WORDS = ("luxur", "lucifer", "luxor", "luxus", "wake up", "wakeup")

WAKE_HTML = DATA_DIR / "wake.html"
MINIMIZED_FILE = "Minimized.data"
WAKE_FILE = "Wake.data"

HtmlCode = '''<!DOCTYPE html>
<html lang="en">
<head>
    <title>Wake</title>
</head>
<body>
    <button id="start" onclick="startRecognition()">Start</button>
    <button id="end" onclick="stopRecognition()">End</button>
    <p id="output"></p>
    <script>
        const output = document.getElementById('output');
        let recognition;
        let listening = false;

        function startRecognition() {
            const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
            recognition = new Recognition();
            recognition.lang = 'en';
            recognition.continuous = true;
            recognition.onresult = function(event) {
                output.textContent += event.results[event.results.length - 1][0].transcript;
            };
            recognition.onend = function() {
                if (listening) recognition.start();
            };
            listening = true;
            recognition.start();
        }

        function stopRecognition() {
            listening = false;
            if (recognition) recognition.stop();
            output.textContent = "";
        }
    </script>
</body>
</html>'''


def _minimized():
    return _read(MINIMIZED_FILE).strip() == "True"


def _trigger(source):
    _write(WAKE_FILE, "True")
    print(f"[WakeListener] wake ({source})")


def _is_wake_phrase(text):
    t = text.lower()
    return any(w in t for w in WAKE_WORDS)


# --------------------------------------------------------------------------
# Wake word via Chrome speech recognition (only while minimized & mic off)
# --------------------------------------------------------------------------

_driver = None


def _get_driver():
    global _driver
    if _driver is None:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        options = Options()
        options.add_argument("--use-fake-ui-for-media-stream")  # auto-allow the real microphone
        options.add_argument("--headless=new")
        _driver = webdriver.Chrome(options=options)
    return _driver


def _quit_driver():
    global _driver
    if _driver is not None:
        try:
            _driver.quit()
        except Exception:
            pass
        _driver = None


def wake_word_loop():
    from selenium.webdriver.common.by import By
    while True:
        if not _minimized() or GetMicrophoneStatus() == "True":
            _quit_driver()
            time.sleep(1)
            continue
        try:
            driver = _get_driver()
            driver.get(WAKE_HTML.as_uri())
            driver.find_element(by=By.ID, value="start").click()
            last_reset = time.time()
            while _minimized() and GetMicrophoneStatus() != "True":
                try:
                    text = driver.find_element(by=By.ID, value="output").text
                except Exception:
                    break
                if _is_wake_phrase(text):
                    _trigger("wake word")
                    _quit_driver()
                    break
                if time.time() - last_reset > 15:
                    driver.find_element(by=By.ID, value="end").click()
                    driver.find_element(by=By.ID, value="start").click()
                    last_reset = time.time()
                time.sleep(0.5)
            try:
                driver.find_element(by=By.ID, value="end").click()
            except Exception:
                pass
            _quit_driver()
        except Exception as e:
            print(f"[WakeListener] wake-word error: {e}")
            _quit_driver()
            time.sleep(3)


# --------------------------------------------------------------------------
# Triple clap detection
# --------------------------------------------------------------------------

def _clap_loop():
    try:
        import numpy as np
        import sounddevice as sd
    except Exception as e:
        print(f"[WakeListener] clap detection unavailable: {e}")
        return

    SAMPLE_RATE = 16000
    BLOCK = 400
    rms_q = deque(maxlen=4)
    background = deque(maxlen=200)
    claps = deque()
    cooldown_until = 0.0

    def callback(indata, frames, t, status):
        rms_q.append(float(np.sqrt(np.mean(indata.astype(np.float64) ** 2))))

    with sd.InputStream(samplerate=SAMPLE_RATE, blocksize=BLOCK,
                        channels=1, dtype="float32", callback=callback):
        while True:
            time.sleep(0.05)
            if not rms_q:
                continue
            rms = max(rms_q)
            rms_q.clear()
            now = time.time()

            if len(background) >= 40:
                noise = float(np.median(background))
                threshold = max(noise * 5.0, 0.02)
                if rms > threshold:
                    if not claps or now - claps[-1] > 0.25:
                        claps.append(now)
                        background.clear()
                    if len(claps) >= 3 and now - claps[0] <= 2.5:
                        claps.clear()
                        if now >= cooldown_until:
                            cooldown_until = now + 6.0
                            _trigger("3 claps")
                    while claps and now - claps[0] > 2.5:
                        claps.popleft()
                    continue
            background.append(rms)
            while claps and now - claps[0] > 2.5:
                claps.popleft()


def StartWakeListener():
    WAKE_HTML.write_text(HtmlCode, encoding="utf-8")
    _write(WAKE_FILE, "False")
    if not _read(MINIMIZED_FILE).strip():
        _write(MINIMIZED_FILE, "False")
    threading.Thread(target=wake_word_loop, daemon=True).start()
    threading.Thread(target=_clap_loop, daemon=True).start()
    print("[WakeListener] started (say 'LUXUR'/'wake up' or clap 3x while minimized)")


if __name__ == "__main__":
    _write(MINIMIZED_FILE, "True")
    StartWakeListener()
    while True:
        time.sleep(1)
