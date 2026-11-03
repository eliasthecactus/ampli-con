import { goto } from '$app/navigation';

export class ApiError extends Error {
	status: number;
	constructor(status: number, message: string) {
		super(message);
		this.status = status;
	}
}

let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(fn: () => void) {
	onUnauthorized = fn;
}

export async function api<T = any>(method: string, path: string, body?: unknown): Promise<T> {
	const headers: Record<string, string> = { 'X-Requested-With': 'amplicon' };
	const init: RequestInit = { method, headers, credentials: 'same-origin' };
	if (body instanceof FormData) init.body = body;
	else if (body !== undefined) {
		headers['Content-Type'] = 'application/json';
		init.body = JSON.stringify(body);
	}
	let r: Response;
	try {
		r = await fetch('/api' + path, init);
	} catch {
		throw new ApiError(0, 'Cannot reach the server');
	}
	if (r.status === 401 && !path.startsWith('/auth/')) {
		onUnauthorized?.();
		goto('/login');
	}
	const ct = r.headers.get('content-type') || '';
	const data = ct.includes('json') ? await r.json() : await r.text();
	if (!r.ok) {
		const msg = typeof data === 'object' && data ? (data.detail ?? r.statusText) : data || r.statusText;
		throw new ApiError(r.status, typeof msg === 'string' ? msg : JSON.stringify(msg));
	}
	return data as T;
}

export const get = <T = any>(p: string) => api<T>('GET', p);
export const post = <T = any>(p: string, b: unknown = {}) => api<T>('POST', p, b);
export const put = <T = any>(p: string, b: unknown = {}) => api<T>('PUT', p, b);
export const patch = <T = any>(p: string, b: unknown = {}) => api<T>('PATCH', p, b);
export const del = <T = any>(p: string) => api<T>('DELETE', p);

export function qs(params: Record<string, unknown>): string {
	const u = new URLSearchParams();
	for (const [k, v] of Object.entries(params)) {
		if (v !== undefined && v !== null && v !== '') u.set(k, String(v));
	}
	const s = u.toString();
	return s ? '?' + s : '';
}
