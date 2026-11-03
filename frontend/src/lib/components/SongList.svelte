<script lang="ts">
	import { Play } from '@lucide/svelte';
	import { duration } from '../format';
	import { player } from '../state.svelte';
	import DownloadButton from './DownloadButton.svelte';

	let { songs, numbered = false }: { songs: any[]; numbered?: boolean } = $props();
</script>

<div class="list">
	{#each songs as s, i (s.id + ':' + i)}
		<div class="song" class:gone={!s.available}>
			<button class="ghost icon play" onclick={() => (player.song = { id: s.id, title: s.title, artist: s.artist, thumbnail: s.thumbnail })} disabled={!s.available} aria-label="Play {s.title}">
				{#if numbered}<span class="n">{s.track ?? i + 1}</span>{:else if s.thumbnail}<img src={s.thumbnail} alt="" loading="lazy" />{/if}
				<span class="overlay"><Play size={15} /></span>
			</button>
			<div class="grow col ellipsis">
				<span class="ellipsis">{s.title}{#if s.is_video}<span class="pill">Video</span>{/if}</span>
				<span class="small muted ellipsis">
					{#if s.artist_id}<a href="/artist/{s.artist_id}">{s.artist}</a>{:else}{s.artist}{/if}{#if s.album && !numbered}
						· {#if s.album_id}<a href="/album/{s.album_id}">{s.album}</a>{:else}{s.album}{/if}{/if}
				</span>
			</div>
			<span class="small faint dur">{duration(s.duration)}</span>
			{#if s.available}<DownloadButton kind="song" id={s.id} title={s.title} subtitle={s.artist} thumbnail={s.thumbnail} label="" small />{/if}
		</div>
	{/each}
</div>

<style>
	.list {
		display: flex;
		flex-direction: column;
	}
	.song {
		display: flex;
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
	a:hover {
		text-decoration: underline;
	}
	.pill {
		margin-left: 6px;
	}
</style>
