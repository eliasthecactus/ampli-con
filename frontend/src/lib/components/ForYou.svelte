<script lang="ts">
	import { RefreshCw } from '@lucide/svelte';
	import { get, qs } from '../api';
	import { errorToast } from '../state.svelte';
	import MediaTile from './MediaTile.svelte';
	import SongList from './SongList.svelte';

	let feed = $state<any>(null);
	let failed = $state(false);
	let busy = $state(false);

	async function load(refresh = false) {
		busy = true;
		try {
			feed = await get('/home' + qs(refresh ? { refresh: 'true' } : {}));
			failed = false;
		} catch (e) {
			failed = true;
			if (refresh) errorToast(e); // on the way in, the search box is still all you need
		} finally {
			busy = false;
		}
	}
	load();

	const onlySongs = (s: any) => s.items.every((i: any) => i.type === 'song');
</script>

{#if feed?.sections.length}
	<div class="row head">
		<h2>For you</h2>
		<span class="spacer"></span>
		<button class="ghost sm" onclick={() => load(true)} disabled={busy}><RefreshCw size={14} /> Refresh</button>
	</div>
	{#if !feed.personalized}
		<p class="small muted">These aren't your recommendations yet. Connect your account on the <a href="/youtube">YouTube page</a> to see your own.</p>
	{/if}
	{#each feed.sections as s, i (i)}
		<section>
			<h3>{s.title}</h3>
			{#if onlySongs(s)}
				<div class="card pad"><SongList songs={s.items.map((x: any) => x.item)} rows={4} /></div>
			{:else}
				<div class="shelf">
					{#each s.items as x, n (n)}<MediaTile type={x.type} item={x.item} />{/each}
				</div>
			{/if}
		</section>
	{/each}
{:else if busy}
	<div class="empty">Loading what's for you…</div>
{:else}
	<div class="empty">
		Search YouTube Music, play right here, and download songs, albums or playlists with one of your profiles. The
		Ampli app does the same from your phone.
	</div>
{/if}

<style>
	.head {
		margin-bottom: 6px;
	}
	section {
		margin-top: 22px;
		min-width: 0;
	}
	h3 {
		margin: 0 0 10px;
	}
	.pad {
		padding: 6px;
	}
	.shelf {
		display: grid;
		grid-auto-flow: column;
		grid-auto-columns: 160px;
		gap: 16px;
		overflow-x: auto;
		padding-bottom: 8px;
	}
	a {
		text-decoration: underline;
	}
</style>
