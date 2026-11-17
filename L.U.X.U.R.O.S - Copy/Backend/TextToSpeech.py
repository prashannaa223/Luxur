import asyncio
import os
import random
import subprocess
import threading

import edge_tts
import pygame

from Backend.Common import AssistantVoice, DATA_DIR, IS_WINDOWS

MP3_PATH = str(DATA_DIR / "speech.mp3")
WAV_PATH = str(DATA_DIR / "speech.wav")

# Only one voice at a time (answers, Focus warnings and reminders share the speaker).
_speak_lock = threading.Lock()


async def _edge_save(text) -> bool:
    try:
        if os.path.exists(MP3_PATH):
            os.remove(MP3_PATH)
        communicate = edge_tts.Communicate(text, AssistantVoice, pitch="+5Hz", rate="+13%")
        await asyncio.wait_for(communicate.save(MP3_PATH), timeout=25)
        return os.path.exists(MP3_PATH) and os.path.getsize(MP3_PATH) > 0
    except Exception as e:
        print(f"edge-tts failed: {e}")
        return False


def _windows_tts(text) -> bool:
    """Offline fallback: Windows System.Speech synthesizes a WAV."""
    if not IS_WINDOWS:
        return False
    try:
        if os.path.exists(WAV_PATH):
            os.remove(WAV_PATH)
        esc = text.replace("'", "''")
        ps = (
            "Add-Type -AssemblyName System.Speech;"
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
            f"$s.SetOutputToWaveFile('{os.path.abspath(WAV_PATH)}');"
            f"$s.Speak('{esc}');"
            "$s.Dispose()"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            capture_output=True, timeout=30,
        )
        return os.path.exists(WAV_PATH) and os.path.getsize(WAV_PATH) > 0
    except Exception as e:
        print(f"Windows TTS failed: {e}")
        return False


def TTS(Text, func=lambda r=None: True):
    with _speak_lock:
        return _tts(Text, func)


def _tts(Text, func):
    """Speak `Text` (edge-tts, offline Windows voice as fallback).
    `func()` returning False stops playback early."""
    audio_path = None

    if asyncio.run(_edge_save(Text)):
        audio_path = MP3_PATH
    elif _windows_tts(Text):
        audio_path = WAV_PATH
    if not audio_path:
        print("Error in TTS: no engine produced audio")
        return False

    try:
        pygame.mixer.init()
        pygame.mixer.music.load(audio_path)
        pygame.mixer.music.play()

        clock = pygame.time.Clock()
        while pygame.mixer.music.get_busy():
            if func() is False:
                break
            clock.tick(10)
        return True
    except Exception as e:
        print(f"Error in TTS: {e}")
        return False
    finally:
        try:
            func(False)
            if pygame.mixer.get_init():
                pygame.mixer.music.stop()
                pygame.mixer.music.unload()
                pygame.mixer.quit()
        except Exception as e:
            print(f"Error in TTS cleanup: {e}")


def TextToSpeech(Text, func=lambda r=None: True):
    Data = str(Text).split(".")

    responses = [
        "The rest of the result has been printed to the chat screen, kindly check it out sir.",
        "The rest of the text is now on the chat screen, sir, please check it.",
        "You can see the rest of the text on the chat screen, sir.",
        "The remaining part of the text is now on the chat screen, sir.",
        "Sir, you'll find more text on the chat screen for you to see.",
        "The rest of the answer is now on the chat screen, sir.",
        "Sir, please look at the chat screen, the rest of the answer is there.",
        "You'll find the complete answer on the chat screen, sir.",
        "The next part of the text is on the chat screen, sir.",
        "Sir, please check the chat screen for more information.",
        "There's more text on the chat screen for you, sir.",
        "Sir, take a look at the chat screen for additional text.",
        "You'll find more to read on the chat screen, sir.",
        "Sir, check the chat screen for the rest of the text.",
        "The chat screen has the rest of the text, sir.",
        "There's more to see on the chat screen, sir, please look.",
        "Sir, the chat screen holds the continuation of the text.",
        "You'll find the complete answer on the chat screen, kindly check it out sir.",
        "Please review the chat screen for the rest of the text, sir.",
        "Sir, look at the chat screen for the complete answer."
    ]

    if len(Data) > 4 and len(Text) >= 250:
        TTS(" ".join(Text.split(".")[0:2]) + ". " + random.choice(responses), func)
    else:
        TTS(Text, func)


if __name__ == "__main__":
    while True:
        TextToSpeech(input("Enter text: "))
