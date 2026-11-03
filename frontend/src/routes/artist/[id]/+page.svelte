<script lang="ts">
	import { page } from '$app/state';
	import { get } from '#lib/api.ts';
	import { errorToast } from '#lib/state.svelte.ts';
	import SongList from '#lib/components/SongList.svelte';
	import DownloadButton from '#lib/components/DownloadButton.svelte';

	let artist = $state<any>(null);
	$effect(() => {
		const id = page.params.id;
		artist = null;
		get(`/artists/${id}`).then((a) => (artist = a)).catch(errorToast);
	});
</script>

{#if artist}
	<div class="head">
		{#if artist.thumbnail}<img src={artist.thumbnail} alt="" />{/if}
		<div class="col">
			<span class="small faint">Artist</span>
			<h1>{artist.name}</h1>
		</div>
	</div>
	{#if artist.songs.length}
		<h2 class="section-title">Top songs</h2>
		<div class="card pad"><SongList songs={artist.songs} /></div>
	{/if}
	{#each [['Albums', artist.albums], ['Singles', artist.singles]] as [label, list] (label)}
		{#if list.length}
			<h2 class="section-title">{label}</h2>
			<div class="tiles">
				{#each list as a (a.id)}
					<div class="tile">
						<a href="/album/{a.id}"><img src={a.thumbnail} alt="" loading="lazy" /></a>
						<a href="/album/{a.id}" class="ellipsis"><b>{a.title}</b></a>
						<span class="small muted">{a.year ?? ''}</span>
						<DownloadButton kind="album" id={a.id} title={a.title} subtitle={artist.name} thumbnail={a.thumbnail} label="Album" small />
					</div>
				{/each}
			</div>
		{/if}
	{/each}
{:else}
	<div class="empty">Loading…</div>
{/if}

<style>
	.head {
		display: flex;
		gap: 20px;
		align-items: flex-end;
		margin-bottom: 10px;
	}
	.head img {
		width: 140px;
		height: 140px;
		border-radius: 50%;
		object-fit: cover;
	}
	.pad {
		padding: 6px;
	}
	.section-title {
		margin: 22px 0 10px;
	}
	.tiles {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
		gap: 16px;
	}
	.tile {
		display: flex;
		flex-direction: column;
		gap: 4px;
		min-width: 0;
	}
	.tile img {
		width: 100%;
		aspect-ratio: 1;
		object-fit: cover;
		border-radius: 10px;
	}
	.tile :global(.dl) {
		margin-top: 4px;
		align-self: flex-start;
	}
</style>
