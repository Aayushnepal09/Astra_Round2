"""Flatten accepted values onto the original PDF using the existing PDFium engine."""

import ctypes
import io
import unicodedata
from contextlib import closing
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as raw
from PIL import ImageFont


def fail(code):
    # Imported by the isolated pdf_worker entry point.
    from __main__ import InvalidDocument
    raise InvalidDocument(code)


def font_file(configured):
    candidates = [configured] if configured else [
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).is_file():
            return Path(candidate)
    fail("font_unavailable")


def lines_for(text, metrics, size, width):
    lines = []
    for paragraph in text.replace("\r\n", "\n").replace("\r", "\n").expandtabs(4).split("\n"):
        line = ""
        for character in paragraph:
            if metrics.getlength(character) * size / 100 > width:
                return None
            if metrics.getlength(line + character) * size / 100 > width:
                if not line:
                    return None
                lines.append(line)
                line = character
            else:
                line += character
        lines.append(line)
    return lines


def page_matrix(page, u, v):
    left, bottom, right, top = page.get_bbox()
    return {
        0: (1, 0, 0, 1, left + u, top - v),
        90: (0, 1, -1, 0, left + v, bottom + u),
        180: (-1, 0, 0, -1, right - u, bottom + v),
        270: (0, -1, 1, 0, right - v, top - u),
    }[page.get_rotation()]


def complete(path, fields, configured_font):
    font_path = font_file(configured_font)
    metrics = ImageFont.truetype(str(font_path), 100, layout_engine=ImageFont.Layout.BASIC)
    glyphs = ImageFont.truetype(str(font_path), 12)
    missing = bytes(glyphs.getmask("\uffff"))
    with pdfium.PdfDocument(path) as document:
        data = font_path.read_bytes()
        buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        handle = raw.FPDFText_LoadFont(document, buffer, len(data), raw.FPDF_FONT_TRUETYPE, True)
        if not handle:
            fail("font_unavailable")
        with closing(pdfium.PdfFont(handle, document, needs_free=True)) as font:
            for index in range(len(document)):
                with closing(document[index]) as page:
                    width, height = page.get_size()
                    if raw.FPDFPage_Flatten(page, raw.FLAT_NORMALDISPLAY) == raw.FLATTEN_FAIL:
                        fail("processing_failed")
                    for field in fields:
                        if field["page"] != index + 1:
                            continue
                        value = field["value"]
                        text = ("X" if value else "") if field["type"] == "acknowledgement" else value
                        if not text:
                            continue
                        for character in set(text) - set("\n\r\t "):
                            # PDFium's text insertion does not shape bidirectional/complex scripts.
                            if (unicodedata.bidirectional(character) in ("R", "AL", "AN")
                                    or unicodedata.category(character).startswith("M")
                                    or bytes(glyphs.getmask(character)) == missing):
                                fail("unsupported_glyph")
                        box_width, box_height = field["width"] * width, field["height"] * height
                        chosen = None
                        ascent, descent = metrics.getmetrics()
                        for half_points in range(24, 11, -1):
                            size = half_points / 2
                            line_height = (ascent + descent) * size / 100 * 1.05
                            lines = lines_for(text, metrics, size, box_width - 2)
                            if lines and len(lines) * line_height <= box_height - 2:
                                chosen = size, line_height, lines
                                break
                        if chosen is None:
                            fail("field_overflow")
                        size, line_height, lines = chosen
                        for line_index, line in enumerate(lines):
                            if not line:
                                continue
                            obj = raw.FPDFPageObj_CreateTextObj(document, font, size)
                            if not obj:
                                fail("processing_failed")
                            try:
                                encoded = line.encode("utf-16-le") + b"\0\0"
                                wide = (ctypes.c_ushort * (len(encoded) // 2)).from_buffer_copy(encoded)
                                if not raw.FPDFText_SetText(obj, wide) or not raw.FPDFPageObj_SetFillColor(obj, 0, 0, 0, 255):
                                    fail("processing_failed")
                                bounds = [ctypes.c_float() for _ in range(4)]
                                if not raw.FPDFPageObj_GetBounds(obj, *(ctypes.byref(value) for value in bounds)):
                                    fail("processing_failed")
                                if bounds[2].value - bounds[0].value > box_width - 2:
                                    fail("field_overflow")
                                u = field["x"] * width + 1
                                v = field["y"] * height + 1 + ascent * size / 100 + line_index * line_height
                                raw.FPDFPageObj_Transform(obj, *page_matrix(page, u, v))
                                if not raw.FPDFPage_InsertObject(page, obj):
                                    fail("processing_failed")
                            except BaseException:
                                raw.FPDFPageObj_Destroy(obj)
                                raise
                    page.gen_content()
            output = io.BytesIO()
            document.save(output)
    return output.getvalue()
