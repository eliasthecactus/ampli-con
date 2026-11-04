<script lang="ts">
	import { Trash2, EyeOff } from '@lucide/svelte';
	import { get, post } from '../api';
	import { bytes, duration } from '../format';
	import { errorToast, toast } from '../state.svelte';

	let { changed }: { changed: () => void } = $props();
	const KIND: Record<string, string> = { file: 'Identical files', video: 'Same YouTube video', song: 'Same song' };
	let groups = $state<any[] | null>(null);
	let drop = $state<Record<number, boolean>>({});
	let busy = $state(false);

	async function load() {
		try {
			groups = (await get('/files/duplicates')).groups;
			const d: Record<number, boolean> = {};
			for (const g of groups ?? []) for (const f of g.files) d[f.id] = f.id !== g.keep;
			drop = d;
		} catch (e) {
			errorToast(e);
		}
	}
	load();

	const chosen = $derived(Object.keys(drop).filter((k) => drop[+k]).map(Number));
	// A group with every copy ticked would lose the song altogether.
	const wholeGroup = $derived((groups ?? []).some((g) => g.files.every((f: any) => drop[f.id])));

	async function remove(ids: number[]) {
		if (!confirm(`Delete ${ids.length} ${ids.length === 1 ? 'file' : 'files'} from the disk? This can't be undone.`)) return;
		busy = true;
		try {
			const r = await post('/files/delete', { ids });
			toast(`Deleted ${r.deleted} ${r.deleted === 1 ? 'file' : 'files'}`, 'ok');
			if (r.problems.length) toast(r.problems[0].reason, 'error');
			await load();
			changed();
		} catch (e) {
			errorToast(e);
		} finally {
			busy = false;
		}
	}
	async function ignore(g: any) {
		await post('/files/duplicates/ignore', { key: g.key }).catch(errorToast);
		groups = (groups ?? []).filter((x) => x.key !== g.key);
	}
</script>

{#if groups === null}
	<div class="empty">Comparing files…</div>
{:else if !groups.length}
	<div class="empty">No duplicates found.</div>
{:else}
	<div class="row bar">
		<span class="muted">{groups.length} {groups.length === 1 ? 'group' : 'groups'}. The best copy of each stays unticked.</span>
		<span class="spacer"></span>
		<button class="danger" disabled={busy || !chosen.length || wholeGroup} onclick={() => remove(chosen)}><Trash2 size={15} /> Delete {chosen.length} ticked</button>
	</div>
	{#if wholeGroup}<p class="small err">Every copy of a song is ticked. Untick one before deleting.</p>{/if}
	{#each groups as g (g.key)}
		<div class="card group">
			<div class="row">
				<span class="pill {g.kind === 'song' ? 'warn' : 'info'}">{KIND[g.kind]}</span>
				<span class="spacer"></span>
				<button class="ghost sm" onclick={() => ignore(g)} title="Not a duplicate"><EyeOff size={14} /> Not a duplicate</button>
			</div>
			{#each g.files as f (f.id)}
				<label class="file">
					<input type="checkbox" bind:checked={drop[f.id]} aria-label="Delete {f.path}" />
					{#if f.has_cover}<img src="/api/files/{f.id}/cover?size=96" alt="" loading="lazy" />{:else}<span class="ph"></span>{/if}
					<span class="grow names">
						<span class="ellipsis"><b>{f.title ?? f.path}</b>{#if f.artist} · {f.artist}{/if}{#if f.album}<span class="faint"> · {f.album}</span>{/if}</span>
						<code class="small faint ellipsis">{f.location_name}/{f.path}</code>
					</span>
					<span class="small faint meta">{f.format?.toUpperCase()}{f.bitrate ? ` ${f.bitrate}k` : ''} · {duration(f.duration)} · {bytes(f.size)}</span>
					{#if f.id === g.keep}<span class="pill ok">Best</span>{/if}
				</label>
			{/each}
		</div>
	{/each}
{/if}

<style>
	.bar {
		margin-bottom: 12px;
		flex-wrap: wrap;
	}
	.group {
		padding: 10px 12px;
		margin-bottom: 10px;
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.file {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 6px 0;
		cursor: pointer;
		min-width: 0;
	}
	img,
	.ph {
		width: 38px;
		height: 38px;
		border-radius: 6px;
		background: var(--bg-3);
		flex: none;
		object-fit: cover;
	}
	.names {
		display: flex;
		flex-direction: column;
		min-width: 0;
	}
	.meta {
		white-space: nowrap;
	}
	.err {
		color: var(--err);
		margin-bottom: 8px;
	}
	@media (max-width: 720px) {
		.meta {
			display: none;
		}
	}
</style>
