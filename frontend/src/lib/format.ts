export function bytes(n: number | null | undefined): string {
	if (!n) return '0 B';
	const u = ['B', 'KB', 'MB', 'GB', 'TB'];
	let i = 0;
	let v = n;
	while (v >= 1024 && i < u.length - 1) {
		v /= 1024;
		i++;
	}
	return `${v < 10 && i > 0 ? v.toFixed(1) : Math.round(v)} ${u[i]}`;
}

export function duration(s: number | null | undefined): string {
	if (!s && s !== 0) return '';
	s = Math.round(s);
	const h = Math.floor(s / 3600);
	const m = Math.floor((s % 3600) / 60);
	const sec = s % 60;
	return h ? `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}` : `${m}:${String(sec).padStart(2, '0')}`;
}

export function ago(ts: number | null | undefined): string {
	if (!ts) return 'never';
	const d = Date.now() / 1000 - ts;
	if (d < 0) return until(ts);
	if (d < 45) return 'just now';
	if (d < 3600) return `${Math.round(d / 60)} min ago`;
	if (d < 86400) return `${Math.round(d / 3600)} h ago`;
	if (d < 86400 * 30) return `${Math.round(d / 86400)} d ago`;
	return new Date(ts * 1000).toLocaleDateString();
}

export function until(ts: number | null | undefined): string {
	if (!ts) return '';
	const d = ts - Date.now() / 1000;
	if (d <= 30) return 'now';
	if (d < 3600) return `in ${Math.round(d / 60)} min`;
	if (d < 86400) return `in ${Math.round(d / 3600)} h`;
	return `in ${Math.round(d / 86400)} d`;
}

export function datetime(ts: number | null | undefined): string {
	if (!ts) return '';
	return new Date(ts * 1000).toLocaleString();
}

export function speed(bps: number | null | undefined): string {
	return bps ? bytes(bps) + '/s' : '';
}

export const STATUS_LABEL: Record<string, string> = {
	pending: 'Pending',
	queued: 'Queued',
	downloading: 'Downloading',
	done: 'Downloaded',
	failed: 'Failed',
	unavailable: 'Unavailable',
	missing: 'File missing',
	ignored: 'Ignored',
	deleted: 'Deleted',
	removed: 'Removed',
	running: 'Running',
	cancelled: 'Cancelled',
	ok: 'OK',
	partial: 'Partly failed',
	review: 'Needs review',
	valid: 'Valid',
	expired: 'Expired',
	unknown: 'Not checked'
};

export const SCHEDULES: { label: string; value: string }[] = [
	{ label: 'Manual only', value: '' },
	{ label: 'Every hour', value: '0 * * * *' },
	{ label: 'Every 3 hours', value: '0 */3 * * *' },
	{ label: 'Every 6 hours', value: '0 */6 * * *' },
	{ label: 'Every 12 hours', value: '0 */12 * * *' },
	{ label: 'Daily at 03:00', value: '0 3 * * *' },
	{ label: 'Weekly (Mon 03:00)', value: '0 3 * * 1' }
];

export function scheduleLabel(v: string): string {
	return SCHEDULES.find((s) => s.value === v)?.label ?? (v ? `Cron: ${v}` : 'Manual only');
}

export const FIELD_LABELS: Record<string, string> = {
	title: 'Title',
	artist: 'Artist',
	album: 'Album',
	album_artist: 'Album artist',
	track: 'Track',
	track_total: 'Track total',
	disc: 'Disc',
	year: 'Year',
	genre: 'Genre',
	comment: 'Comment',
	lyrics: 'Lyrics'
};
export const FIELDS = Object.keys(FIELD_LABELS);
