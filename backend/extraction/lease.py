import re

from .facts import DATE, MONEY, date_value, fact, labeled, lines, money, observation, text
from .financial import DATE_FIELDS
from .models import DocumentFacts, RentTerm

SPACED_MONEY = r'(?:\$\s*-?\d[\d,\s]*\.\s*\d\s*\d|\$\s*(?:\d{1,3}(?:,\d{3})+|\d+)(?![\d.]))'
START = rf'(?:beginning\s+(?:on the scheduled day of\s+)?occupancy|commence on(?: the)?|lease start date|term start(?: date)?|effective from)\s*:?\s*(?P<value>{DATE})'
END = rf'(?:and ending|and end on|lease end date|term end(?: date)?)\s*:?\s*(?P<value>{DATE})'
RENT = rf'(?:rent per month|monthly rental amount|monthly rent|monthly installments of)\s*:?\s*(?P<value>{SPACED_MONEY})'
CLAUSE = r'\b(?:term|rent|payment|deposit|late|returned checks|renewal|termination|notice|holding over|prorat\w*|surrender|possession)\b'


def matches(doc, pattern, normalize):
    found = []
    for page in doc.pages:
        if page.state == 'unreadable':
            continue
        ordered = [word for row in lines(page.words) for word in row]
        content = text(ordered)
        for match in re.finditer(pattern, content, re.I):
            offset, selected = 0, []
            for word in ordered:
                if offset < match.end() and offset+len(word.text) > match.start():
                    selected.append(word)
                offset += len(word.text)+1
            raw = match.group('value')
            found.append((page.number, match.start(), observation(doc, page, selected, raw, normalize(raw))))
    return found


def lease(doc):
    unavailable = bool(doc.errors) or any(page.state == 'unreadable' for page in doc.pages)
    result = DocumentFacts(document=doc, fields={
        name: fact(labeled(doc, label, DATE, date_value), unavailable=unavailable)
        for name, label in DATE_FIELDS.items()
    }, limitations=[
        'Contractual rent and scheduled expiration do not establish incurred debt or actual possession end.',
        'Source-confirmed possession assertions still require evidence review; key return is not substituted.',
        'Clause text is retained as source terms, not interpreted as coverage rules or proof of deposit payment.',
        'Effective-rent selection uses inclusive stated dates; automatic renewal periods are not synthesized.',
        'Document grouping does not verify tenant or property identity.',
    ])
    starts, ends, rents = matches(doc, START, date_value), matches(doc, END, date_value), matches(doc, RENT, money)
    for index, start in enumerate(starts):
        position = start[:2]
        next_position = starts[index+1][:2] if index+1 < len(starts) else (float('inf'), 0)
        term_ends = [end[2] for end in ends if position <= end[:2] < next_position]
        rent = [rent[2] for rent in rents if len(starts) == 1 or position <= rent[:2] < next_position]
        term = RentTerm(start=fact([start[2]]), end=fact(term_ends, unavailable=unavailable),
                        monthly_rent=fact(rent, unavailable=unavailable))
        if term.start.state == term.end.state == 'present' and term.start.observations[0].value > term.end.observations[0].value:
            term.end.state = 'unreviewed'
            term.end.reason = 'Source term ends before it starts'
        result.rent_terms.append(term)
    if not starts:
        result.rent_terms.append(RentTerm(start=fact([], unavailable=unavailable),
                                          end=fact([end[2] for end in ends], unavailable=unavailable),
                                          monthly_rent=fact([rent[2] for rent in rents], unavailable=unavailable)))
    result.fields['deposit_requirement'] = fact(labeled(
        doc, r'(?:security deposit amount|security deposit required|deposit requirement)', MONEY, money), unavailable=unavailable)
    section_active = False
    for page in doc.pages:
        if page.state == 'unreadable':
            section_active = False
            continue
        section = []
        for row in lines(page.words):
            content = text(row)
            heading = re.match(r'^\d{1,2}\.\s+[A-Z]', content)
            if heading:
                if section:
                    raw = text(section)
                    result.clauses.append(observation(doc, page, section, raw, raw))
                section_active = bool(re.search(CLAUSE, content, re.I))
                section = row[:] if section_active else []
            elif section_active:
                section.extend(row)
            elif re.match(r'(?:renewal period|rent payment due date|security deposit amount|holding over fee|nsf fee|first and last month)', content, re.I):
                result.clauses.append(observation(doc, page, row, content, content))
        if section:
            raw = text(section)
            result.clauses.append(observation(doc, page, section, raw, raw))
    return result


def effective_rent(documents, effective_on):
    if effective_on is None:
        from .models import Fact
        return Fact(state='unreviewed', reason='Supply effective_on to select a contractual rent period')
    observations = []
    uncertain = any(result.document.errors or any(page.state == 'unreadable' for page in result.document.pages)
                    for result in documents if result.document.kind in ('lease', 'renewal'))
    for result in documents:
        for term in result.rent_terms:
            if term.start.state != 'present' or term.end.state != 'present':
                uncertain = True
                continue
            start, end = term.start.observations[0].value, term.end.observations[0].value
            if start and end and start <= effective_on <= end:
                for rent in term.monthly_rent.observations:
                    observations.append(rent.model_copy(update={
                        'citations': rent.citations + term.start.observations[0].citations + term.end.observations[0].citations,
                    }))
    selected = fact(observations)
    if uncertain and selected.state != 'conflicting':
        selected.state = 'unreviewed'
        selected.reason = 'An undated or unreadable term prevents reliable effective-rent selection'
    return selected
