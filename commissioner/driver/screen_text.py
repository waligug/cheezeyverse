"""Read only a small button crop using Windows' installed English OCR engine."""
import asyncio
import re


def normalize(text):
    return re.sub(r"[^A-Z]", "", text.upper())


def read(image):
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import SoftwareBitmap, BitmapPixelFormat, BitmapAlphaMode
    import winrt.windows.foundation
    import winrt.windows.foundation.collections
    engine = OcrEngine.try_create_from_language(Language("en-US"))
    if engine is None:
        raise RuntimeError("Windows English OCR is unavailable")
    image = image.resize((image.width * 3, image.height * 3)).convert("RGBA")

    async def recognize():
        with SoftwareBitmap.create_copy_with_alpha_from_buffer(
                image.tobytes("raw", "BGRA"), BitmapPixelFormat.BGRA8,
                image.width, image.height, BitmapAlphaMode.IGNORE) as bitmap:
            result = await asyncio.wait_for(engine.recognize_async(bitmap), timeout=5)
            return normalize(result.text)
    return asyncio.run(recognize())
