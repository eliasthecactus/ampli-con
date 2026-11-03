<script lang="ts">
	import { page } from '$app/state';
	import { get } from '#lib/api.ts';
	import { duration } from '#lib/format.ts';
	import { errorToast } from '#lib/state.svelte.ts';
	import SongList from '#lib/components/SongList.svelte';
	import DownloadButton from '#lib/components/DownloadButton.svelte';

	let album = $state<any>(null);
	$effect(() => {
		const id = page.params.id;
		album = null;
		get(`/albums/${id}`).then((a) => (album = a)).catch(errorToast);
	});
</script>

{#if album}
	<div class="head">
		<img src={album.thumbnail} alt="" />
		<div class="col">
			<span class="small faint">{album.type}</span>
			<h1>{album.title}</h1>
			<span class="muted">{#if album.artist_id}<a href="/artist/{album.artist_id}">{album.artist}</a>{:else}{album.artist}{/if}{album.year ? ` · ${album.year}` : ''} · {album.track_count} songs · {duration(album.duration)}</span>
			<div class="row"><DownloadButton kind="album" id={album.id} title={album.title} subtitle={album.artist} thumbnail={album.thumbnail} label="Download album" /></div>
		</div>
	</div>
	<div class="card pad"><SongList songs={album.tracks} numbered /></div>
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
	a:hover {
		text-decoration: underline;
	}
</style>
