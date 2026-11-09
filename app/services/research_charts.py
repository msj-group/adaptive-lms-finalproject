"""Pure, bounded SVG coordinates; zero observations stay zero."""
def activity_chart(daily):
    maximum = max((row['count'] for row in daily), default=0)
    denominator = maximum or 1
    points = [dict(x=36 + 584 * i / max(1, len(daily) - 1),
                   y=174 - 142 * row['count'] / denominator,
                   count=row['count'], label=row['label']) for i, row in enumerate(daily)]
    line = ' '.join(f"{p['x']:.2f},{p['y']:.2f}" for p in points)
    area = f"36,174 {line} {points[-1]['x']:.2f},174" if points else ''
    ticks = [points[i] for i in sorted({0, len(points) // 2, len(points) - 1})] if points else []
    return dict(points=points, line=line, area=area, maximum=maximum, ticks=ticks)
