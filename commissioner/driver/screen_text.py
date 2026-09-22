"""Read only a small button crop using Windows' installed English OCR engine."""
import asyncio
import re


def normalize(text):
    return re.sub(r"[^A-Z]", "", text.upper())


def read_raw(image):
    """The OCR text as the engine gave it, digits and all.

    `read` normalises to A-Z, which is right for a button label and useless for a date: it
    turns "MARCH 23, 2027" into "MARCH". The Hot Seat calendar heading is the only place the
    game states the day it is actually on, so reading it needs the unnormalised text.
    """
    return _recognise(image, raw=True)


def read(image):
    return _recognise(image)


def _recognise(image, raw=False):
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
            return result.text if raw else normalize(result.text)
    return asyncio.run(recognize())
