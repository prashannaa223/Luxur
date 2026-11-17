"""Speech recognition through Chrome's Web Speech API, driven headlessly by Selenium."""
import atexit
from time import sleep

import mtranslate as mt
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

from Backend.Common import (
    DATA_DIR, InputLanguage, AnnouncementPending, GetMicrophoneStatus, QueryModifier, SetAssistantStatus, TypedPending,
)

HtmlCode = '''<!DOCTYPE html>
<html lang="en">
<head>
    <title>Speech Recognition</title>
</head>
<body>
    <button id="start" onclick="startRecognition()">Start Recognition</button>
    <button id="end" onclick="stopRecognition()">Stop Recognition</button>
    <p id="output"></p>
    <script>
        const output = document.getElementById('output');
        let recognition;
        let listening = false;

        function startRecognition() {
            const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
            recognition = new Recognition();
            recognition.lang = '__LANG__';
            recognition.continuous = true;

            recognition.onresult = function(event) {
                const transcript = event.results[event.results.length - 1][0].transcript;
                output.textContent += transcript;
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

VOICE_HTML = DATA_DIR / "voice.html"

_driver = None


def Driver():
    """Start headless Chrome on first use (Selenium Manager fetches the driver)."""
    global _driver
    if _driver is None:
        VOICE_HTML.write_text(HtmlCode.replace("__LANG__", InputLanguage), encoding="utf-8")
        options = Options()
        options.add_argument("--use-fake-ui-for-media-stream")  # auto-allow microphone access
        options.add_argument("--headless=new")
        _driver = webdriver.Chrome(options=options)
        atexit.register(Shutdown)
    return _driver


def Shutdown():
    """Close headless Chrome so it doesn't outlive the app."""
    global _driver
    if _driver is not None:
        try:
            _driver.quit()
        except Exception:
            pass
        _driver = None


def UniversalTranslator(Text):
    return mt.translate(Text, "en", "auto").capitalize()


def SpeechRecognition():
    """Block until something is heard. Returns "" if the mic is switched off or text is typed first."""
    driver = Driver()
    driver.get(VOICE_HTML.as_uri())
    driver.find_element(by=By.ID, value="start").click()

    try:
        while GetMicrophoneStatus() == "True" and not TypedPending() and not AnnouncementPending():
            Text = driver.find_element(by=By.ID, value="output").text
            if Text:
                if InputLanguage.lower().startswith("en"):
                    return QueryModifier(Text)
                SetAssistantStatus("Translating ...")
                return QueryModifier(UniversalTranslator(Text))
            sleep(0.2)
        return ""
    finally:
        driver.find_element(by=By.ID, value="end").click()


if __name__ == "__main__":
    from Backend.Common import SetMicrophoneStatus
    SetMicrophoneStatus("True")
    while True:
        print(SpeechRecognition())
