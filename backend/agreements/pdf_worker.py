"""Bounded, noninteractive PDF inspection/rendering; no Django initialization."""

import io
import json
import math
import os
import re
import sys
from contextlib import closing
from pathlib import Path


class InvalidDocument(Exception):
    pass


def limit_memory(memory_bytes):
    # Limits also cover decoded images and native PDFium allocations.
    if os.name != "nt":
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        return None

    import ctypes
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [
            ("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
            ("flags", wintypes.DWORD), ("working_min", ctypes.c_size_t),
            ("working_max", ctypes.c_size_t), ("active_processes", wintypes.DWORD),
            ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
            ("scheduling", wintypes.DWORD),
        ]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("basic", BasicLimits), ("io_counters", ctypes.c_ulonglong * 6),
            ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
            ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    job = kernel.CreateJobObjectW(None, None)
    info = ExtendedLimits()
    info.basic.flags = 0x100  # JOB_OBJECT_LIMIT_PROCESS_MEMORY
    info.process_memory = memory_bytes
    if not job or not kernel.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
        raise InvalidDocument("resource_limit")
    if not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
        raise InvalidDocument("resource_limit")
    return job


def check_file(path, max_bytes):
    size = path.stat().st_size
    if size == 0:
        raise InvalidDocument("empty")
    if size > max_bytes:
        raise InvalidDocument("oversized")
    with path.open("rb") as source:
        if not re.match(rb"%PDF-(?:1\.[0-7]|2\.0)[\r\n]", source.read(16)):
            raise InvalidDocument("unsupported")
        source.seek(max(0, size - 2048))
        ending = source.read()
    trailer = re.search(rb"startxref\s+(\d+)\s+%%EOF[\x00\t\n\f\r ]*\Z", ending)
    if not trailer or not 0 < int(trailer[1]) < size:
        raise InvalidDocument("corrupt")


def page_geometry(page):
    width, height = page.get_size()
    rotation = page.get_rotation()
    if (
        not all(math.isfinite(value) and value > 0 for value in (width, height))
        or rotation not in (0, 90, 180, 270)
    ):
        raise InvalidDocument("invalid_page")
    # Record the unrotated page size; rendering honors the intrinsic rotation.
    if rotation in (90, 270):
        width, height = height, width
    return {"width": width, "height": height, "rotation": rotation}


def rasterize(page, edge, *, encode):
    width, height = page.get_size()
    scale = edge / max(width, height)
    pixel_width, pixel_height = math.ceil(width * scale), math.ceil(height * scale)
    if min(pixel_width, pixel_height) < 1 or max(pixel_width, pixel_height) > edge + 1:
        raise InvalidDocument("invalid_page")
    with closing(page.render(
        scale=scale, draw_annots=True, may_draw_forms=True,
        rev_byteorder=True, prefer_bgrx=False, limit_image_cache=True,
    )) as bitmap:
        with bitmap.to_pil() as image:
            image.load()
            if encode:
                output = io.BytesIO()
                image.save(output, format="PNG")
                return output.getvalue()
    return None


def process(operation, path, max_bytes, max_pages, edge, page_number):
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    check_file(path, max_bytes)
    try:
        document = pdfium.PdfDocument(path)
    except pdfium.PdfiumError as error:
        if error.err_code == raw.FPDF_ERR_PASSWORD:
            raise InvalidDocument("encrypted") from error
        raise InvalidDocument("corrupt") from error
    with document:
        if raw.FPDF_GetSecurityHandlerRevision(document) != -1:
            raise InvalidDocument("encrypted")
        if not raw.FPDF_DocumentHasValidCrossReferenceTable(document):
            raise InvalidDocument("corrupt")
        form_type = raw.FPDF_GetFormType(document)
        if form_type not in (raw.FORMTYPE_NONE, raw.FORMTYPE_ACRO_FORM):
            raise InvalidDocument("unsupported_forms")
        if form_type == raw.FORMTYPE_ACRO_FORM:
            # No document/page actions or JavaScript callbacks are installed.
            config = raw.FPDF_FORMFILLINFO(version=2, xfa_disabled=True)
            document.init_forms(config=config)
        count = len(document)
        if not count:
            raise InvalidDocument("no_pages")
        if count > max_pages:
            raise InvalidDocument("too_many_pages")
        try:
            if operation == "render":
                if not 1 <= page_number <= count:
                    raise InvalidDocument("page_not_found")
                with closing(document[page_number - 1]) as page:
                    page_geometry(page)
                    return rasterize(page, edge, encode=True)
            pages = []
            for index in range(count):
                with closing(document[index]) as page:
                    pages.append(page_geometry(page))
                    rasterize(page, edge, encode=False)
            return json.dumps(pages, allow_nan=False).encode("utf-8")
        except pdfium.PdfiumError as error:
            raise InvalidDocument("corrupt") from error


def main():
    job = None
    try:
        operation, filename, byte_limit, page_limit, edge, page_number, memory_limit, font_path = sys.argv[1:]
        job = limit_memory(int(memory_limit))
        if operation not in ("validate", "render", "complete"):
            raise InvalidDocument("processing_failed")
        if operation == "complete":
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from completion_worker import complete
            process("validate", Path(filename), int(byte_limit), int(page_limit), int(edge), 0)
            result = complete(Path(filename), json.load(sys.stdin), font_path)
            if len(result) > int(byte_limit):
                raise InvalidDocument("oversized")
        else:
            result = process(
                operation, Path(filename), int(byte_limit), int(page_limit),
                int(edge), int(page_number),
            )
        sys.stdout.buffer.write(result)
    except InvalidDocument as error:
        sys.stdout.write(json.dumps({"error": str(error)}))
        return 2
    except MemoryError:
        sys.stdout.write(json.dumps({"error": "resource_limit"}))
        return 2
    except Exception:
        sys.stdout.write(json.dumps({"error": "processing_failed"}))
        return 2
    finally:
        if job is not None:
            import ctypes
            from ctypes import wintypes

            close = ctypes.WinDLL("kernel32").CloseHandle
            close.argtypes = (wintypes.HANDLE,)
            close(job)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
