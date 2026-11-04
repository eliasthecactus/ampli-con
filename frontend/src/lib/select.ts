/** Ticks or unticks `id`. With shift, everything between it and the last clicked row follows. */
export function pick<T>(selected: Set<T>, order: T[], id: T, anchor: T | null, shift: boolean): Set<T> {
	const s = new Set(selected);
	const on = !s.has(id);
	const from = anchor === null ? -1 : order.indexOf(anchor);
	const to = order.indexOf(id);
	const rows = shift && from >= 0 && to >= 0 ? order.slice(Math.min(from, to), Math.max(from, to) + 1) : [id];
	for (const row of rows) {
		if (on) s.add(row);
		else s.delete(row);
	}
	return s;
}
