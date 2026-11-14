"""Company-entered business values, separate from every signer's inputs."""

import re
from datetime import date

from django.core.exceptions import ValidationError


class PreparationValues:
    @staticmethod
    def fields(version):
        return [field for field in version.fields if field["role"] == "preparation"] if version else []

    @classmethod
    def validate(cls, version, supplied, *, complete):
        fields = cls.fields(version)
        if not isinstance(supplied, dict) or set(supplied) - {field["id"] for field in fields}:
            raise ValidationError({"preparation_values": "Use only preparation fields from the selected version."})
        errors, values = [], {}
        for field in fields:
            label = field["label"] or field["type"]
            value = supplied.get(field["id"], "")
            if field["type"] not in ("text", "name", "date") or not isinstance(value, str):
                errors.append(f"{label}: enter a supported text, name or date value.")
                continue
            value = value.strip()
            values[field["id"]] = value
            if any(ord(character) < 32 and character not in "\n\r\t" for character in value):
                errors.append(f"{label}: remove unsupported control characters.")
            elif complete and field["required"] and not value:
                errors.append(f"{label}: complete this required company preparation value.")
            elif value and field["type"] == "date":
                try:
                    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
                        raise ValueError
                    date.fromisoformat(value)
                except ValueError:
                    errors.append(f"{label}: enter a valid date in YYYY-MM-DD format.")
            elif value and field["type"] == "name" and not any(c.isalpha() for c in value):
                errors.append(f"{label}: enter a name containing letters.")
        if errors:
            raise ValidationError({"preparation_values": errors})
        return values

    @classmethod
    def displayed(cls, issued):
        values = cls.validate(issued.template_version, issued.preparation_values, complete=True)
        return [{**field, "value": values[field["id"]]} for field in cls.fields(issued.template_version)]
