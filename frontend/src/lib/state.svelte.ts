import { get } from './api';

export type Job = {
	id: number;
	kind: 'song' | 'album' | 'playlist';
	source_id: string;
	title: string;
	subtitle: string | null;
	thumbnail: string | null;
	profile_name: string | null;
	location_name: string | null;
	status: string;
	total: number;
	done: number;
	skipped: number;
	failed: number;
	error: string | null;
	requested_by: string | null;
	created_at: number;
	finished_at: number | null;
	progress: number;
	current: { id: number; title: string; artist: string | null; progress: number }[];
	items?: any[];
	review?: boolean;
};

export const session = $state({
	ready: false,
	user: null as null | { id: number; username: string },
	version: '',
	envUser: false
});

export const live = $state({
	connected: false,
	jobs: {} as Record<number, Job>
});

export const ACTIVE = new Set(['preparing', 'review', 'queued', 'running']);

export const toasts = $state({ list: [] as { id: number; text: string; kind: string }[] });
let toastId = 0;
export function toast(text: string, kind: 'info' | 'ok' | 'error' = 'info') {
	const id = ++toastId;
	toasts.list.push({ id, text, kind });
	setTimeout(() => (toasts.list = toasts.list.filter((t) => t.id !== id)), kind === 'error' ? 7000 : 3500);
}
export function errorToast(e: unknown) {
	toast(e instanceof Error ? e.message : String(e), 'error');
}

type Handler = (data: any) => void;
const handlers: Record<string, Set<Handler>> = {};

/** Subscribe to a server event; returns an unsubscribe function (use in $effect). */
export function on(event: string, fn: Handler): () => void {
	(handlers[event] ??= new Set()).add(fn);
	return () => handlers[event]?.delete(fn);
}
function emit(event: string, data: any) {
	handlers[event]?.forEach((fn) => fn(data));
}

let es: EventSource | null = null;

export async function loadActive() {
	try {
		const jobs = await get<Job[]>('/jobs?active=true');
		const map: Record<number, Job> = {};
		for (const j of jobs) map[j.id] = j;
		live.jobs = map;
	} catch {
		/* ignore */
	}
}

export function connectEvents() {
	if (es) return;
	es = new EventSource('/api/events');
	es.onopen = () => {
		live.connected = true;
		loadActive();
	};
	es.onerror = () => {
		live.connected = false;
	};
	es.addEventListener('job', (e) => {
		const d: Job = JSON.parse((e as MessageEvent).data);
		if (ACTIVE.has(d.status)) live.jobs[d.id] = d;
		else delete live.jobs[d.id];
		if (d.status === 'review' && reviews.waiting.includes(d.id)) {
			reviews.waiting = reviews.waiting.filter((x) => x !== d.id);
			reviews.job ??= d.id;
		}
		emit('job', d);
	});
	es.addEventListener('progress', (e) => {
		const d = JSON.parse((e as MessageEvent).data);
		const j = live.jobs[d.job_id];
		if (j) {
			const i = j.current.findIndex((x) => x.id === d.id);
			if (i >= 0) j.current[i] = { ...j.current[i], progress: d.progress };
			else j.current.push({ id: d.id, title: d.title, artist: d.artist, progress: d.progress });
			const finished = j.done + j.skipped + j.failed;
			j.progress = j.total ? (finished + j.current.reduce((a, c) => a + c.progress, 0)) / j.total : 0;
		}
		emit('progress', d);
	});
	for (const ev of ['item', 'account', 'job_removed', 'jobs_cleared', 'library', 'fix']) {
		es.addEventListener(ev, (e) => emit(ev, JSON.parse((e as MessageEvent).data)));
	}
}

export function disconnectEvents() {
	es?.close();
	es = null;
	live.connected = false;
}

/** The review before a download: the job shown right now, and the ones this browser started and waits for. */
export const reviews = $state({ job: null as number | null, waiting: [] as number[] });
export function reviewWhenReady(id: number) {
	if (!reviews.waiting.includes(id)) reviews.waiting.push(id);
}
