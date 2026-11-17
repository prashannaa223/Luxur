"""The router ("first-layer decision-making model").

Turns a request into one or more categories, e.g.
"open chrome and tell me about AI" -> ["open chrome", "general tell me about AI"].
Runs on the local model when Ollama is up (fast, free, offline), else on Cohere v2.
"""
from rich import print

from Backend import LLM

funcs = [
    "exit", "general", "realtime", "open", "close", "play", "generate image", "system", "content",
    "google search", "youtube search", "reminder", "remember", "agent",
]

preamble = """
You are a very accurate Decision-Making Model, which decides what kind of a query is given to you.
You will decide whether a query is a 'general' query, a 'realtime' query, or is asking to perform any task or automation like 'open facebook, instagram', 'can you write a application and open it in notepad'
*** Do not answer any query, just decide what kind of query is given to you. ***
-> Respond with 'general ( query )' if a query can be answered by a llm model (conversational ai chatbot) and doesn't require any up to date information like if the query is 'who was akbar?' respond with 'general who was akbar?', if the query is 'how can i study more effectively?' respond with 'general how can i study more effectively?', if the query is 'can you help me with this math problem?' respond with 'general can you help me with this math problem?', if the query is 'Thanks, i really liked it.' respond with 'general thanks, i really liked it.' , if the query is 'what is python programming language?' respond with 'general what is python programming language?', etc. Respond with 'general (query)' if a query doesn't have a proper noun or is incomplete like if the query is 'who is he?' respond with 'general who is he?', if the query is 'what's his networth?' respond with 'general what's his networth?', if the query is 'tell me more about him.' respond with 'general tell me more about him.', and so on even if it require up-to-date information to answer. Respond with 'general (query)' if the query is asking about time, day, date, month, year, etc like if the query is 'what's the time?' respond with 'general what's the time?'.
-> Respond with 'realtime ( query )' if a query can not be answered by a llm model (because they don't have realtime data) and requires up to date information like if the query is 'who is indian prime minister' respond with 'realtime who is indian prime minister', if the query is 'tell me about facebook's recent update.' respond with 'realtime tell me about facebook's recent update.', if the query is 'tell me news about coronavirus.' respond with 'realtime tell me news about coronavirus.', etc and if the query is asking about any individual or thing like if the query is 'who is akshay kumar' respond with 'realtime who is akshay kumar', if the query is 'what is today's news?' respond with 'realtime what is today's news?', if the query is 'what is today's headline?' respond with 'realtime what is today's headline?', etc.
-> Respond with 'agent ( task )' if the query is a MULTI-STEP task on the computer, needs looking at the screen, clicking things, filling forms, or research that needs browsing and comparing, like 'open chrome, search for focus music on youtube, play the first good result, then minimize it' respond with 'agent open chrome, search for focus music on youtube, play the first good result, then minimize it', or 'research the best laptops under $1000 and compare them' respond with 'agent research the best laptops under $1000 and compare them'. Keep the whole task in ONE 'agent' entry; never split an agent task into other categories.
-> Respond with 'open (application name or website name)' if a query is asking to open any application like 'open facebook', 'open telegram', etc. but if the query is asking to open multiple applications, respond with 'open 1st application name, open 2nd application name' and so on.
-> Respond with 'close (application name)' if a query is asking to close any application like 'close notepad', 'close facebook', etc. but if the query is asking to close multiple applications or websites, respond with 'close 1st application name, close 2nd application name' and so on.
-> Respond with 'play (song name)' if a query is asking to play any song like 'play afsanay by ys', 'play let her go', etc. but if the query is asking to play multiple songs, respond with 'play 1st song name, play 2nd song name' and so on.
-> Respond with 'generate image (image prompt)' if a query is requesting to generate a image with given prompt like 'generate image of a lion', 'generate image of a cat', etc. but if the query is asking to generate multiple images, respond with 'generate image 1st image prompt, generate image 2nd image prompt' and so on.
-> Respond with 'reminder (datetime with message)' if a query is requesting to set a reminder like 'set a reminder at 9:00pm on 25th june for my business meeting.' respond with 'reminder 9:00pm 25th june business meeting'.
-> Respond with 'remember (fact)' if the user asks you to remember or note something, like 'remember that my exam is on friday' respond with 'remember my exam is on friday'.
-> Respond with 'system (task name)' if a query is asking to control the computer itself in ONE simple step: mute, unmute, volume up, volume down, play, pause, next track, previous track, click, double click, right click, scroll up, scroll down, move mouse left, move mouse right, move mouse up, move mouse down, move mouse to (x) (y), type (text), press enter, press escape, press alt tab, press space, new tab, close tab, refresh, go back, go forward, minimize window, maximize window, show desktop, minimize all, task view, screenshot, lock, etc. If the query says 'write hello in notepad' after notepad is open, respond with 'system type hello'. If the query asks to press a key or shortcut like 'press escape' or 'alt tab to previous window' respond with 'system press escape' or 'system press alt tab'. If the query is asking to do multiple tasks, respond with 'system 1st task, system 2nd task', etc.
-> Respond with 'content (topic)' if a query is asking to write any type of content like application, codes, emails or anything else about a specific topic but if the query is asking to write multiple types of content, respond with 'content 1st topic, content 2nd topic' and so on.
-> Respond with 'google search (topic)' if a query is asking to search a specific topic on google but if the query is asking to search multiple topics on google, respond with 'google search 1st topic, google search 2nd topic' and so on.
-> Respond with 'youtube search (topic)' if a query is asking to search a specific topic on youtube but if the query is asking to search multiple topics on youtube, respond with 'youtube search 1st topic, youtube search 2nd topic' and so on.
*** If the query is asking to perform multiple tasks like 'open facebook, telegram and close whatsapp' respond with 'open facebook, open telegram, close whatsapp' ***
*** If the user is saying goodbye or wants to end the conversation like 'bye luxur.' respond with 'exit'.***
*** Respond with 'general (query)' if you can't decide the kind of query or if a query is asking to perform a task which is not mentioned above. ***
"""

ChatHistory = [
    {"role": "user", "message": "How are you?"},
    {"role": "assistant", "message": "general how are you?"},
    {"role": "user", "message": "do you like pizza?"},
    {"role": "assistant", "message": "general do you like pizza?"},
    {"role": "user", "message": "open chrome and tell me about AI?"},
    {"role": "assistant", "message": "open chrome, general tell me about AI?"},
    {"role": "user", "message": "open chrome and firefox?"},
    {"role": "assistant", "message": "open chrome, open firefox"},
    {"role": "user", "message": "what is today's date and by the way remind me to call my mom at 5pm?"},
    {"role": "assistant", "message": "general what is today's date, reminder 5pm call my mom"},
    {"role": "user", "message": "play music and open youtube and search for AI videos?"},
    {"role": "assistant", "message": "play music, open youtube, youtube search AI videos"},
    {"role": "user", "message": "generate image of a cat playing piano?"},
    {"role": "assistant", "message": "generate image a cat playing piano"},
    {"role": "user", "message": "open chrome, log into my email and find the latest invoice"},
    {"role": "assistant", "message": "agent open chrome, log into my email and find the latest invoice"},
    {"role": "user", "message": "remember that I prefer dark mode"},
    {"role": "assistant", "message": "remember I prefer dark mode"},
]


def _is_category(task):
    return any(task == c or task.startswith(f"{c} ") for c in funcs)


def ParseDecision(response, prompt):
    tasks = [t.strip() for t in response.replace("\n", " ").split(",") if t.strip()]
    # An agent task may contain commas: keep everything after "agent" together.
    for i, task in enumerate(tasks):
        if task.lower().startswith("agent "):
            tail = [task] + [t for t in tasks[i + 1:]]
            tasks = [t for t in tasks[:i] if _is_category(t)] + [", ".join(tail)]
            break
    recognized = [t for t in tasks if _is_category(t)]
    if not recognized:
        # The preamble says to fall back to 'general' when unsure; enforce it here too.
        print(f"[yellow]Unrecognized decision {response!r}, treating as general.[/yellow]")
        recognized = [f"general {prompt}"]
    return recognized


def FirstLayerDMM(prompt: str = "test"):
    messages = [{"role": "system", "content": preamble}]
    for entry in ChatHistory:
        messages.append({"role": entry["role"], "content": entry["message"]})
    messages.append({"role": "user", "content": prompt})
    response, engine = LLM.Route(messages)
    print(f"[dim]router: {engine}[/dim]")
    return ParseDecision(response, prompt)


if __name__ == "__main__":
    while True:
        print(FirstLayerDMM(input(">>> ")))
