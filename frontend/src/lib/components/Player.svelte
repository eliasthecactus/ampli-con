<script lang="ts">
	import { untrack } from 'svelte';
	import { ListMusic, LoaderCircle, Pause, Play, Repeat, Repeat1, Shuffle, SkipBack, SkipForward, Volume1, Volume2, VolumeX, X } from '@lucide/svelte';
	import { duration } from '../format';
	import { player } from '../player.svelte';

	const SEEK_STEP = 5;

	let audio = $state<HTMLAudioElement>();
	let scrub = $state<number | null>(null); // where the seek bar is held while dragging
	let clicked: Element | null = null; // the button the mouse left the focus on

	$effect(() => {
		if (audio) player.attach(audio);
	});

	$effect(() => {
		if (!audio) return;
		player.key;
		scrub = null;
		untrack(() => player.load());
	});

	// The system's media keys, lock screen and notification.
	$effect(() => {
		if (!('mediaSession' in navigator)) return;
		const s = navigator.mediaSession;
		const actions: [MediaSessionAction, MediaSessionActionHandler][] = [
			['play', () => player.toggle()],
			['pause', () => player.toggle()],
			['previoustrack', () => player.previous()],
			['nexttrack', () => player.next()],
			['seekbackward', (d) => player.seek(player.time - (d.seekOffset ?? SEEK_STEP))],
			['seekforward', (d) => player.seek(player.time + (d.seekOffset ?? SEEK_STEP))],
			['seekto', (d) => d.seekTime != null && player.seek(d.seekTime)]
		];
		for (const [action, handler] of actions) {
			try {
				s.setActionHandler(action, handler);
			} catch {
				/* not every browser knows every action */
			}
		}
		return () => actions.forEach(([action]) => {
			try {
				s.setActionHandler(action, null);
			} catch {
				/* see above */
			}
		});
	});
	$effect(() => {
		if (!('mediaSession' in navigator)) return;
		const song = player.song;
		navigator.mediaSession.metadata = song
			? new MediaMetadata({ title: song.title, artist: song.artist, album: song.album ?? '', artwork: song.thumbnail ? [{ src: song.thumbnail }] : [] })
			: null;
	});

	function key(e: KeyboardEvent) {
		if (e.key === 'Tab') clicked = null;
		if (!player.song || e.ctrlKey || e.metaKey || e.altKey) return;
		const el = e.target as HTMLElement;
		if (el.closest?.('textarea, select, [contenteditable], input:not([type="range"])')) return;
		const slider = el.matches?.('input[type="range"]');
		if (e.code === 'Space') {
			// A button reached with the keyboard keeps its space bar. One that was clicked doesn't, or the
			// space bar would start a song over instead of pausing it.
			const button = el.closest?.('button, a');
			if (button && button !== clicked) return;
			(button as HTMLElement | null)?.blur();
			player.toggle();
		} else if (e.key === 'ArrowLeft' && !slider) {
			if (e.shiftKey) player.previous();
			else player.seek(player.time - SEEK_STEP);
		} else if (e.key === 'ArrowRight' && !slider) {
			if (e.shiftKey) player.next();
			else player.seek(player.time + SEEK_STEP);
		} else if (e.key === 'm' || e.key === 'M') {
			player.toggleMute();
		} else if (e.key === 'Escape' && player.showQueue) {
			player.showQueue = false;
		} else {
			return;
		}
		e.preventDefault();
	}

	const percent = (part: number, whole: number) => (whole ? `${Math.min((part / whole) * 100, 100)}%` : '0%');
	const shown = $derived(scrub ?? player.time);
	const level = $derived(player.muted ? 0 : player.volume);
	const repeatLabel = $derived({ off: 'Repeat: off', all: 'Repeat: the whole queue', one: 'Repeat: this song' }[player.repeat]);
</script>

<svelte:window onkeydown={key} onpointerdown={(e) => (clicked = (e.target as Element).closest?.('button, a') ?? null)} />

<audio
	bind:this={audio}
	onplay={() => player.onPlayState(true)}
	onpause={() => player.onPlayState(false)}
	onplaying={() => player.onPlaying()}
	onloadstart={() => player.onLoading()}
	onwaiting={() => player.onLoading()}
	oncanplay={() => player.onReady()}
	ontimeupdate={() => player.onTime()}
	onprogress={() => player.onBuffered()}
	ondurationchange={() => player.onDuration()}
	onended={() => player.onEnded()}
	onerror={() => player.onError()}
></audio>

{#snippet modes()}
	<button class="ghost icon sm" class:on={player.shuffle} onclick={() => player.toggleShuffle()} title="Shuffle" aria-label="Shuffle" aria-pressed={player.shuffle}><Shuffle size={16} /></button>
	<button class="ghost icon sm" class:on={player.repeat !== 'off'} onclick={() => player.cycleRepeat()} title={repeatLabel} aria-label={repeatLabel}>
		{#if player.repeat === 'one'}<Repeat1 size={16} />{:else}<Repeat size={16} />{/if}
	</button>
{/snippet}

{#if player.song}
	{@const song = player.song}
	{#if player.showQueue}
		<section class="queue" aria-label="Queue">
			<header>
				<b>Up next</b>
				{#if player.upNext.length}<span class="small faint">{player.upNext.length}</span>{/if}
				<span class="spacer"></span>
				<span class="modes">{@render modes()}</span>
				{#if player.upNext.length}<button class="ghost sm" onclick={() => player.clearUpNext()}>Clear</button>{/if}
				<button class="ghost icon sm" onclick={() => (player.showQueue = false)} aria-label="Close queue"><X size={15} /></button>
			</header>
			{#if player.upNext.length}
				<ol>
					{#each player.upNext as e (e.key)}
						<li>
							<button class="ghost entry" onclick={() => player.jump(e.key)} title="Play {e.title}">
								{#if e.thumbnail}<img src={e.thumbnail} alt="" loading="lazy" />{:else}<span class="nocover"></span>{/if}
								<span class="grow col ellipsis">
									<span class="ellipsis">{e.title}</span>
									<span class="small muted ellipsis">{e.artist}</span>
								</span>
								{#if e.duration}<span class="small faint nums">{duration(e.duration)}</span>{/if}
							</button>
							<button class="ghost icon sm" onclick={() => player.remove(e.key)} aria-label="Remove {e.title} from the queue"><X size={14} /></button>
						</li>
					{/each}
				</ol>
			{:else}
				<p class="small muted">Nothing else in the queue.</p>
			{/if}
		</section>
	{/if}

	<div class="player">
		<div class="now">
			{#if song.thumbnail}<img src={song.thumbnail} alt="" />{:else}<span class="nocover"></span>{/if}
			<div class="col ellipsis">
				<b class="ellipsis">{#if song.album_id}<a href="/album/{song.album_id}">{song.title}</a>{:else}{song.title}{/if}</b>
				<span class="small muted ellipsis">{#if song.artist_id}<a href="/artist/{song.artist_id}">{song.artist}</a>{:else}{song.artist}{/if}</span>
			</div>
		</div>

		<div class="controls">
			<span class="modes">{@render modes()}</span>
			<button class="ghost icon back" onclick={() => player.previous()} title="Previous" aria-label="Previous"><SkipBack size={18} /></button>
			<button class="primary icon round" onclick={() => player.toggle()} aria-label={player.playing ? 'Pause' : 'Play'}>
				{#if player.loading}<LoaderCircle size={18} class="spin" />{:else if player.playing}<Pause size={18} />{:else}<Play size={18} />{/if}
			</button>
			<button class="ghost icon" onclick={() => player.next()} disabled={!player.hasNext} title="Next" aria-label="Next"><SkipForward size={18} /></button>
		</div>

		<div class="seek">
			<span class="small faint nums">{duration(shown)}</span>
			<input
				class="slider"
				type="range"
				min="0"
				max={player.duration || 1}
				step="0.1"
				value={shown}
				disabled={!player.duration}
				aria-label="Position"
				aria-valuetext={duration(shown)}
				style="--done: {percent(shown, player.duration)}; --loaded: {percent(player.buffered, player.duration)}"
				oninput={(e) => (scrub = +e.currentTarget.value)}
				onchange={(e) => {
					player.seek(+e.currentTarget.value);
					scrub = null;
				}}
			/>
			<span class="small faint nums">{player.duration ? duration(player.duration) : '–:––'}</span>
		</div>

		<div class="side">
			<div class="volume">
				<button class="ghost icon sm" onclick={() => player.toggleMute()} title={player.muted ? 'Unmute' : 'Mute'} aria-label={player.muted ? 'Unmute' : 'Mute'}>
					{#if level === 0}<VolumeX size={16} />{:else if level < 0.5}<Volume1 size={16} />{:else}<Volume2 size={16} />{/if}
				</button>
				<input
					class="slider"
					type="range"
					min="0"
					max="1"
					step="0.01"
					value={level}
					aria-label="Volume"
					style="--done: {level * 100}%; --loaded: 0%"
					oninput={(e) => player.setVolume(+e.currentTarget.value)}
				/>
			</div>
			<button class="ghost icon sm" class:on={player.showQueue} onclick={() => (player.showQueue = !player.showQueue)} title="Queue" aria-label="Queue" aria-expanded={player.showQueue}><ListMusic size={16} /></button>
			<button class="ghost icon sm" onclick={() => player.close()} title="Close player" aria-label="Close player"><X size={16} /></button>
		</div>
	</div>
{/if}

<style>
	.player {
		position: fixed;
		left: 232px;
		right: 12px;
		bottom: 12px;
		display: grid;
		grid-template-columns: minmax(0, 1fr) minmax(0, 1.6fr) minmax(0, 1fr);
		grid-template-areas: 'now controls side' 'now seek side';
		align-items: center;
		column-gap: 16px;
		padding: 8px 12px;
		background: var(--bg-2);
		border: 1px solid var(--line);
		border-radius: 14px;
		box-shadow: var(--shadow-lg);
		z-index: 45;
	}
	.now {
		grid-area: now;
		display: flex;
		align-items: center;
		gap: 10px;
		min-width: 0;
	}
	.now img,
	.now .nocover {
		width: 52px;
		height: 52px;
		border-radius: 7px;
		object-fit: cover;
		flex-shrink: 0;
	}
	.nocover {
		display: block;
		background: var(--bg-hover);
	}
	.now .col,
	.entry .col {
		gap: 1px;
	}
	.now a:hover {
		text-decoration: underline;
	}
	.controls {
		grid-area: controls;
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 6px;
	}
	.modes {
		display: inline-flex;
		gap: 2px;
	}
	.round {
		border-radius: 50%;
	}
	.on {
		color: var(--accent);
	}
	.seek {
		grid-area: seek;
		display: flex;
		align-items: center;
		gap: 8px;
	}
	.nums {
		font-variant-numeric: tabular-nums;
		white-space: nowrap;
	}
	.seek .nums {
		min-width: 34px;
	}
	.seek .nums:first-child {
		text-align: right;
	}
	.side {
		grid-area: side;
		display: flex;
		align-items: center;
		justify-content: flex-end;
		gap: 2px;
	}
	.volume {
		display: flex;
		align-items: center;
		gap: 4px;
		width: 130px;
		margin-right: 6px;
	}

	.slider {
		--rail: linear-gradient(to right, var(--accent) var(--done), var(--text-3) var(--done), var(--text-3) var(--loaded), var(--line-2) var(--loaded));
		appearance: none;
		flex: 1;
		min-width: 0;
		height: 16px;
		margin: 0;
		padding: 0;
		border: none;
		background: transparent;
		cursor: pointer;
	}
	.slider:focus {
		border: none;
		box-shadow: none;
	}
	.slider:disabled {
		cursor: default;
	}
	.slider::-webkit-slider-runnable-track {
		height: 4px;
		border-radius: 2px;
		background: var(--rail);
	}
	.slider::-moz-range-track {
		height: 4px;
		border-radius: 2px;
		background: var(--rail);
	}
	.slider::-webkit-slider-thumb {
		appearance: none;
		width: 12px;
		height: 12px;
		margin-top: -4px;
		border-radius: 50%;
		background: var(--accent);
		opacity: 0;
	}
	.slider::-moz-range-thumb {
		width: 12px;
		height: 12px;
		border: none;
		border-radius: 50%;
		background: var(--accent);
		opacity: 0;
	}
	.slider:hover:not(:disabled)::-webkit-slider-thumb,
	.slider:focus-visible::-webkit-slider-thumb {
		opacity: 1;
	}
	.slider:hover:not(:disabled)::-moz-range-thumb,
	.slider:focus-visible::-moz-range-thumb {
		opacity: 1;
	}

	.queue {
		position: fixed;
		right: 12px;
		bottom: 100px;
		width: min(400px, calc(100vw - 24px));
		max-height: min(60vh, 540px);
		display: flex;
		flex-direction: column;
		background: var(--bg-2);
		border: 1px solid var(--line);
		border-radius: 14px;
		box-shadow: var(--shadow-lg);
		z-index: 44;
	}
	.queue header {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 10px 10px 10px 14px;
		border-bottom: 1px solid var(--line);
	}
	.queue header .modes {
		display: none;
	}
	.spacer {
		flex: 1;
	}
	.queue ol {
		list-style: none;
		margin: 0;
		padding: 6px;
		overflow-y: auto;
	}
	.queue li {
		display: flex;
		align-items: center;
		gap: 2px;
	}
	.queue p {
		margin: 0;
		padding: 16px 14px;
	}
	.entry {
		flex: 1;
		min-width: 0;
		height: auto;
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 5px 6px;
		font-weight: 400;
		text-align: left;
	}
	.entry img,
	.entry .nocover {
		width: 38px;
		height: 38px;
		border-radius: 5px;
		object-fit: cover;
		flex-shrink: 0;
	}

	@media (max-width: 860px) {
		.player {
			left: 8px;
			right: 8px;
			bottom: 8px;
			grid-template-columns: minmax(0, 1fr) auto auto;
			grid-template-areas: 'seek seek seek' 'now controls side';
			column-gap: 6px;
			padding: 4px 8px 8px;
		}
		.now img,
		.now .nocover {
			width: 40px;
			height: 40px;
		}
		.controls .modes,
		.back,
		.volume {
			display: none;
		}
		.queue {
			right: 8px;
			bottom: 88px;
			width: calc(100vw - 16px);
		}
		.queue header .modes {
			display: inline-flex;
		}
	}
</style>
