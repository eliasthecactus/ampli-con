<script lang="ts">
	import { X, RotateCcw, Trash2, ChevronRight, Disc3, ListMusic, Music } from '@lucide/svelte';
	import { del, get, post } from '#lib/api.ts';
	import { ago } from '#lib/format.ts';
	import { ACTIVE, errorToast, live, on, type Job } from '#lib/state.svelte.ts';

	let history = $state<Job[]>([]);
	let openId = $state<number | null>(null);
	let detail = $state<any>(null);

	async function load() {
		try {
			history = (await get<Job[]>('/jobs?limit=200')).filter((j) => !ACTIVE.has(j.status));
		} catch (e) {
			errorToast(e);
		}
	}
	load();
	$effect(() => on('job', (j: Job) => {
		if (!ACTIVE.has(j.status)) {
			history = [j, ...history.filter((h) => h.id !== j.id)];
		}
		if (openId === j.id) loadDetail(j.id);
	}));
	$effect(() => on('item', (d) => { if (openId === d.job_id) loadDetail(d.job_id); }));
	$effect(() => on('job_removed', (d) => (history = history.filter((h) => h.id !== d.id))));
	$effect(() => on('jobs_cleared', () => load()));

	const active = $derived(Object.values(live.jobs).sort((a, b) => a.id - b.id));

	async function loadDetail(id: number) {
		try {
			detail = await get(`/jobs/${id}`);
		} catch {
			/* gone */
		}
	}
	function toggle(id: number) {
		openId = openId === id ? null : id;
		detail = null;
		if (openId) loadDetail(id);
	}
	async function act(fn: () => Promise<unknown>) {
		try {
			await fn();
		} catch (e) {
			errorToast(e);
		}
	}
	const STATUS: Record<string, [string, string]> = {
		preparing: ['Reading from YouTube', 'info'],
		queued: ['Waiting', 'muted'],
		running: ['Downloading', 'info'],
		done: ['Done', 'ok'],
		partial: ['Partly failed', 'warn'],
		failed: ['Failed', 'err'],
		cancelled: ['Cancelled', 'muted']
	};
	const ITEM: Record<string, string> = { queued: 'Waiting', downloading: 'Downloading', done: 'Saved', skipped: 'Already there', failed: 'Failed', cancelled: 'Cancelled' };
</script>

{#snippet jobRow(j: Job)}
	<div class="job" class:open={openId === j.id}>
		<button class="ghost head" onclick={() => toggle(j.id)}>
			<ChevronRight size={15} class="chev" />
			{#if j.thumbnail}<img src={j.thumbnail} alt="" />{:else}<span class="icon-ph">{#if j.kind === 'album'}<Disc3 size={18} />{:else if j.kind === 'playlist'}<ListMusic size={18} />{:else}<Music size={18} />{/if}</span>{/if}
			<div class="grow col ellipsis">
				<span class="ellipsis"><b>{j.title}</b>{#if j.subtitle}<span class="muted">{' · '}{j.subtitle}</span>{/if}</span>
				<span class="small faint ellipsis">
					{j.kind} · {j.profile_name} → {j.location_name} · {j.requested_by ?? ''} · {ago(j.finished_at ?? j.created_at)}
				</span>
				{#if ACTIVE.has(j.status)}
					<div class="bar"><span style:width="{Math.round(j.progress * 100)}%"></span></div>
					{#if j.current?.length}<span class="small muted ellipsis">{j.current.map((c) => c.title).join(', ')}</span>{/if}
				{:else if j.error}
					<span class="small err ellipsis">{j.error}</span>
				{/if}
			</div>
			<span class="counts small faint">{j.done + j.skipped}/{j.total || '…'}{#if j.failed} · <span class="err">{j.failed} failed</span>{/if}</span>
			<span class="pill {STATUS[j.status]?.[1]}">{STATUS[j.status]?.[0] ?? j.status}</span>
		</button>
		<div class="actions">
			{#if ACTIVE.has(j.status)}
				<button class="ghost icon sm" title="Cancel" onclick={() => act(() => post(`/jobs/${j.id}/cancel`))}><X size={15} /></button>
			{:else}
				{#if j.status !== 'done'}<button class="ghost icon sm" title="Retry" onclick={() => act(() => post(`/jobs/${j.id}/retry`))}><RotateCcw size={15} /></button>{/if}
				<button class="ghost icon sm" title="Remove from the list" onclick={() => act(() => del(`/jobs/${j.id}`))}><Trash2 size={15} /></button>
			{/if}
		</div>
		{#if openId === j.id && detail}
			<div class="items">
				{#each detail.items as it (it.id)}
					<div class="item">
						<span class="small faint n">{it.position}</span>
						<div class="grow col ellipsis">
							<span class="ellipsis">{it.title}{#if it.artist}<span class="muted">{' · '}{it.artist}</span>{/if}</span>
							{#if it.file_path}<code class="small faint ellipsis">{it.file_path}</code>{/if}
							{#if it.error}<span class="small err">{it.error}</span>{/if}
						</div>
						{#if it.status === 'downloading'}
							<div class="bar small-bar"><span style:width="{Math.round(it.progress * 100)}%"></span></div>
						{/if}
						<span class="small {it.status === 'failed' ? 'err' : 'faint'}">{ITEM[it.status] ?? it.status}</span>
					</div>
				{/each}
			</div>
		{/if}
	</div>
{/snippet}

<div class="page-head">
	<h1>Downloads</h1>
	<span class="spacer"></span>
	{#if history.length}<button onclick={() => act(() => del('/jobs'))}><Trash2 size={15} /> Clear finished</button>{/if}
</div>

<h2 class="section-title">In progress</h2>
{#if active.length}
	<div class="card list">{#each active as j (j.id)}{@render jobRow(j)}{/each}</div>
{:else}
	<div class="empty">Nothing downloading. Start a download from Search or in the Ampli app.</div>
{/if}

<h2 class="section-title">Finished</h2>
{#if history.length}
	<div class="card list">{#each history as j (j.id)}{@render jobRow(j)}{/each}</div>
{:else}
	<div class="empty">No finished downloads.</div>
{/if}

<style>
	.section-title {
		margin: 20px 0 10px;
	}
	.list {
		padding: 4px;
	}
	.job {
		position: relative;
		border-bottom: 1px solid var(--line);
	}
	.job:last-child {
		border-bottom: none;
	}
	.head {
		width: 100%;
		height: auto;
		justify-content: flex-start;
		gap: 12px;
		padding: 10px 84px 10px 8px;
		text-align: left;
		font-weight: 450;
	}
	.head :global(.chev) {
		flex-shrink: 0;
		color: var(--text-3);
		transition: transform 0.15s;
	}
	.open .head :global(.chev) {
		transform: rotate(90deg);
	}
	.head img,
	.icon-ph {
		width: 44px;
		height: 44px;
		border-radius: 7px;
		object-fit: cover;
		flex-shrink: 0;
		background: var(--bg-3);
		display: grid;
		place-items: center;
		color: var(--text-3);
	}
	.col {
		gap: 3px;
	}
	.counts {
		font-variant-numeric: tabular-nums;
	}
	.actions {
		position: absolute;
		right: 6px;
		top: 18px;
		display: flex;
		gap: 2px;
	}
	.bar {
		height: 5px;
		border-radius: 99px;
		background: var(--bg-3);
		overflow: hidden;
		max-width: 420px;
	}
	.bar span {
		display: block;
		height: 100%;
		background: var(--accent);
		transition: width 0.3s;
	}
	.small-bar {
		width: 90px;
	}
	.items {
		padding: 0 10px 10px 62px;
	}
	.item {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 5px 0;
		border-top: 1px dashed var(--line);
	}
	.n {
		width: 22px;
		text-align: right;
		font-variant-numeric: tabular-nums;
	}
	.err {
		color: var(--err);
	}
	@media (max-width: 700px) {
		.counts {
			display: none;
		}
		.items {
			padding-left: 10px;
		}
	}
</style>
