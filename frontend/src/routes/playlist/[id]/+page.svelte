<script lang="ts">
	import { page } from '$app/state';
	import { Play, Shuffle } from '@lucide/svelte';
	import { get } from '#lib/api.ts';
	import { duration } from '#lib/format.ts';
	import { errorToast } from '#lib/state.svelte.ts';
	import { player } from '#lib/player.svelte.ts';
	import SongList from '#lib/components/SongList.svelte';
	import DownloadButton from '#lib/components/DownloadButton.svelte';

	let pl = $state<any>(null);
	$effect(() => {
		const id = page.params.id;
		pl = null;
		get(`/playlists/${id}`).then((p) => (pl = p)).catch(errorToast);
	});
</script>

{#if pl}
	<div class="head">
		{#if pl.thumbnail}<img src={pl.thumbnail} alt="" />{/if}
		<div class="col">
			<span class="small faint">Playlist</span>
			<h1>{pl.title}</h1>
			<span class="muted">{pl.author ?? ''} · {pl.track_count} songs · {duration(pl.duration)}</span>
			<div class="row">
				<button class="primary" onclick={() => player.playSongs(pl.tracks)}><Play size={15} /> Play</button>
				<button onclick={() => player.shuffleSongs(pl.tracks)}><Shuffle size={15} /> Shuffle</button>
				<DownloadButton kind="playlist" id={pl.id} title={pl.title} subtitle={pl.author} thumbnail={pl.thumbnail} label="Download playlist" />
			</div>
		</div>
	</div>
	<div class="card pad"><SongList songs={pl.tracks} /></div>
{:else}
	<div class="empty">Loading…</div>
{/if}

<style>
	.head {
		display: flex;
		gap: 20px;
		align-items: flex-end;
		margin-bottom: 20px;
		flex-wrap: wrap;
	}
	.head img {
		width: 180px;
		height: 180px;
		border-radius: 12px;
		object-fit: cover;
	}
	.col {
		gap: 6px;
		min-width: 0;
	}
	.row {
		margin-top: 8px;
	}
	.pad {
		padding: 6px;
	}
</style>
