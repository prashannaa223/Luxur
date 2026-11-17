# Changes: LUXUR OS update

Everything in this update, in the three commits it ships as. Paths are relative to `L.U.X.U.R.O.S - Copy`.

## Before you update

1. Copy `L.U.X.U.R.O.S - Copy/Data/` and `L.U.X.U.R.O.S - Copy/.env` somewhere safe.
2. Pull or merge the update.
3. Copy `Data/` and `.env` back, then run `start.bat`.
4. Check your keys: `.venv\Scripts\python -m Backend.SelfTest`

## Coming from Luxurv.0beta

This brings in the LUXURv1 additions (the persona, Pulse Hub, the wake word and claps, rest mode, interrupt, the Windows voice fallback, Start-menu lookup) plus everything below. The Cohere client moves from the v1 API (`chat_stream(message=, chat_history=)`) to v2.

## Commit 1: Backend

**New**
- `Backend/LLM.py`: every model call goes through here.
  - Groq: `openai/gpt-oss-120b` for chat and the agent, `gpt-oss-20b` for quick checks, with low reasoning effort.
  - Cohere: v2 client (`ClientV2`) for routing and screen reading.
  - Ollama: optional local router.
- `Backend/Agent.py`: the desktop agent harness (model plans, code executes). 23 tools: apps, windows, browser, search, YouTube, media, mouse, keyboard, screen reading, web research, ask the user, remember. Limits: 25 steps, interrupt, emergency stop.
- `Backend/Desktop.py`: the desktop layer used by the agent and simple commands.
  - Browsers: `auto` picks the first installed of Zen, Helium, LibreWolf, Firefox, Brave, Vivaldi, Chrome, Edge.
  - Windows: Start-menu and App Paths lookup, `pygetwindow` window control, UI Automation element list via `pywinauto`.
  - Linux: thin shim, testing only.
- `Backend/Focus.py`: Focus Mode.
  - Commands: "focus", "focus on <goal>", "focus off".
  - Strict prompt, and off-topic requests get "Focus, bitch. No bullshit."
  - A window-title watcher warns about distractions, then minimizes them after 25 s.
- `Backend/Reminders.py`: reads times like "9pm", "in 10 minutes" or "25th june 9pm" and speaks the reminder when it's due.

**Changed**
- `Backend/PulseHub.py`:
  - Brain-graph queries (`graph`, `node_details`, `graph_version`).
  - Task-memory links, "same conversation" links, and `remember`.
  - Pattern detection for favorite apps, sites and active hours.
  - Reminders, a tool audit log, and telemetry pruning (keeps the last 2,000 rows).
  - Safe migration: removes duplicate links, then adds unique indexes. Existing data is kept.
- `Backend/Model.py`: the router now also knows `agent` (multi-step tasks) and `remember`. It uses the local model when Ollama is running, otherwise Cohere v2. When unsure it falls back to `general` instead of crashing.
- `Backend/Chatbot.py`: Pulse Hub context, strict answers in Focus Mode, and lazy clients. Memory extraction gets a bigger token budget; before, it stored 2 memories in 48 chats.
- `Backend/RealtimeSearchEngine.py`: shared clients, Pulse Hub logging, and search results treated as data.
- `Backend/Automation.py`: one-step commands now delegate to `Desktop.py`. `pywhatkit` and `shell=True` are gone, and Windows-only imports are guarded.
- `Backend/ImageGeneration.py`: `huggingface_hub` `InferenceClient` (the old endpoint is dead).
- `Backend/TextToSpeech.py`: `Data\` paths fixed, the Windows voice fallback runs only on Windows, and one voice at a time.
- `Backend/WakeListener.py`: removed the fake-microphone flag, so the wake word can actually hear you.
- `Backend/SpeakToText.py`: stops listening when you type or when LUXUR has something to announce.
- `Backend/Common.py`:
  - New state: Focus, approval requests (unique ids, deny after 60 s), typed input, the announcement queue.
  - New settings: models, `Browser`, `ScreenReading`.
  - IPv4-first network fix, applied everywhere.
- `Backend/Requirement.txt`: added `psutil`, `numpy`, `sounddevice`, `mss`, `pyautogui`; Windows-only `pygetwindow` and `pywinauto`. Removed `keyboard`.
- `.env.example`: all settings, with comments.

## Commit 2: App and UI

- `main.py`:
  - Routes Focus commands, the off-topic gate, `agent`, `remember` and reminders.
  - Background notices are queued and spoken between turns.
  - Approvals are spoken as well as shown.
  - Clears a stale interrupt each turn, and tasks no longer stay stuck as "executing".
- `Frontend/GUI.py`:
  - **Pulse Hub tab** (`Ctrl+3`): a force-directed graph with details panel, search, a pulse on new nodes, and a badge when it changes in the background.
  - **Focus theme:** the whole UI turns red, with a focus pill in the top bar.
  - **Approval dialog:** always on top, Deny is the default, auto-deny after 60 s.
  - **Typed command box** in the Chat tab.
  - The mic button also stops a running agent task.
- `Frontend/Graphics/pulse.svg`: new tab icon.

## Commit 3: Repo and setup

- `start.bat` (Windows) and `start.sh` (Linux): on first run they create `.venv`, install requirements (again whenever they change), and create `.env`.
- `README.md`: rewritten short and practical. `Backend/SelfTest.py`: one live call per API, PASS/FAIL, never prints keys.
- `.gitignore` and `.gitattributes`: runtime data and caches are no longer tracked. Text is stored with LF; Windows checkouts get CRLF.
- `CHANGES.md`: this file.

## Safety and privacy

- **Asks first:**
  - Deleting, sending, posting, buying, passwords, logins, `Alt+F4` and `Delete`.
  - Any Enter, newline or click in chat, mail, shopping or payment windows, or when the goal is to send or buy.
  - Logins are handed to you; it never types passwords.
- **Best-effort:** this check is not a guarantee, so watch what the agent does.
- **Screen reading:** whole-screen screenshots go to Cohere, only during a task you asked for. Skipped when the active window looks private. `ScreenReading=off` disables it.
- **Focus Mode:** reads only window titles, on your PC. Unknown titles are judged only by the local model.
- **Prompt injection:** screen and web text is treated as data, never as instructions.
- **Audit log:** typed text is redacted.
- **After you approve:** focus goes back to the window the step was meant for. If that fails, the step is skipped.
- **Never types into LUXUR itself.**
- **Stop:** tap the mic button, or move the mouse into a screen corner.

## Tested

- 14 test groups with stubbed models and a fake desktop: router, hybrid fallback, agent loop, safety gate, focus handling, privacy, limits, Focus Mode, reminders, approvals, Pulse Hub graph and patterns, automation.
- The `main.py` flow end to end, plus migration of a real Pulse Hub database with all rows kept.
- GUI rendered offscreen in every state at 1280x800 and 780x560.
- `start.sh` from a clean copy; `start.bat` under Wine up to the Python check.
- All code compiles on Python 3.11.

**Not run yet:** the Windows desktop layer, live Groq, Cohere and Hugging Face calls (run `Backend.SelfTest`), `start.bat` past the Python check, a real local model, wake word and claps, and the approval dialog on a real desktop.
