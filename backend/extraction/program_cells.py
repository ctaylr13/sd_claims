"""Read waiver fields from native table cells without model-supplied values or boxes."""

import re
from decimal import Decimal
from statistics import median

from .facts import money


def box(chars):
    return [min(c['x0'] for c in chars), min(c['top'] for c in chars),
            max(c['x1'] for c in chars), max(c['bottom'] for c in chars)]


def white(color):
    if isinstance(color, (int, float)):
        return color >= .98
    if color and len(color) in (1, 3):
        return all(v >= .98 for v in color)
    if color and len(color) == 4:
        return all(v <= .02 for v in color)
    return False


def read_chars(chars, region):
    raw = [c for c in chars if region[0] <= (c['x0']+c['x1'])/2 <= region[2]
           and region[1] <= (c['top']+c['bottom'])/2 <= region[3]]
    visible = [c for c in raw if c['text'] != '_' and not white(c.get('non_stroking_color'))]
    rows = []
    for char in sorted(visible, key=lambda c: (c['top'], c['x0'])):
        if rows and abs(char['top']-rows[-1][0]['top']) <= 2:
            rows[-1].append(char)
        else:
            rows.append([char])
    content, mapping = '', []
    for row in rows:
        if content:
            content += '\n'
            mapping.append(None)
        previous = None
        for char in sorted(row, key=lambda c: c['x0']):
            if previous and char['x0']-previous['x1'] > 1.5 and not content[-1:].isspace():
                content += ' '
                mapping.append(None)
            content += char['text']
            mapping.extend([char]*len(char['text']))
            previous = char
    audit = {'bbox': list(region), 'text': content, 'raw_chars': [
        {k:c.get(k) for k in ('text','x0','top','x1','bottom','fontname','non_stroking_color')} for c in raw],
        'discarded_underlines': sum(c['text']=='_' for c in raw),
        'discarded_white_chars': sum(white(c.get('non_stroking_color')) for c in raw)}
    return content, mapping, audit


def amount_in_cell(content, mapping):
    matches = list(re.finditer(r'\$\s*-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{2})?(?![\d,.])', content))
    if len(matches) != 1 or content.count('$') != 1:
        return None
    match, = matches
    if re.search(r'\d', content[:match.start()]+content[match.end():]):
        return None
    selected = [c for c in mapping[match.start():match.end()] if c and not c['text'].isspace()]
    numeric = [c for c in selected if c['text'] not in '$-+']
    widths = [c['x1']-c['x0'] for c in numeric if c['x1']>c['x0']]
    if not widths:
        return None
    tolerance = max(1.5, .45*median(widths))
    if any(abs(a['top']-b['top'])>2 or b['x0']-a['x1']>tolerance for a,b in zip(numeric,numeric[1:])):
        return None
    value = money(match.group())
    return {'value': value, 'quote': match.group(), 'bbox': box(selected)} if value is not None else None


def canonical_quote(value):
    value = re.sub(r'(?<=\d),(?=\d)', '', value.casefold())
    return re.sub(r'[+-]?\d+(?:\.\d+)?', lambda m: format(Decimal(m.group()), '.2f'), value)


def read_waiver_cells(source):
    cells = [c for table in source.find_tables() for c in table.cells]
    headers = []
    for region in cells:
        text = source.crop(region).extract_text() or ''
        if re.fullmatch(r'security deposit waiver\s*\(must check one\)', ' '.join(text.split()), re.I):
            headers.append(region)
    records, audits = [], []
    for header in headers:
        for monthly in cells:
            if abs(monthly[1]-header[3]) > 2 or not 30 <= monthly[2]-monthly[0] <= 130:
                continue
            fee_text, fee_map, fee_audit = read_chars(source.chars, monthly)
            if not re.search(r'\bper\s+month\b', fee_text, re.I):
                continue
            no_rows = [c for c in cells if abs(c[0]-monthly[0])<1 and abs(c[2]-monthly[2])<1 and abs(c[1]-monthly[3])<1]
            if len(no_rows) != 1 or not 18 <= monthly[0]-header[0] <= 120:
                continue
            no_row, = no_rows
            regions = {'yes': [header[0],monthly[1],monthly[0],monthly[3]],
                       'no': [header[0],no_row[1],monthly[0],no_row[3]],
                       'description': [monthly[2],monthly[1],header[2],monthly[3]]}
            parts = {name: read_chars(source.chars, region) for name,region in regions.items()}
            selected = []
            for option in ('yes','no'):
                compact = re.sub(r'\s+', '', parts[option][0])
                if re.fullmatch(r'[□☐☒☑]?[xX✓✔]'+option, compact, re.I) or re.fullmatch(r'[☒☑]'+option, compact, re.I):
                    selected.append(option)
            audit = {'header': list(header), 'monthly': fee_audit,
                     **{name: part[2] for name,part in parts.items()}, 'selected_options': selected}
            audits.append(audit)
            if len(selected) != 1:
                continue
            option, = selected
            label_text, label_map, label_audit = parts[option]
            label_chars = [c for c in label_map if c and c['text'].strip()]
            context = '\n'.join((label_text,fee_text,parts['description'][0]))
            context_box = [header[0],monthly[1],header[2],monthly[3]] if option=='yes' else regions['no']
            records.append({'key':'program_election','value':option,'quote':label_text.strip(),
                            'bbox':box(label_chars),'context':context,'context_bbox':context_box,'cell_proof':audit})
            if option != 'yes':
                continue
            fee = amount_in_cell(fee_text, fee_map)
            if fee:
                records.append({'key':'program_monthly_fee',**fee,'context':context,
                                'context_bbox':context_box,'cell_proof':audit})
            description, mapping, _ = parts['description']
            if re.search(r'waive payment of the security deposit(?: of)?\s*\$', description, re.I):
                deposit = amount_in_cell(description, mapping)
                if deposit:
                    records.append({'key':'waived_deposit',**deposit,'context':context,
                                    'context_bbox':context_box,'cell_proof':audit})
    return records, audits
