"""Read bounded text anchors in the PDF's displayed coordinate system."""

import math
import re


FIELD_LABEL = re.compile(
    r"^(?:(?:client|customer|company|authorized|printed|full|signer|your|effective)\s+){0,3}"
    r"(?:name|signature|date(?: signed)?|signed(?: by)?|initials?|e-?mail|address|phone|fee|amount|reference|title|by|acknowledgement)"
    r"(?:\s*[:\-]\s*.*)?$", re.I,
)

PLACEHOLDER = re.compile(r"\[[^\[\]\n]{2,90}\]|\{\{[^{}\n]{2,90}\}\}|\b(?:Person|Place|Date)\b")


def field_hint(label, context, *, inline=False):
    """Classify explicit form labels locally; ambiguous blanks stay for AI review."""
    text = label.strip(' []{}:.-')
    role_text = f"{text} {context}"
    company = bool(re.search(r'\bcompany(?: signing block| signer| countersigner| signature)\b', role_text, re.I))
    client = bool(re.search(r'\b(?:client|customer)(?: signing block| signer| signature)\b', role_text, re.I))
    role = 'company' if company else 'client' if client else None
    if inline:
        following = context.split('AFTER:', 1)[-1]
        owner_match = re.search(r'[\(\[\"“](?:the\s+)?(Company|Client|Customer)[\)\]\"”]', following)
        owner = owner_match.group(1).replace('Customer', 'Client') if owner_match else None
        if re.search(r'effective|as of|commencement', role_text, re.I) and re.search(r'\bdate\b', text, re.I):
            return {'type': 'date', 'role': 'preparation', 'label': 'Effective date', 'required': True}
        named_owner = re.search(r'\b(company|client|customer)\b', text, re.I)
        if named_owner:
            owner = named_owner.group(1).title().replace('Customer', 'Client')
        if re.search(r'\b(?:Person|name)\b', text, re.I) and owner:
            return {'type': 'name', 'role': 'preparation', 'label': f'{owner} legal name', 'required': True}
        if re.search(r'\b(?:Place|address|location)\b', text, re.I) and owner:
            return {'type': 'text', 'role': 'preparation', 'label': f'{owner} address', 'required': True}
        if re.search(r'\bdate\b', text, re.I):
            return {'type': 'date', 'role': role or 'preparation', 'label': text, 'required': True}
        return None
    if re.search(r'\bsignature\b|^signed(?: by)?$', text, re.I):
        return {'type': 'signature', 'role': role or 'client', 'label': text or 'Signature', 'required': True}
    if FIELD_LABEL.fullmatch(text):
        if re.search(r'\bname\b', text, re.I):
            kind = 'name'
        elif re.search(r'\bdate\b', text, re.I):
            kind = 'date'
        elif re.search(r'\binitials?\b', text, re.I):
            kind = 'initials'
        elif re.search(r'\b(?:title|e-?mail|address|phone|fee|amount|reference)\b', text, re.I):
            kind = 'text'
        else:
            return None
        if not role:
            role = 'preparation' if re.search(r'fee|amount|reference|effective', text, re.I) else 'client'
        return {'type': kind, 'role': role, 'label': text, 'required': True}
    return None


def row_segments(row):
    """Keep side-by-side signing blocks distinct even when PDFium joins a row."""
    segments, segment, previous = [], [], None
    for character, box in row:
        if not character.isspace():
            height = max(1, box[3] - box[1])
            if previous is not None and box[0] - previous[2] > max(18, height * 1.8):
                if segment:
                    segments.append(segment)
                segment = []
            previous = box
        segment.append((character, box))
    if segment:
        segments.append(segment)
    return segments


def displayed_box(page, boxes):
    left, bottom, right, top = page.get_bbox()
    width, height = page.get_size()
    rotation = page.get_rotation()
    points = []
    for x0, y0, x1, y1 in boxes:
        for x, y in ((x0, y0), (x0, y1), (x1, y0), (x1, y1)):
            points.append({0: (x-left, top-y), 90: (y-bottom, x-left),
                           180: (right-x, y-bottom), 270: (top-y, right-x)}[rotation])
    x0, y0 = max(0, min(x for x, _ in points)), max(0, min(y for _, y in points))
    x1, y1 = min(width, max(x for x, _ in points)), min(height, max(y for _, y in points))
    if x1 <= x0 or y1 <= y0:
        return None
    return {"x": x0/width, "y": y0/height, "width": (x1-x0)/width, "height": (y1-y0)/height}


def page_anchors(page, textpage, page_number, *, include_ranges=False):
    import pypdfium2.raw as raw

    rows, row = [], []
    for index in range(min(textpage.count_chars(), 30000)):
        value = raw.FPDFText_GetUnicode(textpage, index)
        character = chr(value) if 0 < value <= 0x10ffff else ""
        if character in ("\r", "\n"):
            if row:
                rows.append(row)
                row = []
            continue
        if not character or ord(character) < 32 or 0xd800 <= value <= 0xdfff:
            continue
        box = textpage.get_charbox(index)
        if not all(math.isfinite(v) for v in box):
            continue
        # Physical rows also cover PDFs without explicit newline characters.
        if row and not character.isspace() and row[-1][0] != " " and abs((box[1]+box[3])-(row[-1][1][1]+row[-1][1][3])) > 12:
            rows.append(row)
            row = []
        row.append((" " if character.isspace() else character, box))
    if row:
        rows.append(row)
    anchors = []
    previous_rows, headings = [], []
    row_texts = [''.join(char for char, _ in row).strip() for row in rows]
    all_text = ' '.join(row_texts)
    ranges, offset = [], 0
    for text, row in zip(row_texts, rows):
        ranges.append({'start': offset, 'end': offset+len(text), 'boxes': [box for char,box in row if not char.isspace()]})
        offset += len(text)+1
    text_offset = 0
    for row_index, row in enumerate(rows):
        current_rows = []
        for segment in row_segments(row):
            text = "".join(char for char, _ in segment).strip()
            if not text:
                continue
            # Strip characters and their boxes together so offsets stay aligned.
            while segment and segment[0][0].isspace():
                segment = segment[1:]
            while segment and segment[-1][0].isspace():
                segment = segment[:-1]
            left = min(box[0] for char, box in segment if not char.isspace())
            previous = min(previous_rows, key=lambda item: abs(item[0]-left))[1] if previous_rows else ""
            current_rows.append((left, text))
            if re.fullmatch(r"(?:the\s+)?(?:company|client|customer)(?:\s+signer)?", text, re.I):
                headings = [(x, label) for x, label in headings if abs(x-left) > 24]
                headings.append((left, text))
            context = previous
            if headings:
                heading = min(headings, key=lambda item: abs(item[0]-left))[1]
                context = f"{heading} signing block. {previous}"
            blanks = list(re.finditer(r"_{3,}|\.{5,}", text))
            pieces, start = [], 0
            for match in blanks:
                label = text[start:match.start()].strip() or re.split(r"_{3,}|\.{5,}", previous)[-1].strip() or "Unlabelled blank"
                pieces.append((label, [box for _, box in segment[match.start():match.end()]], True, False, context, None, None))
                start = match.end()
            if not blanks and len(text) <= 120 and FIELD_LABEL.fullmatch(text):
                pieces.append((text, [box for _, box in segment], False, False, context, None, None))
            # Inline placeholders are actual printed tokens, not mentions of dates/names in prose.
            position = all_text.find(text, text_offset)
            if position >= 0:
                text_offset = position
            for match in PLACEHOLDER.finditer(text):
                if position < 0 or FIELD_LABEL.fullmatch(text):
                    continue
                if match.group() == 'Date' and not re.search(r'\bas\s+of\s*$', all_text[max(0, position+match.start()-25):position+match.start()], re.I):
                    continue
                if not match.group().startswith(('[', '{')) and not (
                    re.search(r'\b(?:entered into|as of|by and between|principal place|corporation|Effective Date)\b', all_text[max(0, position-240):position+len(text)+240], re.I)
                ):
                    continue
                after = all_text[position+match.end():position+match.end()+300] if position >= 0 else ''
                nearby = f'BEFORE: {text[max(0, match.start()-80):match.start()]} AFTER: {after}'
                hint = field_hint(match.group(), nearby, inline=True)
                if hint:
                    pieces.append((hint['label'], [box for _, box in segment[match.start():match.end()]], True, True, nearby, hint, (position+match.start(),position+match.end())))
            for label, boxes, blank, inline, evidence_context, inline_hint, span in pieces:
                box = displayed_box(page, boxes)
                if box is None:
                    continue
                height = max(.018, min(.03, box['height']*1.25)) if inline else max(.035, min(.06, box["height"]*2))
                y = box['y']-.002 if inline else box["y"]-height+.005 if blank else box["y"]+box["height"]+.005
                width = max(.02, box['width']) if inline else max(.12, min(.55, box["width"])) if blank else .3
                field = {"x": min(box["x"], .99), "y": max(0, min(y, 1-height)),
                         "width": min(width, 1-box["x"]), "height": height}
                if field["width"] < .02:
                    continue
                anchors.append({"id": f"p{page_number}-a{len(anchors)+1}", "page": page_number,
                                "text": label[:180], "context": evidence_context[:300], "box": field,
                                "blank": blank, 'hint': field_hint(label, evidence_context) if not inline else inline_hint})
                if include_ranges and inline:
                    anchors[-1].update(_span=span, _row=row_index, _ranges=ranges, _text=all_text)
                if len(anchors) >= 60:
                    return anchors
        if current_rows:
            previous_rows = current_rows
    return anchors


def inline_group(page, textpage, page_number, fields):
    """Reflow accepted inline values within their original text rows, preserving surrounding text."""
    anchors = [a for a in page_anchors(page, textpage, page_number, include_ranges=True) if a.get('_span')]
    matched = []
    for field in fields:
        if field['page'] != page_number or not field.get('value'):
            continue
        for anchor in anchors:
            if field['type'] == anchor['hint']['type'] and all(abs(field[key]-anchor['box'][key]) < .001 for key in ('x','y','width','height')):
                matched.append((field, anchor)); break
    if not matched:
        return [], set()
    first, last = min(a['_row'] for _,a in matched), max(a['_row'] for _,a in matched)
    ranges = matched[0][1]['_ranges']
    start, end = ranges[first]['start'], ranges[last]['end']
    text = matched[0][1]['_text'][start:end]
    for field, anchor in sorted(matched, key=lambda item:item[1]['_span'][0], reverse=True):
        left,right = anchor['_span']
        text = text[:left-start] + field['value'].replace('\n',' ').replace('\r',' ') + text[right-start:]
    box = displayed_box(page, [b for row in ranges[first:last+1] for b in row['boxes']])
    if not box:
        return [], set()
    box['y'] = max(0,box['y']-.003)
    box['height'] = min(1-box['y'],box['height']+.006)
    return [{'page':page_number, 'type':'text', 'value':text, '_inline_group':True, **box}], {id(field) for field,_ in matched}
