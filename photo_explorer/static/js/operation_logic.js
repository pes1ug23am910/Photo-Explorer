/** Pure transformations shared by tools and browser-free contracts. */
export function newestUndoable(operations) {
  return [...operations].sort((a, b) => b.id - a.id).find(op =>
    op.status === 'done' && op.undoable && !op.undone_by && op.kind !== 'undo') || null;
}

export function structureOptions(path, files, skip = '') {
  return new URLSearchParams({path, files: files ? '1' : '0', skip}).toString();
}

export function serializeCondition(field, op, value, unit = 'B', upper = '') {
  const numeric = ['size', 'width', 'height', 'pixels', 'rating'].includes(field);
  const convert = input => {
    if (!numeric) return input;
    if (String(input).trim() === '' || !Number.isFinite(Number(input))) throw new Error('Enter a finite number');
    return Number(input) * (field === 'size' ? ({B: 1, KB: 1024, MB: 1048576}[unit] || 1) : 1);
  };
  const converted = op === 'in' ? String(value).split(/[,;\s]+/).filter(Boolean).map(v => v.replace(/^\./, '').toLowerCase())
    : ['favorite', 'meta_failed'].includes(field) ? value === true || value === 'true'
    : op === 'between' ? [convert(value), convert(upper)] : convert(value);
  return {field, op, value: converted};
}

export function serializeRule({name, root, dir, dirMode, others, ids, match, conditions, type, dest, collision, createDest}) {
  return {name, scope: {root_id: root, dir: dir || null, dir_mode: dirMode || 'subtree', include_other_files: !!others,
    ...(ids ? {ids: [...ids]} : {})}, match, conditions,
  action: {type, dest, collision, create_dest: !!createDest}};
}
