"""Opt-in dated charge rows with a source-backed section and column layout."""

import re

from .facts import DATE, MONEY, category, citation, date_value, fact, lines, money, observation, period, text
from .models import FinancialEntry


def dated_charges(document):
    if document.kind != 'move_out_statement' or document.errors or not document.sha256:
        return []
    recovered, active = [], None
    for page in document.pages:
        if page.method != 'native_text' or page.state != 'readable' or page.errors:
            active = None
            continue
        rows = lines(page.words)
        section = None
        continuing = active is not None and page.number == active['page']+1 and abs(page.width-active['width']) < 1
        if continuing and rows:
            first = rows[0]
            continuing = bool(re.fullmatch(DATE, first[0].text) and abs(first[0].bbox[0]-active['date_left'])<5)
        if not continuing:
            active = None
        for row in rows:
            content = text(row)
            if re.fullmatch(r'(?:current unpaid charges|charges|deductions)\s*:?', content, re.I):
                section = row
                active = None
                continue
            names = {w.text.casefold(): w for w in row}
            if 'date' in names and 'description' in names and ({'payment', 'payments', 'credit', 'credits', 'balance'} & names.keys()):
                active = None
                section = None
                continue
            if ('date' in names and 'description' in names and ('amount' in names or 'due' in names)
                    and not {'payment', 'payments', 'credit', 'credits', 'balance'} & names.keys()):
                if section and min(w.bbox[1] for w in row)-max(w.bbox[3] for w in section) < 60:
                    amount_label = names.get('amount', names.get('due'))
                    active = {'page': page.number, 'width': page.width, 'date_left': names['date'].bbox[0],
                              'description_left': names['description'].bbox[0], 'amount_left': amount_label.bbox[0]-10,
                              'header': citation(document, page, section+row)}
                continue
            if re.match(r'(?:total|balance|credits?|deposits?|refund|payment|tenant forwarding)\b', content, re.I):
                active = None
                section = None
                continue
            if not active:
                continue
            dates = [w for w in row if re.fullmatch(DATE, w.text) and abs(w.bbox[0]-active['date_left']) < 5]
            amounts = [w for w in row if re.fullmatch(MONEY, w.text)]
            if len(dates) != 1 or len(amounts) != 1:
                continue
            date_word, amount_word = dates[0], amounts[0]
            description_words = [w for w in row if active['description_left']-4 <= w.bbox[0] < active['amount_left']]
            description = text(description_words)
            if (amount_word.bbox[0] < active['amount_left'] or not description
                    or re.search(r'\b(?:payment|credit|refund|deposit|balance|total)\b', description, re.I)
                    or date_value(date_word.text) is None or money(amount_word.text) is None):
                continue
            recovered.append(FinancialEntry(
                entry_id=f'{document.document_id}:p{page.number}:dated-{len(recovered)+1}', kind='charge',
                category=category(description), description=description,
                posting_date=fact([observation(document, page, [date_word], date_word.text, date_value(date_word.text))]),
                service_period=period(document, page, description_words),
                amount=fact([observation(document, page, [amount_word], amount_word.text, money(amount_word.text))]),
                running_balance=fact([]), citations=[citation(document, page, row), active['header']],
                source_columns={'Amount Due': amount_word.text}))
        if active:
            active = dict(active, page=page.number)
    return recovered
