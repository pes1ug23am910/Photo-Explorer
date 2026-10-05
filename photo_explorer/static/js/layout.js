/** Pure layout calculations for virtual rows, including real date header rows. */
export function buildLayout(total, columns, rowHeight, groups = []) {
  const runs = groups.length ? groups : [{start: 0, count: total, date: null}];
  const segments = [];
  let y = 8;
  for (const group of runs) {
    const count = Math.min(group.count, Math.max(0, total - group.start));
    if (!count) continue;
    const header = group.date ? 34 : 0;
    const height = Math.ceil(count / columns) * rowHeight;
    segments.push({...group, count, y, bodyY: y + header, endY: y + header + height});
    y += header + height;
  }
  return {segments, height: y + 8, columns, rowHeight};
}

export function segmentForIndex(layout, index) {
  let low = 0;
  let high = layout.segments.length - 1;
  while (low <= high) {
    const middle = (low + high) >> 1;
    const segment = layout.segments[middle];
    if (index < segment.start) high = middle - 1;
    else if (index >= segment.start + segment.count) low = middle + 1;
    else return segment;
  }
  return null;
}

export function indexPosition(layout, index) {
  const segment = segmentForIndex(layout, index);
  if (!segment) return {x: 0, y: 0};
  const local = index - segment.start;
  return {x: local % layout.columns, y: segment.bodyY + Math.floor(local / layout.columns) * layout.rowHeight};
}

export function visibleWindow(layout, scrollTop, height) {
  const top = Math.max(0, scrollTop - 2 * layout.rowHeight);
  const bottom = scrollTop + height + 2 * layout.rowHeight;
  let low = 0;
  let high = layout.segments.length;
  while (low < high) {
    const middle = (low + high) >> 1;
    if (layout.segments[middle].endY < top) low = middle + 1;
    else high = middle;
  }
  const indices = [];
  const headers = [];
  for (let i = low; i < layout.segments.length; i++) {
    const segment = layout.segments[i];
    if (segment.y > bottom) break;
    if (segment.date) headers.push(segment);
    const firstRow = Math.max(0, Math.floor((top - segment.bodyY) / layout.rowHeight));
    const lastRow = Math.ceil((bottom - segment.bodyY) / layout.rowHeight);
    for (let local = firstRow * layout.columns; local < Math.min(segment.count, lastRow * layout.columns); local++) {
      indices.push(segment.start + local);
    }
  }
  return {indices, headers};
}
