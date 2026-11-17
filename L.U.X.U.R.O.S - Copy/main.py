import os
import threading
import traceback
from asyncio import run
from time import sleep

from Backend import Focus, Reminders
from Backend.Agent import RunAgent
from Backend.Automation import Automation
from Backend.Chatbot import ChatBot, Hub
from Backend.Common import (
    Assistantname, Username,
    AnswerModifier, ClearInterrupt, GetAssistantStatus, GetMicrophoneStatus, Interrupted, LoadChatLog,
    AnnouncementPending, PopAnnouncements, QueueAnnouncement, RequestConfirmation, _write,
    QueryModifier, ReadTypedQuery, RestoreMicAfterTyped, SetAssistantStatus, SetMicrophoneStatus, ShowTextToScreen, TempDirectoryPath,
)
from Backend.ImageGeneration import GenerateImage
from Backend.Model import FirstLayerDMM
from Backend.RealtimeSearchEngine import RealtimeSearchEngine
from Backend.SpeakToText import Shutdown, SpeechRecognition
from Backend.TextToSpeech import TextToSpeech
from Backend.WakeListener import StartWakeListener
from Frontend.GUI import GraphicalUserInterface

hub = Hub()

DefaultMessage = f'''{Username} : Hello {Assistantname}, How are you?
{Assistantname} : Welcome {Username}. I am doing well. How may I help you?'''

Functions = ["open", "close", "play", "system", "content", "google search", "youtube search"]
REST_PHRASES = ("rest", "take rest", "take a rest", "go to rest", "you can rest", "rest now")
# Requests Focus Mode never blocks.
FOCUS_ALWAYS_OK = ("remember", "reminder", "system", "close", "exit")


def SetStatus(StatusText):
    SetAssistantStatus(StatusText)
    hub.set_voice_state(StatusText)


def ChatLogIntegration():
    lines = []
    for entry in LoadChatLog():
        if entry.get("role") == "user":
            lines.append(f"{Username} : {entry['content']}")
        elif entry.get("role") == "assistant":
            lines.append(f"{Assistantname} : {entry['content']}")
    TempDirectoryPath("Database.data").write_text(AnswerModifier("\n".join(lines)), encoding="utf-8")


def ShowChatsOnGUI():
    history = TempDirectoryPath("Database.data").read_text(encoding="utf-8")
    ShowTextToScreen(history if history.strip() else DefaultMessage)


def InitialExecution():
    SetMicrophoneStatus("False")
    SetStatus("Available ...")
    ShowTextToScreen("")
    ClearInterrupt()
    for leftover in ("TypedInput.data", "TypedRestoreMic.data", "Confirm.data", "ConfirmAnswer.data"):
        _write(leftover, "")   # state left behind by a session that was killed
    _write("Announce.data", "[]")
    Focus.SetFocusState({"active": False})
    TempDirectoryPath("Rest.data").write_text("False", encoding="utf-8")
    ChatLogIntegration()
    ShowChatsOnGUI()
    hub.event_bus.publish("session.boot", {"app": "L.U.X.U.R OS"}, source="main")


def Speak(Answer):
    ShowTextToScreen(f"{Assistantname} : {Answer}")
    SetStatus("Answering ...")
    # Playback stops when the mic button is tapped (interrupt) or the mic is switched off.
    TextToSpeech(Answer, func=lambda r=None: GetMicrophoneStatus() == "True" and not Interrupted())
    ClearInterrupt()


def Announce(Text):
    """Spoken notice from a background service (Focus warnings, reminders): queued, spoken between turns."""
    QueueAnnouncement(Text)


def SpeakAnnouncements():
    for text in PopAnnouncements():
        ShowTextToScreen(f"{Assistantname} : {text}")
        TextToSpeech(text)


def AskUser(question):
    """Approval for the desktop agent: an always-on-top dialog, and LUXUR says it out loud."""
    threading.Thread(target=TextToSpeech, args=("I need your OK. " + question.split("\n")[0],), daemon=True).start()
    return RequestConfirmation(question)


def HandleSpecial(Query, task_id):
    """Rest and Focus commands, matched in code (not by the model). Returns True if handled."""
    if Query.lower().strip().rstrip(".!?") in REST_PHRASES:
        hub.update_task(task_id, "done", {"type": "rest"}, intent="rest")
        ShowTextToScreen(f"{Assistantname} : Resting, sir. Say 'Luxur' or clap three times to wake me.")
        SetStatus("Answering ...")
        TextToSpeech("Resting now, sir. Say Luxur or clap three times to wake me.")
        SetMicrophoneStatus("False")
        TempDirectoryPath("Rest.data").write_text("True", encoding="utf-8")
        SetStatus("Available ...")
        return True
    command = Focus.ParseCommand(Query)
    if command:
        ShowTextToScreen(f"{Username} : {Query}")
        mode, goal = command
        message = Focus.Activate(goal) if mode == "on" else Focus.Deactivate(hub)
        hub.update_task(task_id, "done", {"type": f"focus {mode}", "answer": message}, intent=f"focus {mode}")
        Speak(message)
        return True
    return False


def MainExecution():
    ClearInterrupt()  # a tap on the mic during the last turn must not cancel this one
    TypedQuery = ReadTypedQuery()
    Query = QueryModifier(TypedQuery) if TypedQuery else SpeechRecognition()
    if not Query:
        return False

    task_id = hub.create_task(title=Query[:120], intent="pending")
    if HandleSpecial(Query, task_id):
        return True

    ShowTextToScreen(f"{Username} : {Query}")
    SetStatus("Thinking ...")
    Decision = FirstLayerDMM(Query)
    print(f"\nDecision : {Decision}\n")
    hub.update_task(task_id, "executing", {"decision": Decision}, plan=Decision,
                    intent=Decision[0].split(" ")[0] if Decision else "")

    if Focus.Active() and not all(d.startswith(FOCUS_ALWAYS_OK) for d in Decision) and Focus.IsOffTopic(Query):
        hub.update_task(task_id, "done", {"type": "focus blocked"})
        Speak(Focus.OFF_TOPIC_REPLY)
        return True

    General = [" ".join(i.split()[1:]) for i in Decision if i.startswith("general")]
    Realtime = [" ".join(i.split()[1:]) for i in Decision if i.startswith("realtime")]
    MergedQuery = " and ".join(General + Realtime)
    replies = []

    if any(q.startswith(func) for q in Decision for func in Functions):
        SetStatus("Working ...")
        run(Automation(list(Decision)))
        hub.event_bus.publish("automation.executed", {"commands": Decision})

    for q in Decision:
        if q.startswith("generate image"):
            hub.event_bus.publish("image.generation.started", {"prompt": q})
            threading.Thread(target=GenerateImage, args=(q,), daemon=True).start()
            replies.append("Generating your images now.")
        elif q.startswith("remember "):
            memory_id = hub.remember(q.removeprefix("remember "))
            hub.link_task_memory(task_id, memory_id, "learned")
            replies.append("Noted. I'll remember that.")
        elif q.startswith("reminder "):
            due, message = Reminders.ParseDue(q.removeprefix("reminder "))
            hub.add_reminder(message, due)
            replies.append(f"Reminder set for {due:%I:%M %p on %A}: {message}." if due else
                           f"I couldn't read a time, so I saved it as a note: {message}.")

    agent_tasks = [q.removeprefix("agent ") for q in Decision if q.startswith("agent ")]
    for goal in agent_tasks:
        replies.append(RunAgent(goal, confirm=AskUser))

    if Realtime:
        SetStatus("Searching ...")
        replies.append(RealtimeSearchEngine(QueryModifier(MergedQuery)))
    elif General:
        replies.append(ChatBot(QueryModifier(MergedQuery)))

    answer = " ".join(r for r in replies if r)
    hub.update_task(task_id, "done", {"answer": answer[:500]} if answer else {"type": "automation"})
    if answer:
        Speak(answer)

    if "exit" in Decision:
        Speak("Okay, bye!")
        SetMicrophoneStatus("False")
        hub.shutdown()
        Shutdown()
        os._exit(0)
    return True


def FirstThread():
    while True:
        if AnnouncementPending():
            SpeakAnnouncements()
        if GetMicrophoneStatus() == "True":
            try:
                MainExecution()
                RestoreMicAfterTyped()
            except Exception as e:
                RestoreMicAfterTyped()
                # Stop listening so a persistent failure (missing key, no Chrome, ...) doesn't retry
                # in a loop; the error stays on screen until the mic is used again.
                hub.event_bus.publish("error.main_loop", {"error": str(e)}, level="error")
                traceback.print_exc()
                SetMicrophoneStatus("False")
                SetAssistantStatus(f"Error: {e}")
        else:
            if not GetAssistantStatus().startswith(("Available ...", "Error")):
                SetStatus("Available ...")
            sleep(0.1)


def StartServices():
    StartWakeListener()
    Focus.Watcher(Announce).start()
    Reminders.Scheduler(hub, Announce).start()
    threading.Thread(target=hub.detect_patterns, daemon=True).start()


if __name__ == "__main__":
    InitialExecution()
    StartServices()
    threading.Thread(target=FirstThread, daemon=True).start()
    GraphicalUserInterface()
