<script lang="ts">
	import { Search } from '@lucide/svelte';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { get, qs } from '#lib/api.ts';
	import { errorToast } from '#lib/state.svelte.ts';
	import SongList from '#lib/components/SongList.svelte';
	import ForYou from '#lib/components/ForYou.svelte';
	import MediaTile from '#lib/components/MediaTile.svelte';

	let q = $state(page.url.searchParams.get('q') ?? '');
	let results = $state<any>(null);
	let busy = $state(false);

	async function run(text: string) {
		if (!text.trim()) {
			results = null;
			return;
		}
		busy = true;
		try {
			results = await get('/search' + qs({ q: text }));
		} catch (e) {
			errorToast(e);
		} finally {
			busy = false;
		}
	}
	$effect(() => {
		const text = page.url.searchParams.get('q') ?? '';
		q = text;
		run(text);
	});
	function submit(e: Event) {
		e.preventDefault();
		goto('/' + qs({ q }));
	}
</script>

<div class="page-head">
	<h1>Search</h1>
</div>
<form class="search" onsubmit={submit}>
	<Search size={18} />
	<input type="search" bind:value={q} placeholder="Songs, albums, playlists, artists on YouTube Music" />
	<button class="primary" type="submit" disabled={busy}>Search</button>
</form>

{#if results}
	{#if results.songs.length}
		<h2 class="section-title">Songs</h2>
		<div class="card pad"><SongList songs={results.songs} /></div>
	{/if}
	{#if results.albums.length}
		<h2 class="section-title">Albums</h2>
		<div class="tiles">{#each results.albums as a (a.id)}<MediaTile type="album" item={a} />{/each}</div>
	{/if}
	{#if results.playlists.length}
		<h2 class="section-title">Playlists</h2>
		<div class="tiles">{#each results.playlists as p (p.id)}<MediaTile type="playlist" item={p} />{/each}</div>
	{/if}
	{#if results.artists.length}
		<h2 class="section-title">Artists</h2>
		<div class="tiles">{#each results.artists as a (a.id)}<MediaTile type="artist" item={a} />{/each}</div>
	{/if}
	{#if !results.songs.length && !results.albums.length && !results.playlists.length && !results.artists.length}
		<div class="empty">Nothing found.</div>
	{/if}
{:else if !busy}
	<ForYou />
{/if}

<style>
	.search {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 6px 6px 6px 14px;
		border: 1px solid var(--line-2);
		border-radius: 12px;
		background: var(--bg-2);
		margin-bottom: 22px;
		color: var(--text-3);
	}
	.search input {
		flex: 1;
		border: none !important;
		background: transparent !important;
		box-shadow: none !important;
		font-size: 15px;
		padding: 0;
	}
	.pad {
		padding: 6px;
	}
	.section-title {
		margin: 22px 0 10px;
	}
	.tiles {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
		gap: 16px;
	}
</style>
