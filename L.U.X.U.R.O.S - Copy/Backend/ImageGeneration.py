import asyncio
import re
import sys
from random import randint

from huggingface_hub import InferenceClient
from PIL import Image

from Backend.Common import DATA_DIR, ImageModel, RequireEnv

IMAGE_COUNT = 4


def ImagePaths(prompt):
    stem = re.sub(r"[^a-z0-9]+", "_", prompt.lower()).strip("_")[:60] or "image"
    return [DATA_DIR / f"{stem}_{i}.jpg" for i in range(1, IMAGE_COUNT + 1)]


def open_images(paths):
    for image_path in paths:
        try:
            Image.open(image_path).show()
        except OSError:
            print(f"Could not open image: {image_path}")


async def generate_images(prompt):
    # provider="auto" routes to whichever Hugging Face inference provider serves the model.
    client = InferenceClient(provider="auto", token=RequireEnv("HuggingFaceAPIKey", "HUGGINGFACE_API_KEY"))
    tasks = [
        asyncio.to_thread(
            client.text_to_image,
            f"{prompt}, quality=4K, sharpness=maximum, Ultra High details, high resolution",
            model=ImageModel,
            seed=randint(0, 1_000_000),
        )
        for _ in range(IMAGE_COUNT)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    saved = []
    for path, result in zip(ImagePaths(prompt), results):
        if isinstance(result, Exception):
            print(f"Image generation failed: {result}")
            continue
        result.convert("RGB").save(path, "JPEG", quality=95)
        saved.append(path)
    return saved


def GenerateImage(prompt: str):
    prompt = prompt.removeprefix("generate image").strip()
    saved = asyncio.run(generate_images(prompt))
    open_images(saved)
    return bool(saved)


if __name__ == "__main__":
    GenerateImage(" ".join(sys.argv[1:]) or input("Image prompt: "))
