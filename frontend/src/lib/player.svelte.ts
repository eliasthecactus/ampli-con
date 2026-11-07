import { toast } from './state.svelte';

export type Track = {
	id: string;
	title: string;
	artist: string;
	thumbnail: string | null;
	src?: string; // a file of the library; songs from the catalog go through the stream proxy
	duration?: number; // until the audio knows better
	artist_id?: string | null;
	album?: string | null;
	album_id?: string | null;
};
type Entry = Track & { key: number }; // the same song can be in the queue twice
export type Repeat = 'off' | 'all' | 'one';

const STORE = 'amplicon-player';
const RESTART_AFTER = 3; // seconds in, "previous" goes back to the start of the song
const WARM_BEFORE_END = 30; // seconds left, the server starts looking up the next song
const MAX_FAILURES = 3; // songs in a row that won't play before the player stops trying

let keys = 0;
const entry = (t: Track): Entry => ({ ...t, key: ++keys });

function mixed<T>(list: T[]): T[] {
	const out = [...list];
	for (let i = out.length - 1; i > 0; i--) {
		const j = Math.floor(Math.random() * (i + 1));
		[out[i], out[j]] = [out[j], out[i]];
	}
	return out;
}

const catalogTrack = (s: any): Track => ({
	id: s.id,
	title: s.title,
	artist: s.artist ?? '',
	thumbnail: s.thumbnail ?? null,
	duration: s.duration || undefined,
	artist_id: s.artist_id,
	album: s.album,
	album_id: s.album_id
});

/** A file of the library or the import, as the player wants it. */
export const fileTrack = (f: { id: number; title?: string | null; artist?: string | null; path: string; has_cover?: boolean }): Track => ({
	id: String(f.id),
	title: f.title || f.path,
	artist: f.artist ?? '',
	thumbnail: f.has_cover ? `/api/files/${f.id}/cover?size=96` : null,
	src: `/api/files/${f.id}/audio`
});

/** The web player: a queue played through Amplicon's stream proxy, and the one audio element behind it. */
class Player {
	queue = $state.raw<Entry[]>([]); // in playing order
	index = $state(-1);
	song = $derived(this.queue[this.index] ?? null);
	key = $derived(this.song?.key ?? 0);
	upNext = $derived(this.queue.slice(this.index + 1));

	playing = $state(false);
	loading = $state(false);
	time = $state(0);
	duration = $state(0);
	buffered = $state(0);

	volume = $state(1);
	muted = $state(false);
	shuffle = $state(false);
	repeat = $state<Repeat>('off');
	showQueue = $state(false);

	hasNext = $derived(this.index < this.queue.length - 1 || this.repeat === 'all');

	private audio: HTMLAudioElement | undefined;
	private order: Entry[] = []; // the queue before shuffling, to go back to
	private failures = 0;
	private finished = false; // ran off the end of the queue
	private warmed = 0;

	constructor() {
		try {
			const s = JSON.parse(localStorage.getItem(STORE) ?? '{}');
			if (typeof s.volume === 'number') this.volume = Math.min(Math.max(s.volume, 0), 1);
			this.muted = !!s.muted;
			this.shuffle = !!s.shuffle;
			if (s.repeat === 'all' || s.repeat === 'one') this.repeat = s.repeat;
		} catch {
			/* storage blocked */
		}
	}

	private remember() {
		try {
			localStorage.setItem(STORE, JSON.stringify({ volume: this.volume, muted: this.muted, shuffle: this.shuffle, repeat: this.repeat }));
		} catch {
			/* ignore */
		}
	}

	attach(audio: HTMLAudioElement) {
		this.audio = audio;
		audio.volume = this.volume;
		audio.muted = this.muted;
	}

	/** Plays a list, starting at one of its songs. With shuffle on, the rest comes in random order. */
	play(tracks: Track[], start = 0) {
		const list = tracks.map(entry);
		if (!list.length) return;
		this.order = list;
		const first = list[start];
		this.queue = this.shuffle ? [first, ...mixed(list.filter((e) => e !== first))] : list;
		this.index = this.shuffle ? 0 : start;
		this.finished = false;
		this.failures = 0;
	}

	/** Songs from the catalog: what isn't available stays out of the queue. */
	playSongs(songs: any[], from?: any) {
		const list = songs.filter((s) => s.available !== false);
		this.play(list.map(catalogTrack), from ? Math.max(list.indexOf(from), 0) : 0);
	}

	shuffleSongs(songs: any[]) {
		const list = songs.filter((s) => s.available !== false);
		if (!this.shuffle) this.toggleShuffle();
		this.play(list.map(catalogTrack), Math.floor(Math.random() * list.length));
	}

	addSong(song: any) {
		const e = entry(catalogTrack(song));
		this.queue = [...this.queue, e];
		this.order = [...this.order, e];
		if (this.index < 0) this.index = 0;
		else if (this.finished) this.advance();
		toast(`Added ${song.title} to the queue`, 'ok');
	}

	remove(key: number) {
		const at = this.queue.findIndex((e) => e.key === key);
		if (at <= this.index) return; // only what's still to come
		this.queue = this.queue.filter((e) => e.key !== key);
		this.order = this.order.filter((e) => e.key !== key);
	}

	clearUpNext() {
		this.queue = this.queue.slice(0, this.index + 1);
		const left = new Set(this.queue.map((e) => e.key));
		this.order = this.order.filter((e) => left.has(e.key));
	}

	jump(key: number) {
		const at = this.queue.findIndex((e) => e.key === key);
		if (at >= 0) this.go(at);
	}

	close() {
		this.audio?.pause();
		this.queue = [];
		this.order = [];
		this.index = -1;
		this.showQueue = false;
	}

	toggle() {
		if (!this.audio) return;
		if (this.audio.paused) this.audio.play().catch(() => {});
		else this.audio.pause();
	}

	seek(seconds: number) {
		if (!this.audio) return;
		this.time = Math.min(Math.max(seconds, 0), this.duration || seconds);
		this.audio.currentTime = this.time;
		this.publishPosition();
	}

	next() {
		if (this.index < this.queue.length - 1) this.go(this.index + 1);
		else if (this.repeat === 'all') this.go(0);
	}

	previous() {
		if (this.time > RESTART_AFTER) this.seek(0);
		else if (this.index > 0) this.go(this.index - 1);
		else if (this.repeat === 'all') this.go(this.queue.length - 1);
		else this.seek(0);
	}

	toggleShuffle() {
		this.shuffle = !this.shuffle;
		this.remember();
		const now = this.song;
		if (!now) return;
		if (this.shuffle) {
			this.queue = [now, ...mixed(this.queue.filter((e) => e.key !== now.key))];
			this.index = 0;
		} else {
			this.queue = this.order;
			this.index = this.order.findIndex((e) => e.key === now.key);
		}
	}

	cycleRepeat() {
		this.repeat = this.repeat === 'off' ? 'all' : this.repeat === 'all' ? 'one' : 'off';
		this.remember();
	}

	setVolume(volume: number) {
		this.volume = volume;
		this.muted = volume === 0;
		if (this.audio) {
			this.audio.volume = volume;
			this.audio.muted = this.muted;
		}
		this.remember();
	}

	toggleMute() {
		this.muted = !this.muted;
		if (this.audio) this.audio.muted = this.muted;
		if (!this.muted && this.volume === 0) this.setVolume(0.5);
		this.remember();
	}

	private go(index: number) {
		this.finished = false;
		if (index === this.index) this.restart();
		else this.index = index;
	}

	private advance() {
		this.finished = false;
		this.index += 1;
	}

	private restart() {
		if (!this.audio) return;
		this.audio.currentTime = 0;
		this.audio.play().catch(() => {});
	}

	/** The song changed. */
	load() {
		const audio = this.audio;
		if (!audio) return;
		const song = this.song;
		this.time = 0;
		this.buffered = 0;
		this.duration = song?.duration ?? 0;
		if (!song) {
			audio.removeAttribute('src');
			audio.load();
			this.loading = false;
			return;
		}
		audio.src = song.src ?? `/api/stream/${song.id}`;
		audio.play().catch(() => {});
	}

	// What the audio element reports.

	onPlaying() {
		this.failures = 0;
		this.loading = false;
	}

	onLoading() {
		this.loading = true;
	}

	onReady() {
		this.loading = false;
	}

	onPlayState(playing: boolean) {
		this.playing = playing;
		if ('mediaSession' in navigator) navigator.mediaSession.playbackState = playing ? 'playing' : 'paused';
	}

	onTime() {
		const audio = this.audio;
		if (!audio) return;
		this.time = audio.currentTime;
		this.onBuffered();
		const next = this.queue[this.index + 1];
		if (next && !next.src && next.key !== this.warmed && this.duration && this.duration - this.time < WARM_BEFORE_END) {
			this.warmed = next.key;
			fetch(`/api/stream/${next.id}`, { method: 'HEAD' }).catch(() => {});
		}
	}

	onBuffered() {
		const audio = this.audio;
		if (!audio) return;
		for (let i = 0; i < audio.buffered.length; i++) {
			if (audio.buffered.start(i) <= audio.currentTime && audio.currentTime <= audio.buffered.end(i)) {
				this.buffered = audio.buffered.end(i);
				return;
			}
		}
	}

	onDuration() {
		const d = this.audio?.duration;
		if (d && Number.isFinite(d)) {
			this.duration = d;
			this.publishPosition();
		}
	}

	onEnded() {
		if (this.repeat === 'one') this.restart();
		else if (this.index < this.queue.length - 1) this.advance();
		else if (this.repeat === 'all') this.go(0);
		else this.finished = true;
	}

	onError() {
		if (!this.audio?.error || !this.song) return;
		this.loading = false;
		toast(`Couldn't play ${this.song.title}`, 'error');
		this.failures += 1;
		if (this.failures < MAX_FAILURES && this.index < this.queue.length - 1) this.advance();
	}

	private publishPosition() {
		if (!('mediaSession' in navigator) || !this.duration) return;
		try {
			navigator.mediaSession.setPositionState({ duration: this.duration, position: Math.min(this.time, this.duration), playbackRate: 1 });
		} catch {
			/* not every browser takes it */
		}
	}
}

export const player = new Player();
