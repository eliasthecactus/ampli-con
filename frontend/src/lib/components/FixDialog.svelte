<script lang="ts">
	import { FileCheck, Pencil, Wand2 } from '@lucide/svelte';
	import { get, post } from '../api';
	import { errorToast, on, toast } from '../state.svelte';
	import Modal from './Modal.svelte';
	import Tabs from './Tabs.svelte';

	let { open = $bindable(false), ids = null, edit, select, done }: { open?: boolean; ids?: number[] | null; edit: (id: number) => void; select: (ids: number[]) => void; done: () => void } = $props();

	const MOVES_PAGE = 200;
	const MOVES_MAX = 5000;
	const LABELS: Record<string, string> = { title: 'Title', artist: 'Artists', album: 'Album', albumartist: 'Album artist', track: 'Track', track_total: 'Tracks', disc: 'Disc', disc_total: 'Discs', year: 'Year', genre: 'Genre', label: 'Label', isrc: 'ISRC' };
	let counts = $state<any>({ queued: 0, apply: 0, confident: 0, check: 0, none: 0, failed: 0 });
	let eligible = $state(0);
	let moves = $state<any>({ count: 0, samples: [] });
	let moveLimit = $state(MOVES_PAGE);
	let group = $state('confident');
	let items = $state<any[]>([]);
	let unchecked = $state<Set<number>>(new Set());
	let mode = $state('fill');
	let busy = $state(false);
	let total = $state(0);

	const working = $derived(counts.queued + counts.apply > 0);
	const results = $derived(counts.confident + counts.check + counts.none + counts.failed);
	const groups = $derived([
		{ value: 'confident', label: `Sure (${counts.confident})` },
		{ value: 'check', label: `Check (${counts.check})` },
		{ value: 'none', label: `Not found (${counts.none})` },
		...(counts.failed ? [{ value: 'failed', label: `Failed (${counts.failed})` }] : [])
	]);
	const picked = $derived(items.filter((i) => !unchecked.has(i.id)).map((i) => i.id));
	const label = (n: number) => `${n.toLocaleString()} ${n === 1 ? 'file' : 'files'}`;

	async function load() {
		try {
			const r = await get(`/files/fix?group=${group}`);
			counts = r.counts;
			items = r.items;
			eligible = ids ? ids.length : r.eligible;
			await loadMoves();
		} catch (e) {
			errorToast(e);
		}
	}
	const loadMoves = async () => {
		moves = await post('/files/rename/preview', { ...(ids ? { ids } : {}), limit: moveLimit });
	};
	function moreMoves() {
		moveLimit = Math.min(moveLimit + MOVES_PAGE * 5, MOVES_MAX);
		loadMoves().catch(errorToast);
	}
	$effect(() => {
		if (open) {
			unchecked = new Set();
			moveLimit = MOVES_PAGE;
			load();
		}
	});
	$effect(() => on('fix', (c) => {
		const was = counts.queued + counts.apply;
		counts = c;
		total = Math.max(total, c.queued + c.apply);
		if (open && was && !(c.queued + c.apply)) {
			total = 0;
			if (!c.confident && c.check) group = 'check';
			load();
			done();
		}
	}));
	function pick(id: number) {
		const s = new Set(unchecked);
		if (s.has(id)) s.delete(id);
		else s.add(id);
		unchecked = s;
	}
	async function run(fn: () => Promise<void>) {
		busy = true;
		try {
			await fn();
		} catch (e) {
			errorToast(e);
		} finally {
			busy = false;
		}
	}
	const moveAll = () => run(async () => {
		const r = await post('/files/rename', ids ? { ids } : {});
		toast(`Moved ${label(r.renamed)}`, 'ok');
		if (r.problems.length) toast(`${r.problems.length} couldn't be moved: ${r.problems[0].reason}`, 'error');
		await load();
		done();
	});
	const find = () => run(async () => {
		const r = await post('/files/fix', { ids, mode });
		total = r.queued;
		counts = { ...counts, queued: r.queued };
		group = 'confident';
	});
	const acceptAll = () => run(async () => {
		await post('/files/fix/apply', { group });
		await load();
	});
	const acceptPicked = () => run(async () => {
		await post('/files/fix/apply', { ids: picked });
		await load();
	});
	const discard = () => run(async () => {
		await post('/files/fix/discard', {});
		items = [];
		await load();
	});
	function editOne(id: number) {
		post('/files/fix/discard', { ids: [id] }).catch(errorToast);
		open = false;
		edit(id);
	}
	function selectRest() {
		select(items.map((i) => i.id));
		open = false;
	}
	function tab() {
		unchecked = new Set();
		load();
	}
</script>

<Modal bind:open title="Fix files" width="780px">
	<div class="col">
		<section>
			<h3>Names and locations</h3>
			{#if moves.count}
				<p>{label(moves.count)} {moves.count === 1 ? "doesn't have the name or location of its" : "don't have the name or location of their"} profile.</p>
				<div class="moves">
					{#each moves.samples as s (s.id)}
						<div class="small sample"><code class="faint">{#if s.target_location !== s.location}{s.location}: {/if}{s.path}</code><br /><code class="to">→ {#if s.target_location !== s.location}{s.target_location}: {/if}{s.target_path}</code></div>
					{/each}
					{#if moves.count > moves.samples.length}
						<div class="row more">
							<span class="small faint">And {label(moves.count - moves.samples.length)} more.</span>
							{#if moveLimit < MOVES_MAX}<button class="ghost sm" onclick={moreMoves}>Show more</button>{/if}
						</div>
					{/if}
				</div>
				<p class="hint">A file is skipped if the new name is already taken. Files without a title or artist keep their name until they have tags.</p>
				<button onclick={moveAll} disabled={busy}><FileCheck size={15} /> Move {label(moves.count)}</button>
			{:else}
				<p class="muted">All files are where they belong.</p>
			{/if}
		</section>

		<section>
			<h3>Tags and covers</h3>
			{#if working}
				<p>{counts.apply ? `Writing ${label(counts.apply)}…` : `Looking up ${label(counts.queued)}…`}</p>
				<progress max={total || 1} value={(total || 1) - counts.queued - counts.apply}></progress>
				<p class="hint">You can close this, it keeps running on the server.</p>
			{:else if results}
				<Tabs options={groups} bind:value={group} onchange={tab} />
				{#if group === 'none' || group === 'failed'}
					<div class="row bar">
						<span class="grow small muted">{group === 'none' ? 'Nothing new found for these files.' : 'These files couldn’t be read or written.'}</span>
						{#if items.length}<button class="sm" onclick={selectRest}>Select in library</button>{/if}
						<button class="sm ghost" onclick={discard} disabled={busy}>Clear all results</button>
					</div>
				{:else if items.length}
					<div class="row bar">
						<span class="grow small muted">Nothing is written until you accept it. The name and location follow the new tags.</span>
						<button class="sm ghost" onclick={discard} disabled={busy}>Discard all</button>
						<button class="sm" onclick={acceptPicked} disabled={busy || !picked.length}>Accept {picked.length} shown</button>
						<button class="sm primary" onclick={acceptAll} disabled={busy}>Accept all {counts[group]}</button>
					</div>
				{/if}
				<div class="items">
					{#each items as p (p.id)}
						<div class="item">
							{#if group === 'confident' || group === 'check'}<input type="checkbox" checked={!unchecked.has(p.id)} onchange={() => pick(p.id)} aria-label="Accept {p.path}" />{/if}
							<div class="grow">
								<div class="row"><code class="small faint grow">{p.path}</code>
									{#if group === 'check' || group === 'confident'}<span class="pill {group === 'confident' ? 'ok' : 'warn'}">{p.source === 'file name' ? 'From file name' : `${Math.round(p.score * 100)} %`}</span>{/if}
									<button class="ghost sm" onclick={() => editOne(p.id)} aria-label="Edit {p.path}"><Pencil size={13} /></button>
								</div>
								{#each p.changed ?? [] as k (k)}
									<div class="line small"><span class="faint">{LABELS[k] ?? k}</span><span class="old">{p.before[k] || '(empty)'}</span><span class="new">{p.after[k] || '(empty)'}</span></div>
								{/each}
								{#if p.cover}<div class="line small"><span class="faint">Cover</span><img src={p.cover} alt="" /></div>{/if}
								{#if p.folder_cover}<div class="line small"><span class="faint">Cover</span><img src="/api/files/{p.id}/folder-cover" alt="" /><span class="faint">from the folder</span></div>{/if}
								{#if p.error}<div class="small err">{p.error}</div>{/if}
							</div>
						</div>
					{:else}
						<p class="muted">Nothing here.</p>
					{/each}
				</div>
				{#if counts[group] > items.length}<p class="hint">Showing the first {items.length} of {counts[group].toLocaleString()}.</p>{/if}
			{:else if eligible}
				<p>{label(eligible)} {eligible === 1 ? 'lacks' : 'lack'} tags their profile needs, or a cover.</p>
				<label class="check"><input type="radio" bind:group={mode} value="fill" /> Only fill what's empty, keep the tags that are there</label>
				<label class="check"><input type="radio" bind:group={mode} value="all" /> Replace tags with the match</label>
				<p class="hint">Amplicon looks up every file on Deezer, about two per second, and uses the file name and folders of files without tags. You see every change before it's written.</p>
				<button class="primary" onclick={find} disabled={busy}><Wand2 size={15} /> Find tags for {label(eligible)}</button>
			{:else}
				<p class="muted">No tags or covers are missing.</p>
			{/if}
		</section>
	</div>
	{#snippet footer()}
		<button onclick={() => (open = false)}>Close</button>
	{/snippet}
</Modal>

<style>
	.col {
		display: flex;
		flex-direction: column;
		gap: 18px;
	}
	h3 {
		margin: 0 0 6px;
		font-size: 14px;
	}
	.moves {
		max-height: 280px;
		margin: 8px 0;
		padding: 2px 10px;
		overflow-y: auto;
		border: 1px solid var(--line);
		border-radius: 8px;
	}
	.sample {
		margin: 6px 0;
		word-break: break-all;
	}
	.more {
		gap: 8px;
		margin: 6px 0;
	}
	.to {
		color: var(--ok);
	}
	progress {
		width: 100%;
		accent-color: var(--accent);
	}
	.bar {
		flex-wrap: wrap;
		gap: 6px;
		margin: 10px 0 4px;
	}
	.items {
		display: flex;
		flex-direction: column;
	}
	.item {
		display: flex;
		gap: 10px;
		padding: 8px 0;
		border-bottom: 1px solid var(--line);
		word-break: break-word;
	}
	.item .grow {
		min-width: 0;
	}
	.line {
		display: grid;
		grid-template-columns: 90px minmax(0, 1fr) minmax(0, 1fr);
		gap: 8px;
		margin-top: 3px;
		align-items: center;
	}
	.old {
		color: var(--err);
		text-decoration: line-through;
	}
	.new {
		color: var(--ok);
	}
	.err {
		color: var(--err);
	}
	img {
		width: 40px;
		height: 40px;
		border-radius: 5px;
		object-fit: cover;
	}
</style>
