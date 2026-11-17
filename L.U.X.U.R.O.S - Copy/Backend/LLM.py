"""Every model call goes through here.

- Groq      : chat, web-search answers, the desktop agent (tool calling).
- Cohere v2 : router fallback and screen reading (command-a-plus accepts images).
- Ollama    : optional local router ("hybrid"); used when it's running, else Cohere.
"""
import base64
import time

import requests

from Backend.Common import (
    CohereModel, GroqFastModel, GroqModel, GroqVisionModel, LocalModel, OllamaURL, RequireEnv,
)

_groq = None
_cohere = None


def GroqClient():
    global _groq
    if _groq is None:
        from groq import Groq
        _groq = Groq(api_key=RequireEnv("GroqAPIKey"))
    return _groq


def CohereClient():
    global _cohere
    if _cohere is None:
        import cohere
        _cohere = cohere.ClientV2(api_key=RequireEnv("CohereAPIKey"))
    return _cohere


def _groq_extra(model):
    # gpt-oss models reason before answering; keep it short so replies stay fast
    # and the token budget isn't spent on thinking.
    return {"reasoning_effort": "low"} if model.startswith("openai/gpt-oss") else {}


# --------------------------------------------------------------------------
# Groq
# --------------------------------------------------------------------------

def Chat(messages, model=None, max_tokens=1024, temperature=0.7):
    """Streamed Groq chat completion; returns the full text."""
    model = model or GroqModel
    completion = GroqClient().chat.completions.create(
        model=model, messages=messages, max_tokens=max_tokens, temperature=temperature,
        top_p=1, stream=True, **_groq_extra(model),
    )
    answer = ""
    for chunk in completion:
        if chunk.choices and chunk.choices[0].delta.content:
            answer += chunk.choices[0].delta.content
    return answer.replace("</s>", "").strip()


def ToolStep(messages, tools, model=None, max_tokens=2048):
    """One agent step: returns the assistant message (may contain tool_calls)."""
    model = model or GroqModel
    completion = GroqClient().chat.completions.create(
        model=model, messages=messages, tools=tools, tool_choice="auto",
        max_tokens=max_tokens, temperature=0.2, **_groq_extra(model),
    )
    return completion.choices[0].message


# --------------------------------------------------------------------------
# Local model (Ollama)
# --------------------------------------------------------------------------

_local_status = {"ok": False, "checked": 0.0}


def LocalAvailable():
    """True when Ollama is running and has LocalModel pulled. Cached for 30 s."""
    if time.time() - _local_status["checked"] < 30:
        return _local_status["ok"]
    ok = False
    try:
        tags = requests.get(f"{OllamaURL}/api/tags", timeout=0.5).json()
        names = {m.get("name", "") for m in tags.get("models", [])}
        ok = LocalModel in names or f"{LocalModel}:latest" in names
        if not ok:
            print(f"[LLM] Ollama is running but '{LocalModel}' isn't pulled: ollama pull {LocalModel}")
    except (requests.RequestException, ValueError):
        ok = False
    _local_status.update(ok=ok, checked=time.time())
    return ok


def LocalChat(messages, max_tokens=200):
    response = requests.post(f"{OllamaURL}/api/chat", json={
        "model": LocalModel, "messages": messages, "stream": False, "keep_alive": "30m",
        "options": {"temperature": 0, "num_predict": max_tokens},
    }, timeout=30)
    response.raise_for_status()
    return response.json()["message"]["content"].strip()


# --------------------------------------------------------------------------
# Cohere v2
# --------------------------------------------------------------------------

def CohereChat(messages, model=None, temperature=0.3):
    """Streamed Cohere v2 chat; returns the full text."""
    stream = CohereClient().chat_stream(model=model or CohereModel, messages=messages,
                                        temperature=temperature)
    text = ""
    for event in stream:
        delta = getattr(event, "delta", None)
        message = getattr(delta, "message", None) if delta is not None else None
        content = getattr(message, "content", None) if message is not None else None
        if content is not None and not isinstance(content, list) and getattr(content, "text", None):
            text += content.text
    return text.strip()


def Route(messages):
    """Router call: local model when available (fast, free, offline), else Cohere."""
    if LocalAvailable():
        try:
            return LocalChat(messages, max_tokens=120), "local"
        except (requests.RequestException, KeyError, ValueError) as e:
            print(f"[LLM] local router failed, using Cohere: {e}")
            _local_status.update(ok=False, checked=time.time())
    return CohereChat(messages, temperature=0.2), "cohere"


def Judge(prompt, max_tokens=300):
    """Short yes/no or one-line judgement: local model if available, else Groq's fast model."""
    messages = [{"role": "user", "content": prompt}]
    if LocalAvailable():
        try:
            return LocalChat(messages, max_tokens=min(max_tokens, 120))
        except (requests.RequestException, KeyError, ValueError):
            pass
    return Chat(messages, model=GroqFastModel, max_tokens=max_tokens, temperature=0)


# --------------------------------------------------------------------------
# Screen reading (vision)
# --------------------------------------------------------------------------

def DescribeImage(png_bytes, question):
    """Ask a vision model about a screenshot. Cohere first, Groq vision as fallback."""
    data_uri = "data:image/png;base64," + base64.b64encode(png_bytes).decode()
    content = [{"type": "text", "text": question},
               {"type": "image_url", "image_url": {"url": data_uri}}]
    try:
        response = CohereClient().chat(model=CohereModel, messages=[{"role": "user", "content": content}],
                                       temperature=0.2)
        return "".join(part.text for part in response.message.content if getattr(part, "text", None)).strip()
    except Exception as e:
        print(f"[LLM] Cohere vision failed, trying Groq vision: {e}")
    completion = GroqClient().chat.completions.create(
        model=GroqVisionModel, messages=[{"role": "user", "content": content}],
        max_tokens=800, temperature=0.2,
    )
    return (completion.choices[0].message.content or "").strip()
