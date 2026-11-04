"""Small offline SVG charts derived from public request metadata."""
from html import escape


def _text(value):
    return escape(str(value), quote=True)


def _chart(title, subtitle, rows, maximum, value_label, color, suffix):
    """Horizontal bars with exact values, including explicitly visible zeros."""
    if not rows:
        return ''
    maximum = max(1, maximum)
    width, left, track, top, gap = 760, 125, 475, 34, 37
    height = top + len(rows) * gap + 12
    svg = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{_text(title)}">',
           f'<title>{_text(title)}</title>',
           f'<text x="{left}" y="19" class="axis">0</text>',
           f'<text x="{left + track}" y="19" class="axis" text-anchor="end">{_text(value_label)}</text>']
    for i, row in enumerate(rows):
        y = top + i * gap
        value = row['value']
        svg.append(f'<text x="8" y="{y + 16}" class="chart-label">{_text(row["label"])}</text>')
        svg.append(f'<rect x="{left}" y="{y}" width="{track}" height="20" rx="5" fill="#e8eeeb"/>')
        if value is not None:
            length = min(track, max(0, value / maximum * track))
            if length:
                svg.append(f'<rect x="{left}" y="{y}" width="{length:.2f}" height="20" rx="5" fill="{color}"/>')
            label = f'{value:,}{suffix}'
        else:
            label = 'No data'
        if row.get('note'):
            label += ' · ' + row['note']
        svg.append(f'<text x="{left + track + 10}" y="{y + 16}" class="chart-value">{_text(label)}</text>')
    svg.append('</svg>')
    return f'<section class="chart"><h2>{_text(title)}</h2><p>{_text(subtitle)}</p><p class="scroll-hint">Swipe horizontally to see chart values.</p>{"".join(svg)}</section>'


def charts(rows, context_limit):
    """Render selected aggregates only; never read or export evaluator diagnostics."""
    if not rows:
        return ''
    progress = [{'label': r['label'], 'value': r['minute'], 'note': r.get('stop_note', '')} for r in rows]
    observed = [r['minute'] for r in rows if isinstance(r['minute'], int)]
    horizon = max(60, ((max(observed, default=0) + 9) // 10) * 10)
    failures = [{'label': r['label'], 'value': r['errors'], 'note': f'of {r["executed"]} executed' if r['errors'] is not None else ''} for r in rows]
    error_max = max(50, max((r['errors'] or 0 for r in rows), default=0))
    contexts = [{'label': r['label'], 'value': r['input_peak'],
                 'note': f'{r["trimmed"]} trims' if r['trimmed'] is not None else ''} for r in rows]
    limit = context_limit if isinstance(context_limit, int) and context_limit > 0 else 32768
    return '<div class="charts">' + ''.join([
        _chart('How far did each run get?', 'Simulated minutes at the stopping point. A full episode is 1,440 minutes; shorter bars are not full-day scores.', progress, horizon, f'{horizon} min', '#479477', ' min'),
        _chart('How many calls returned errors?', 'Executed calls only. Includes rejected draft checks, invalid committed orders and malformed responses; these categories have different effects on simulated time.', failures, error_max, f'{error_max} errors', '#cd7559', ''),
        _chart('How large did the prompts get?', 'Highest recorded input tokens per case against the configured input threshold. Trims are history-reduction operations, not failed cases.', contexts, limit, f'{limit:,} tokens', '#6e84ba', ''),
    ]) + '</div>'
