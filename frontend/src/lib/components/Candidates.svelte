<script lang="ts">
	import { Search } from '@lucide/svelte';

	let {
		candidates,
		busy = false,
		onpick,
		onsearch
	}: { candidates: any[]; busy?: boolean; onpick: (c: any) => void; onsearch: (q: string) => void } = $props();
	let q = $state('');
	const percent = (s: number) => `${Math.round((s ?? 0) * 100)} %`;
</script>

<div class="col">
	<form class="row" onsubmit={(e) => { e.preventDefault(); onsearch(q); }}>
		<input type="search" bind:value={q} placeholder="Search for another match" aria-label="Search for another match" />
		<button class="icon" type="submit" disabled={busy} aria-label="Search"><Search size={15} /></button>
	</form>
	{#each candidates as c, i (c.source + c.id + i)}
		<button class="ghost cand" onclick={() => onpick(c)}>
			{#if c.cover}<img src={c.cover} alt="" loading="lazy" />{:else}<span class="ph"></span>{/if}
			<span class="grow col2 ellipsis">
				<span class="ellipsis"><b>{c.title}</b> · {c.artist}</span>
				<span class="small faint ellipsis">{[c.album, c.year?.slice(0, 4), c.genre, c.label].filter(Boolean).join(' · ')}</span>
			</span>
			<span class="pill {c.score >= 0.82 ? 'ok' : 'warn'}">{percent(c.score)}</span>
		</button>
	{:else}
		<p class="small faint">{busy ? 'Searching…' : 'No matches found.'}</p>
	{/each}
</div>

<style>
	.cand {
		height: auto;
		padding: 6px;
		justify-content: flex-start;
		gap: 10px;
		text-align: left;
		font-weight: 400;
		white-space: normal;
		width: 100%;
	}
	img,
	.ph {
		width: 40px;
		height: 40px;
		border-radius: 6px;
		object-fit: cover;
		background: var(--bg-3);
		flex: none;
	}
	.col2 {
		display: flex;
		flex-direction: column;
	}
</style>
