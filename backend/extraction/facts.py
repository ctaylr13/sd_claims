import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from .models import Citation, Fact, Observation

MONTH = r'(?:January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)'
DATE = rf'(?:\d(?:\s*\d)?\s*[/-]\s*\d(?:\s*\d)?\s*[/-]\s*\d(?:\s*\d){{1,3}}|{MONTH}\s+\d{{1,2}},?\s+\d{{4}}|\d{{4}}-\d{{2}}-\d{{2}})'
NUMBER = r'(?:\d{1,3}(?:,\d{3})+|\d+)'
MONEY = rf'(?<![\w/])(?:\(\s*)?(?:(?:-\$\s*|\$\s*-?\s*){NUMBER}(?:\.\d{{2}})?|-?{NUMBER}\.\d{{2}})(?:\s*\))?(?![\d/.])'


def money(raw):
    compact = re.sub(r'\s', '', raw)
    signed = rf'(?:-\$?|\$-?)?{NUMBER}(?:\.\d{{2}})?'
    if not re.fullmatch(rf'(?:{signed}|\({signed}\))', compact):
        return None
    value = re.sub(r'[\s$,()]', '', raw)
    try:
        amount = Decimal(value)
        if '(' in raw:
            amount = -abs(amount)
        return format(amount, '.2f')
    except InvalidOperation:
        return None


def date_value(raw):
    value = re.sub(r'(?<=\d)\s+(?=[\d/-])|(?<=[/-])\s+', '', raw.strip())
    for pattern in ('%m/%d/%Y', '%m/%d/%y', '%m-%d-%Y', '%m-%d-%y', '%B %d, %Y', '%B %d %Y', '%b %d, %Y', '%b %d %Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(value, pattern).date().isoformat()
        except ValueError:
            pass
    return None


def lines(words):
    rows = []
    for word in sorted(words, key=lambda word: (word.bbox[1], word.bbox[0])):
        if rows and abs(word.bbox[1] - rows[-1][0].bbox[1]) <= 3:
            rows[-1].append(word)
        else:
            rows.append([word])
    return [sorted(row, key=lambda word: word.bbox[0]) for row in rows]


def text(words):
    return ' '.join(word.text for word in words)


def citation(doc, page, words):
    return Citation(document_id=doc.document_id, sha256=doc.sha256, page=page.number,
                    quote=text(words), bbox=(min(word.bbox[0] for word in words),
                                            min(word.bbox[1] for word in words),
                                            max(word.bbox[2] for word in words),
                                            max(word.bbox[3] for word in words)), method=page.method)


def observation(doc, page, words, raw, value):
    limitations = []
    if value is None:
        limitations.append('Value retained without a safe normalization')
    if page.method == 'ocr':
        limitations.append('OCR output requires visual review')
    if any(word.confidence is not None and word.confidence < 80 for word in words):
        limitations.append('Low OCR word confidence in cited region')
    return Observation(raw_value=raw, value=value, citations=[citation(doc, page, words)],
                       limitations=limitations)


def fact(observations, *, unavailable=False):
    if not observations:
        return Fact(state='unreadable' if unavailable else 'missing',
                    reason='No supported match in reviewed readable text; not proof of absence'
                    if not unavailable else 'Source could not be fully read; value not established')
    nonblank = [item for item in observations if item.raw_value.strip()]
    if not nonblank:
        return Fact(state='missing', observations=observations, reason='Recorded source value is blank')
    values = {item.value for item in nonblank if item.value is not None}
    state = 'conflicting' if len(values) > 1 else 'present'
    if any(item.value is None for item in nonblank) and state != 'conflicting':
        state = 'unreviewed'
    return Fact(state=state, observations=observations,
                reason='Competing source observations retained' if state == 'conflicting' else
                'An observation could not be normalized safely' if state == 'unreviewed' else None)


def period(doc, page, words):
    content = text(words)
    dates = list(re.finditer(DATE, content, re.I))
    if len(dates) >= 2:
        # Explicit ranges only -- two unrelated dates are not a service period.
        first, second = dates[:2]
        if re.fullmatch(r'\s*(?:-|–|through|to)\s*', content[first.end():second.start()], re.I):
            start, end = date_value(first.group()), date_value(second.group())
            value = f'{start}/{end}' if start and end else None
            return fact([observation(doc, page, words, content[first.start():second.end()], value)])
    match = re.search(rf'\b{MONTH}(?:,?\s+{MONTH})*\s*,?\s*(?:\d{{4}})?\b', content, re.I)
    short_range = re.search(r'\b\d{1,2}/\d{1,2}\s*[-–]\s*\d{1,2}/\d{1,2}\b', content)
    numeric_month = re.search(r'(?<![\d/-])(0?[1-9]|1[0-2])[-/](\d{4})(?![\d/-])', content)
    if short_range:
        return fact([observation(doc, page, words, short_range.group(), None)])
    if numeric_month:
        value = f'{numeric_month.group(2)}-{int(numeric_month.group(1)):02}'
        return fact([observation(doc, page, words, numeric_month.group(), value)])
    if match:
        raw = match.group().strip()
        value = None
        for pattern in ('%B %Y', '%b %Y'):
            try:
                value = datetime.strptime(raw, pattern).strftime('%Y-%m')
                break
            except ValueError:
                pass
        return fact([observation(doc, page, words, raw, value)])
    return fact([])


def category(description):
    content = description.lower()
    for name, pattern in (
        ('utilities', r'utility|utilities|water|sewer|electric|power|gas bill'),
        ('deposit', r'deposit'),
        ('program_fee', r'sdrp|sdwp|replacement program|waiver program'),
        ('benefits_fee', r'\brbp\b|benefit package'),
        ('returned_payment_fee', r'nsf\s*fee|returned (?:check|payment) fee'),
        ('late_fee', r'late fee|late charge'),
        ('legal_fee', r'court|legal|attorney|eviction|writ'),
        ('cleaning', r'clean|carpet'),
        ('damage', r'damage|repair|replace|paint|haul'),
        ('rent', r'\brent\b|rental\s*income'),
        ('administrative_fee', r'administrati|coordination|bill fee'),
    ):
        if re.search(pattern, content):
            return name
    return 'other'


def labeled(doc, label, value_pattern, normalize):
    observations = []
    for page in doc.pages:
        if page.state == 'unreadable':
            continue
        for row in lines(page.words):
            for match in re.finditer(rf'(?:{label})\s*:?\s*(?P<value>{value_pattern})', text(row), re.I):
                raw = match.group('value').strip()
                observations.append(observation(doc, page, row, raw, normalize(raw)))
    return observations
