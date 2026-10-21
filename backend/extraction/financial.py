import re

from .facts import DATE, MONEY, category, citation, date_value, fact, labeled, lines, money, observation, period, text
from .models import DocumentFacts, FinancialEntry

REQUEST_LABEL = r'(?:requested insurer reimbursement|insurer reimbursement requested|reimbursement requested|requested reimbursement|amount of claim|claim amount requested)'
DATE_FIELDS = {
    'reported_move_out': r'(?:reported move[- ]?out(?: date)?|move[- ]?out date)',
    'key_return': r'(?:keys? returned(?: date)?|key return(?: date)?|keys:\s*issued:\s*\d+\s+returned:\s*\d+\s+date)',
    'confirmed_possession_end': r'confirmed possession end(?: date)?',
    'reported_lease_expiration': r'(?:lease expires|lease expira(?:tion|tion date))',
    'rent_paid_through': r'rent paid through',
    'notice_date': r'date notice given',
}
MONEY_FIELDS = {
    'requested_insurer_reimbursement': REQUEST_LABEL,
    'tenant_balance_due': r'(?:amount due|balance due of|balance due to:\s*resident\s*\[\s*\]\s*owner\s*\[\s*x\s*\])',
    'resident_refund': r'(?:refund due|balance due to:\s*resident\s*\[\s*x\s*\]\s*owner\s*\[\s*\])',
    'total_charges': r'total (?:charges|deductions)',
    'total_credits': r'total credits',
    'total_deposits': r'total deposits',
    'deposit_held': r'(?:management held security deposit|deposit held)',
    'prepayments': r'prepayments',
    'opening_balance': r'(?:opening|starting) balance',
    'closing_balance': r'closing balance',
    'deposit_balance': r'deposit balance',
    'deposit_paid': r'deposit paid',
}


def shared_fields(doc):
    unavailable = bool(doc.errors) or any(page.state == 'unreadable' for page in doc.pages)
    fields = {name: fact(labeled(doc, label, DATE, date_value), unavailable=unavailable)
              for name, label in DATE_FIELDS.items()}
    fields.update({name: fact(labeled(doc, label, MONEY, money), unavailable=unavailable)
                   for name, label in MONEY_FIELDS.items()})
    return fields


def ledger(doc):
    result = DocumentFacts(document=doc, fields=shared_fields(doc), limitations=[
        'Pattern-based extraction; unparsed regions require review. No computed loss or insurer request from ledger balances.',
        'Posting dates do not establish service periods. Financial assertions across documents are not additive.',
    ])
    for page in doc.pages:
        if page.state == 'unreadable':
            continue
        rows = lines(page.words)
        headers = [row for row in rows if all(name in text(row).lower() for name in ('charges', 'payments', 'balance'))]
        if not headers:
            result.unparsed.extend(citation(doc, page, row) for row in rows if re.search(MONEY, text(row)))
            continue
        header = headers[0]
        names = {word.text.lower(): word for word in header}
        if 'date' not in names or 'description' not in names:
            result.unparsed.append(citation(doc, page, header))
            continue
        centers = [(names[name].bbox[0] + names[name].bbox[2])/2 for name in ('charges', 'payments', 'balance')]
        boundaries = [names['charges'].bbox[0] - 15, (centers[0]+centers[1])/2,
                      (centers[1]+centers[2])/2, page.width]
        date_center = (names['date'].bbox[0] + names['date'].bbox[2])/2
        header_bottom = max(word.bbox[3] for word in header) + 8
        date_words = [word for word in page.words if word.bbox[1] > header_bottom
                      and abs((word.bbox[0]+word.bbox[2])/2 - date_center) < 30
                      and re.fullmatch(DATE, word.text)]
        date_words.sort(key=lambda word: word.bbox[1])
        for index, date_word in enumerate(date_words):
            y = (date_word.bbox[1] + date_word.bbox[3])/2
            top = header_bottom if index == 0 else (date_words[index-1].bbox[3]+date_word.bbox[1])/2
            bottom = page.height if index == len(date_words)-1 else (date_word.bbox[3]+date_words[index+1].bbox[1])/2
            date_cell = containing_cell(page, date_center, y)
            if date_cell:
                top, bottom = date_cell[1], date_cell[3]
            row = [word for word in page.words if top <= (word.bbox[1]+word.bbox[3])/2 < bottom]
            row.sort(key=lambda word: (round(word.bbox[1]/3), word.bbox[0]))
            amounts, columns = {}, {}
            for column_index, name in enumerate(('Charges', 'Payments', 'Balance')):
                cell = containing_cell(page, centers[column_index], y)
                if cell and (cell[0] < boundaries[column_index]-20 or cell[2] > boundaries[column_index+1]+20):
                    cell = None
                cells = region_words(page, cell) if cell else [
                    word for word in page.words if abs((word.bbox[1]+word.bbox[3])/2-y) < 5
                    and boundaries[column_index] <= (word.bbox[0]+word.bbox[2])/2 < boundaries[column_index+1]]
                raw = text(sorted(cells, key=lambda word: word.bbox[0]))
                if re.fullmatch(MONEY, raw):
                    amounts[name] = fact([observation(doc, page, cells, raw, money(raw))])
                    columns[name] = raw
            if not amounts:
                result.unparsed.append(citation(doc, page, row))
                continue
            desc_center = (names['description'].bbox[0]+names['description'].bbox[2])/2
            desc_cell = containing_cell(page, desc_center, y)
            if desc_cell and (desc_cell[0] < date_center+20 or desc_cell[2] > boundaries[0]+2):
                desc_cell = None
            description_words = region_words(page, desc_cell) if desc_cell else [
                word for word in row if names['description'].bbox[0]-25 <= word.bbox[0] < boundaries[0]]
            description = ' '.join(text(part) for part in lines(description_words))
            kind = 'charge' if 'Charges' in amounts else 'payment' if 'Payments' in amounts else 'unclassified'
            returned = re.search(r'\b(?:payment return|returned payment|echeck return|reversal|reversed)\b', description, re.I)
            return_fee = re.search(r'nsf\s*fee|returned (?:check|payment) fee', description, re.I)
            if returned and not return_fee:
                kind = 'reversal'
            elif re.search(r'\bdeposit\s+(?:received|refund|transfer)\b', description, re.I):
                kind = 'deposit'
            elif re.search(r'\bcredit\b|\bdeposit\s+applied\b', description, re.I):
                kind = 'credit'
            if 'Charges' in amounts and 'Payments' in amounts:
                kind = 'unclassified'
            amount = amounts.get('Charges', amounts.get('Payments', fact([])))
            if kind == 'unclassified' and 'Charges' in amounts and 'Payments' in amounts:
                amount = fact(amounts['Charges'].observations + amounts['Payments'].observations)
                amount.state = 'unreviewed'
                amount.reason = 'Both charge and payment columns populated; retain both without netting'
            result.entries.append(FinancialEntry(
                entry_id=f'{doc.document_id}:p{page.number}:r{index+1}', kind=kind,
                category=category(description), description=description,
                posting_date=fact([observation(doc, page, [date_word], date_word.text, date_value(date_word.text))]),
                service_period=period(doc, page, description_words),
                amount=amount,
                running_balance=amounts.get('Balance', fact([])),
                citations=[citation(doc, page, row), citation(doc, page, header)], source_columns=columns))
        summary_fields(doc, page, rows, result.fields, header_bottom)
    mark_unparsed(result)
    return result


def summary_fields(doc, page, rows, fields, header_bottom):
    summary_labels = {'balance_as_of': r'balance as of\s*(' + DATE + ')',
                      'prepayments': r'prepayments', 'total_unpaid': r'total unpaid', 'deposit_held': r'deposit held'}
    for name, pattern in summary_labels.items():
        for row in rows:
            if min(word.bbox[1] for word in row) >= header_bottom:
                continue
            content = text(row)
            match = re.search(pattern, content, re.I)
            if not match:
                continue
            if name == 'balance_as_of':
                raw_date = match.group(1)
                previous = fields.get('balance_as_of', fact([])).observations
                fields['balance_as_of'] = fact(previous + [observation(doc, page, row, raw_date, date_value(raw_date))])
            offset = 0
            label_words = []
            for word in row:
                if offset < match.end() and offset+len(word.text) > match.start():
                    label_words.append(word)
                offset += len(word.text)+1
            x0 = min(word.bbox[0] for word in label_words)
            x1 = max(word.bbox[2] for word in label_words)
            candidates = [word for word in page.words if re.fullmatch(MONEY, word.text)
                          and max(word.bbox[1] for word in row) < word.bbox[1] < max(word.bbox[3] for word in row)+35
                          and x0-15 <= (word.bbox[0]+word.bbox[2])/2 <= x1+15]
            if len(candidates) == 1:
                word = candidates[0]
                target = 'balance' if name == 'balance_as_of' else name
                existing = fields.get(target, fact([])).observations
                fields[target] = fact(existing + [observation(doc, page, label_words+[word], word.text, money(word.text))])


def containing_cell(page, x, y):
    cells = [cell for cell in page.table_cells if cell[0] <= x < cell[2] and cell[1] <= y < cell[3]]
    return min(cells, key=lambda cell: (cell[2]-cell[0])*(cell[3]-cell[1])) if cells else None


def region_words(page, cell):
    return [word for row in lines([word for word in page.words
            if cell[0] <= (word.bbox[0]+word.bbox[2])/2 < cell[2]
            and cell[1] <= (word.bbox[1]+word.bbox[3])/2 < cell[3]]) for word in row]


def statement_entries(doc, page, section, rows, result):
    anchors = [(row, list(re.finditer(MONEY, text(row)))) for row in rows]
    result.unparsed.extend(citation(doc, page, row) for row, values in anchors if len(values) > 1)
    rows = [row for row, values in anchors if len(values) <= 1]
    anchors = [(row, values[0]) for row, values in anchors if len(values) == 1]
    if not anchors:
        result.unparsed.extend(citation(doc, page, row) for row in rows)
        return
    for index, (anchor, match) in enumerate(anchors):
        previous = anchors[index-1][0] if index else None
        following = anchors[index+1][0] if index+1 < len(anchors) else None
        top = (max(word.bbox[3] for word in previous)+min(word.bbox[1] for word in anchor))/2 if previous else 0
        bottom = (max(word.bbox[3] for word in anchor)+min(word.bbox[1] for word in following))/2 if following else page.height
        selected = [word for row in rows for word in row if top <= (word.bbox[1]+word.bbox[3])/2 < bottom]
        amount_words = [word for word in anchor if '$' in word.text or re.fullmatch(MONEY, word.text)]
        amount_x = min(word.bbox[0] for word in amount_words) if amount_words else page.width
        description_words = [word for word in selected if word.bbox[2] <= amount_x+2]
        description = ' '.join(text(row) for row in lines(description_words))
        kind = 'charge' if section in ('charges', 'deductions') else 'credit' if section == 'credits' else 'deposit'
        if 'payment' in description.lower():
            kind = 'payment'
        result.entries.append(FinancialEntry(
            entry_id=f'{doc.document_id}:p{page.number}:r{len(result.entries)+1}', kind=kind,
            category=category(description), description=description, posting_date=fact([]),
            service_period=period(doc, page, description_words),
            amount=fact([observation(doc, page, amount_words, match.group(), money(match.group()))]),
            running_balance=fact([]), citations=[citation(doc, page, selected)]))


def move_out(doc):
    result = DocumentFacts(document=doc, fields=shared_fields(doc), limitations=[
        'Statement totals and ledger totals may assert the same tenant debt; do not add them together.',
        'Deposit requirement, deposit held, and applied deposit credit have separate meanings.',
    ])
    for page in doc.pages:
        if page.state == 'unreadable':
            continue
        section, pending = None, []
        for row in lines(page.words):
            content = text(row)
            heading = re.fullmatch(r'(?:deductions|charges|credits|deposits)\s*:?', content, re.I)
            boundary = re.match(r'(?:total |amount due|balance due|balance due to|please |regards)', content, re.I)
            if heading or boundary:
                if pending:
                    statement_entries(doc, page, section, pending, result)
                section = content.lower().rstrip(':') if heading else None
                pending = []
            elif section:
                pending.append(row)
        if pending:
            statement_entries(doc, page, section, pending, result)
    mark_unparsed(result)
    return result


def mark_unparsed(result):
    citations = [cite for value in result.fields.values() for obs in value.observations for cite in obs.citations]
    citations.extend(cite for entry in result.entries for cite in entry.citations)
    for page in result.document.pages:
        if page.state == 'unreadable':
            continue
        covered = [cite for cite in citations + result.unparsed if cite.page == page.number]
        for row in lines(page.words):
            amounts = [word for word in row if re.fullmatch(MONEY, word.text)]
            if any(not any(cite.bbox[0] <= (word.bbox[0]+word.bbox[2])/2 <= cite.bbox[2]
                           and cite.bbox[1] <= (word.bbox[1]+word.bbox[3])/2 <= cite.bbox[3]
                           for cite in covered) for word in amounts):
                result.unparsed.append(citation(result.document, page, row))
