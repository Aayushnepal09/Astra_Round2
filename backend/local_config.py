"""Read the optional local Groq key without evaluating shell or dotenv expressions."""

import os

from django.core.exceptions import ImproperlyConfigured


def groq_api_key(path, environ=None):
    environ = os.environ if environ is None else environ
    if "GROQ_API_KEY" in environ:
        return environ["GROQ_API_KEY"].strip()
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        return ""
    except (OSError, UnicodeError) as error:
        raise ImproperlyConfigured("The project .env could not be read. Save it as UTF-8 and restart Django.") from error
    for line in lines:
        name, separator, value = line.strip().partition("=")
        if not separator or name.strip() != "GROQ_API_KEY":
            continue
        value = value.strip()
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise ImproperlyConfigured("Close the quotation marks around GROQ_API_KEY in the project .env.")
            value = value[1:-1]
        return value.strip()
    return ""
