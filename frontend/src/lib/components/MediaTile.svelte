<script lang="ts">
	import { Play } from '@lucide/svelte';
	import { player } from '../player.svelte';
	import DownloadButton from './DownloadButton.svelte';

	// queue: the songs around this one, which play on after it.
	let { type, item, queue }: { type: string; item: any; queue?: any[] } = $props();
	const href = $derived(type === 'song' ? null : `/${type}/${item.id}`);
</script>

{#if type === 'artist'}
	<a class="tile round" {href}>
		{#if item.thumbnail}<img src={item.thumbnail} alt="" loading="lazy" />{:else}<div class="ph"></div>{/if}
		<b class="ellipsis">{item.name}</b>
	</a>
{:else if type === 'song'}
	<div class="tile">
		<button class="cover" onclick={() => player.playSongs(queue ?? [item], item)} aria-label="Play {item.title}">
			{#if item.thumbnail}<img src={item.thumbnail} alt="" loading="lazy" />{:else}<div class="ph"></div>{/if}
			<span class="overlay"><Play size={22} /></span>
		</button>
		<b class="ellipsis">{item.title}</b>
		<span class="small muted ellipsis">{item.artist}</span>
		<DownloadButton kind="song" id={item.id} title={item.title} subtitle={item.artist} thumbnail={item.thumbnail} label="Song" small />
	</div>
{:else}
	<div class="tile">
		<a {href}>{#if item.thumbnail}<img src={item.thumbnail} alt="" loading="lazy" />{:else}<div class="ph"></div>{/if}</a>
		<a {href} class="ellipsis"><b>{item.title}</b></a>
		{#if type === 'album'}
			<span class="small muted ellipsis">{item.type} · {item.artist}{item.year ? ` · ${item.year}` : ''}</span>
			<DownloadButton kind="album" id={item.id} title={item.title} subtitle={item.artist} thumbnail={item.thumbnail} label="Album" small />
		{:else}
			<span class="small muted ellipsis">{item.author ?? ''}{item.track_count ? ` · ${item.track_count} songs` : ''}</span>
		{/if}
	</div>
{/if}

<style>
	.tile {
		display: flex;
		flex-direction: column;
		gap: 4px;
		min-width: 0;
	}
	.tile img,
	.ph {
		display: block;
		width: 100%;
		aspect-ratio: 1;
		object-fit: cover;
		border-radius: 10px;
		background: var(--bg-3);
	}
	.round img,
	.round .ph {
		border-radius: 50%;
	}
	.cover {
		position: relative;
		display: block;
		width: 100%;
		padding: 0;
		border: none;
		background: none;
		cursor: pointer;
	}
	.overlay {
		position: absolute;
		inset: 0;
		display: grid;
		place-items: center;
		background: rgb(0 0 0 / 0.45);
		color: #fff;
		opacity: 0;
		border-radius: 10px;
	}
	.cover:hover .overlay,
	.cover:focus-visible .overlay {
		opacity: 1;
	}
	.tile :global(.dl) {
		margin-top: 4px;
		align-self: flex-start;
	}
</style>
