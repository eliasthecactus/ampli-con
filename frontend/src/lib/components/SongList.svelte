<script lang="ts">
	import { ListPlus, Play } from '@lucide/svelte';
	import { duration } from '../format';
	import { player } from '../player.svelte';
	import DownloadButton from './DownloadButton.svelte';

	// rows: a block of that many rows that scrolls sideways, instead of one long list.
	let { songs, numbered = false, rows = 0 }: { songs: any[]; numbered?: boolean; rows?: number } = $props();
</script>

<div class="list" class:block={rows} style={rows ? `--rows: ${Math.min(rows, songs.length)}` : undefined}>
	{#each songs as s, i (s.id + ':' + i)}
		<div class="song" class:gone={!s.available}>
			<button class="ghost icon play" onclick={() => player.playSongs(songs, s)} disabled={!s.available} aria-label="Play {s.title}">
				{#if numbered}<span class="n">{s.track ?? i + 1}</span>{:else if s.thumbnail}<img src={s.thumbnail} alt="" loading="lazy" />{/if}
				<span class="overlay"><Play size={15} /></span>
			</button>
			<div class="grow col ellipsis">
				<span class="ellipsis">{s.title}{#if s.is_video}<span class="pill">Video</span>{/if}</span>
				<span class="small muted ellipsis">
					{#if s.artist_id}<a href="/artist/{s.artist_id}">{s.artist}</a>{:else}{s.artist}{/if}{#if s.album && !numbered}{' · '}{#if s.album_id}<a href="/album/{s.album_id}">{s.album}</a>{:else}{s.album}{/if}{/if}
				</span>
			</div>
			{#if s.duration}<span class="small faint dur">{duration(s.duration)}</span>{/if}
			{#if s.available}<button class="ghost icon sm add" onclick={() => player.addSong(s)} title="Add to queue" aria-label="Add {s.title} to the queue"><ListPlus size={15} /></button>{/if}
			{#if s.available}<DownloadButton kind="song" id={s.id} title={s.title} subtitle={s.artist} thumbnail={s.thumbnail} label="" small />{/if}
		</div>
	{/each}
</div>

<style>
	.list {
		display: flex;
		flex-direction: column;
	}
	.block {
		display: grid;
		grid-template-rows: repeat(var(--rows), auto);
		grid-auto-flow: column;
		grid-auto-columns: minmax(280px, 1fr);
		column-gap: 8px;
		overflow-x: auto;
	}
	.song {
		display: flex;
		min-width: 0;
		align-items: center;
		gap: 12px;
		padding: 6px 4px;
		border-radius: 8px;
	}
	.song:hover {
		background: var(--bg-hover);
	}
	.gone {
		opacity: 0.45;
	}
	.play {
		position: relative;
		width: 42px;
		height: 42px;
		padding: 0;
		flex-shrink: 0;
		overflow: hidden;
	}
	.play img {
		width: 42px;
		height: 42px;
		object-fit: cover;
		border-radius: 6px;
	}
	.n {
		color: var(--text-3);
		font-variant-numeric: tabular-nums;
	}
	.overlay {
		position: absolute;
		inset: 0;
		display: grid;
		place-items: center;
		background: rgb(0 0 0 / 0.45);
		color: #fff;
		opacity: 0;
		border-radius: 6px;
	}
	.play:hover .overlay {
		opacity: 1;
	}
	.dur {
		font-variant-numeric: tabular-nums;
	}
	.add {
		flex-shrink: 0;
		opacity: 0;
	}
	.song:hover .add,
	.add:focus-visible {
		opacity: 1;
	}
	@media (hover: none) {
		.add {
			opacity: 1;
		}
	}
	a:hover {
		text-decoration: underline;
	}
	.pill {
		margin-left: 6px;
	}
</style>
