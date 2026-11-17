# LUXUR OS

An AI that lives on your desktop. Talk or type: it answers, controls your computer, remembers what matters, and keeps you focused.

## Start

- **Windows:** double-click `start.bat`
- **Linux (testing only):** `./start.sh`

The first run installs everything (a few minutes) and opens `.env`. Paste your keys, save, run it again.

| Key | Get it free at | Used for |
| --- | --- | --- |
| `GroqAPIKey` | console.groq.com/keys | Chat, answers, the desktop agent |
| `CohereAPIKey` | dashboard.cohere.com/api-keys | Understanding commands, reading the screen |
| `HuggingFaceAPIKey` *(optional)* | huggingface.co/settings/tokens | Image generation |

Check your keys: in `L.U.X.U.R.O.S - Copy`, run `.venv\Scripts\python -m Backend.SelfTest` (Windows) or `.venv/bin/python -m Backend.SelfTest` (Linux).

## Use it

Press the mic button (`Ctrl+M`) and talk, or type in the **Chat** tab.

| Say | What happens |
| --- | --- |
| "Open Zen, play lo-fi on YouTube, then minimize it" | The desktop agent does it, step by step |
| "Research the best laptops under $1000 and compare them" | Browses, compares, sums up |
| "Remember my exam is on Friday" | Saved to Pulse Hub |
| "Remind me at 9pm to call mom" | Speaks up at 9pm |
| "Focus on my essay" / "Focus off" | Red UI, no distractions, strict answers |
| "Rest" | Minimizes; say "Luxur" or clap 3 times to wake it |

**Pulse Hub** (`Ctrl+3`) shows LUXUR's memory as a live graph. Click a node to see what's connected.

**Stop the agent:** tap the mic button, or slam the mouse into a screen corner.

## Privacy and safety

- **Screen reading sends screenshots of the whole screen to Cohere.** It happens only during a task you asked for, and is skipped when the active window looks private (banking, mail, chat, sign-in). Other windows on screen are still visible, so close private ones first. Turn it off with `ScreenReading=off` in `.env`.
- **LUXUR asks before** deleting, sending, posting, buying, passwords and logins, and before pressing Enter or clicking in chat, mail, shopping or payment windows. This check is best-effort, not a guarantee, so keep an eye on what it does.
- **Focus Mode** only reads the active window's title, on your PC.
- **Your data stays local** in `L.U.X.U.R.O.S - Copy/Data` (chat log and the Pulse Hub database). Back it up before updating.

## Settings (`.env`)

- `Browser=auto` picks the first installed of Zen, Helium, LibreWolf, Firefox, Brave, Vivaldi, Chrome, Edge. You can also name one.
- `Username`, `Assistantname`, `InputLanguage`, `AssistantVoice`
- **Free local brain (optional):** install [Ollama](https://ollama.com) and run `ollama pull qwen2.5:1.5b`. LUXUR then sorts your commands on your own PC (faster, offline) and falls back to Cohere when Ollama isn't running.
