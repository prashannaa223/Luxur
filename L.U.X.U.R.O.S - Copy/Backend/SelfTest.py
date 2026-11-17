"""Live check of every external API LUXUR uses.  Run:  python -m Backend.SelfTest

Makes one small real call per service and prints PASS / FAIL. Keys are never printed.
The vision check uses a generated test image, never a screenshot of your screen.
"""
import io
import json

from PIL import Image, ImageDraw

from Backend import LLM
from Backend.Common import Env, CohereModel, GroqModel, GroqVisionModel, ImageModel, LocalModel


def check(name, fn):
    try:
        detail = fn()
        print(f"PASS  {name}: {str(detail)[:90]}")
        return True
    except Exception as e:
        print(f"FAIL  {name}: {type(e).__name__}: {str(e)[:200]}")
        return False


def groq_chat():
    return LLM.Chat([{"role": "user", "content": "Reply with exactly: ok"}], max_tokens=200)


def groq_tools():
    tools = [{"type": "function", "function": {"name": "open_app", "description": "Open an app",
              "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}}]
    msg = LLM.ToolStep([{"role": "user", "content": "Open notepad."}], tools)
    if not msg.tool_calls:
        raise RuntimeError(f"no tool call returned: {msg.content!r}")
    call = msg.tool_calls[0].function
    return f"{call.name}({json.loads(call.arguments)})"


def cohere_router():
    from Backend.Model import FirstLayerDMM
    return FirstLayerDMM("open chrome and tell me a joke")


def vision(fn):
    def run():
        image = Image.new("RGB", (320, 120), "white")
        ImageDraw.Draw(image).text((20, 45), "LUXUR TEST 42", fill="black")
        buf = io.BytesIO()
        image.save(buf, "PNG")
        return fn(buf.getvalue())
    return run


def cohere_vision(png):
    response = LLM.CohereClient().chat(model=CohereModel, messages=[{"role": "user", "content": [
        {"type": "text", "text": "What text is in this image?"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + __import__("base64").b64encode(png).decode()}}]}])
    return "".join(p.text for p in response.message.content if getattr(p, "text", None))


def groq_vision(png):
    import base64
    completion = LLM.GroqClient().chat.completions.create(model=GroqVisionModel, max_tokens=200, messages=[
        {"role": "user", "content": [{"type": "text", "text": "What text is in this image?"},
                                     {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}}]}])
    return completion.choices[0].message.content


def local_router():
    if not LLM.LocalAvailable():
        return f"skipped (Ollama not running or '{LocalModel}' not pulled)"
    return LLM.LocalChat([{"role": "user", "content": "Reply with exactly: ok"}], max_tokens=10)


def image_model():
    if not Env("HuggingFaceAPIKey", "HUGGINGFACE_API_KEY"):
        return "skipped (no HuggingFaceAPIKey)"
    from huggingface_hub import whoami
    user = whoami(token=Env("HuggingFaceAPIKey", "HUGGINGFACE_API_KEY"))
    return f"token valid (account: {user.get('name', '?')}); image model {ImageModel}"


if __name__ == "__main__":
    print(f"Groq model: {GroqModel} | Cohere model: {CohereModel}\n")
    results = [
        check("Groq chat", groq_chat),
        check("Groq tool calling (desktop agent)", groq_tools),
        check("Router (local or Cohere)", cohere_router),
        check("Cohere vision (screen reading)", vision(cohere_vision)),
        check("Groq vision (fallback)", vision(groq_vision)),
        check("Local model (optional)", local_router),
        check("Hugging Face (optional)", image_model),
    ]
    print(f"\n{sum(results)}/{len(results)} checks passed.")
