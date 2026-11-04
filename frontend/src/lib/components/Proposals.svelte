<script lang="ts">
	import { post } from '../api';
	import { errorToast, toast } from '../state.svelte';

	const LABELS: Record<string, string> = { title: 'Title', artist: 'Artists', album: 'Album', albumartist: 'Album artist', track: 'Track', track_total: 'Tracks', disc: 'Disc', disc_total: 'Discs', year: 'Year', genre: 'Genre', label: 'Label', isrc: 'ISRC' };
	let { proposals, done }: { proposals: any[]; done: () => void } = $props();
	let list = $state<any[]>([]);
	let busy = $state(false);
	$effect(() => {
		list = proposals.filter((p) => p.changed.length || p.cover).map((p) => ({ ...p, include: true }));
	});
	const unchanged = $derived(proposals.length - list.length);
	const chosen = $derived(list.filter((p) => p.include));

	async function apply() {
		busy = true;
		try {
			const changes = chosen.map((p) => ({ id: p.id, fields: Object.fromEntries(p.changed.map((k: string) => [k, p.after[k] ?? ''])), ...(p.cover ? { cover: { url: p.cover } } : {}) }));
			const r = await post('/files/apply', { changes });
			toast(`Updated ${r.changed} ${r.changed === 1 ? 'file' : 'files'}${r.renamed ? `, renamed ${r.renamed}` : ''}`, 'ok');
			if (r.problems.length) toast(`${r.problems.length} problems: ${r.problems[0].reason}`, 'error');
			done();
		} catch (e) {
			errorToast(e);
		} finally {
			busy = false;
		}
	}
</script>

<div class="row">
	<b>{list.length} {list.length === 1 ? 'file changes' : 'files change'}</b>
	{#if unchanged}<span class="small faint">{unchanged} stay the same</span>{/if}
	<span class="spacer"></span>
	<button class="primary" onclick={apply} disabled={busy || !chosen.length}>Apply to {chosen.length}</button>
</div>
<div class="diff">
	{#each list as p (p.id)}
		<label class="change">
			<input type="checkbox" bind:checked={p.include} />
			<div class="grow">
				<code class="small faint">{p.path}</code>
				{#each p.changed as k (k)}
					<div class="line small"><span class="faint key">{LABELS[k] ?? k}</span><span class="old">{p.before[k] || '(empty)'}</span><span class="new">{p.after[k] || '(empty)'}</span></div>
				{/each}
				{#if p.cover}<div class="line small"><span class="faint key">Cover</span><img src={p.cover} alt="" /></div>{/if}
			</div>
		</label>
	{:else}
		<p class="muted">Nothing would change.</p>
	{/each}
</div>

<style>
	.diff {
		display: flex;
		flex-direction: column;
		margin-top: 10px;
	}
	.change {
		display: flex;
		gap: 10px;
		padding: 8px 0;
		border-bottom: 1px solid var(--line);
		cursor: pointer;
		word-break: break-word;
	}
	.line {
		display: grid;
		grid-template-columns: 90px minmax(0, 1fr) minmax(0, 1fr);
		gap: 8px;
		margin-top: 3px;
	}
	.old {
		color: var(--err);
		text-decoration: line-through;
	}
	.new {
		color: var(--ok);
	}
	img {
		width: 40px;
		height: 40px;
		border-radius: 5px;
	}
</style>
