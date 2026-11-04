<script lang="ts">
	import { RefreshCw, Pencil, Sparkles, Trash2, Search, ChevronUp, ChevronDown, FileCheck, Wand2, Play, Music, X } from '@lucide/svelte';
	import { get, post, qs } from '#lib/api.ts';
	import { bytes, duration } from '#lib/format.ts';
	import { errorToast, on, player, toast } from '#lib/state.svelte.ts';
	import BulkEditor from '#lib/components/BulkEditor.svelte';
	import Duplicates from '#lib/components/Duplicates.svelte';
	import FileEditor from '#lib/components/FileEditor.svelte';
	import RenameDialog from '#lib/components/RenameDialog.svelte';
	import Tabs from '#lib/components/Tabs.svelte';

	const PAGE = 100;
	const MISSING = [['cover', 'Cover'], ['artist', 'Artist'], ['title', 'Title'], ['album', 'Album'], ['year', 'Year'], ['genre', 'Genre'], ['lyrics', 'Lyrics']];
	const COLS = [['title', 'Title'], ['artist', 'Artists'], ['album', 'Album'], ['year', 'Year'], ['genre', 'Genre'], ['format', 'Format'], ['bitrate', 'kbit/s'], ['duration', 'Length'], ['size', 'Size'], ['added_at', 'Added']];

	let tab = $state('files');
	let items = $state<any[]>([]);
	let total = $state(0);
	let facets = $state<any>(null);
	let scan = $state<any>({ running: false });
	let filters = $state<Record<string, any>>({ q: '', location_id: '', format: '', genre: '', year: '', artist: '', album: '', source: '' });
	let missing = $state<string[]>([]);
	let misnamed = $state(false);
	let sort = $state('artist');
	let dir = $state('asc');
	let offset = $state(0);
	let selected = $state<Set<number>>(new Set());
	let editing = $state<number | null>(null);
	let bulkOpen = $state(false);
	let bulkStart = $state('set');
	let rename = $state<any>(null);
	let loading = $state(true);

	const params = () => ({ ...filters, missing: missing.join(','), misnamed: misnamed ? 1 : '', sort, dir });
	const pages = $derived(Math.max(1, Math.ceil(total / PAGE)));
	const pageIds = $derived(items.map((i) => i.id));
	const allOnPage = $derived(pageIds.length > 0 && pageIds.every((i) => selected.has(i)));
	const filtered = $derived(Object.values(filters).some(Boolean) || missing.length > 0 || misnamed);

	async function load() {
		try {
			const r = await get(`/files${qs({ ...params(), offset, limit: PAGE })}`);
			items = r.items;
			total = r.total;
			scan = r.scan;
		} catch (e) {
			errorToast(e);
		} finally {
			loading = false;
		}
	}
	async function loadFacets() {
		try {
			facets = await get('/files/facets');
			scan = facets.scan;
		} catch (e) {
			errorToast(e);
		}
	}
	function refresh() {
		load();
		loadFacets();
	}
	refresh();

	let timer: ReturnType<typeof setTimeout>;
	function search() {
		clearTimeout(timer);
		timer = setTimeout(() => {
			offset = 0;
			load();
		}, 220);
	}
	function changed() {
		offset = 0;
		load();
	}
	function reset() {
		for (const k of Object.keys(filters)) filters[k] = '';
		missing = [];
		misnamed = false;
		changed();
	}
	function toggleMissing(k: string) {
		missing = missing.includes(k) ? missing.filter((x) => x !== k) : [...missing, k];
		changed();
	}
	function sortBy(k: string) {
		if (sort === k) dir = dir === 'asc' ? 'desc' : 'asc';
		else {
			sort = k;
			dir = k === 'added_at' ? 'desc' : 'asc';
		}
		changed();
	}
	function toggle(id: number) {
		const s = new Set(selected);
		if (s.has(id)) s.delete(id);
		else s.add(id);
		selected = s;
	}
	function togglePage() {
		const s = new Set(selected);
		for (const i of pageIds) {
			if (allOnPage) s.delete(i);
			else s.add(i);
		}
		selected = s;
	}
	async function selectAll() {
		try {
			selected = new Set((await get(`/files/ids${qs(params())}`)).ids);
		} catch (e) {
			errorToast(e);
		}
	}
	function bulk(start: string) {
		bulkStart = start;
		bulkOpen = true;
	}
	function afterEdit() {
		selected = new Set();
		refresh();
	}
	async function rescan() {
		try {
			await post('/files/scan');
			scan = { ...scan, running: true };
		} catch (e) {
			errorToast(e);
		}
	}
	async function askRename() {
		try {
			rename = await get('/files/rename');
			if (!rename.count) toast('All file names follow the rules', 'ok');
		} catch (e) {
			errorToast(e);
		}
	}
	async function fixSelected() {
		try {
			const r = await post('/files/rename', { ids: [...selected] });
			toast(`Renamed ${r.renamed} ${r.renamed === 1 ? 'file' : 'files'}`, 'ok');
			if (r.problems.length) toast(`${r.problems.length} couldn't be renamed: ${r.problems[0].reason}`, 'error');
			afterEdit();
		} catch (e) {
			errorToast(e);
		}
	}
	async function removeSelected() {
		const n = selected.size;
		if (!confirm(`Delete ${n} ${n === 1 ? 'file' : 'files'} from the disk? This can't be undone.`)) return;
		try {
			const r = await post('/files/delete', { ids: [...selected] });
			toast(`Deleted ${r.deleted} ${r.deleted === 1 ? 'file' : 'files'}`, 'ok');
			afterEdit();
		} catch (e) {
			errorToast(e);
		}
	}
	function play(f: any) {
		player.song = { id: String(f.id), title: f.title ?? f.path, artist: f.artist ?? '', thumbnail: f.has_cover ? `/api/files/${f.id}/cover?size=96` : null, src: `/api/files/${f.id}/audio` };
	}
	$effect(() => on('library', (s) => {
		const finished = scan.running && !s.running;
		scan = s;
		if (finished) refresh();
	}));
	const cell = (f: any, k: string) => {
		if (k === 'duration') return duration(f.duration);
		if (k === 'size') return bytes(f.size);
		if (k === 'format') return f.format?.toUpperCase();
		if (k === 'added_at') return new Date(f.added_at * 1000).toLocaleDateString();
		if (k === 'year') return f.year?.slice(0, 4);
		return f[k];
	};
</script>

<div class="page-head">
	<h1>Library</h1>
	{#if facets}<span class="muted small">{facets.files.toLocaleString()} files · {bytes(facets.bytes)} · {Math.round(facets.seconds / 3600)} h</span>{/if}
	<span class="spacer"></span>
	<Tabs options={[{ value: 'files', label: 'Files' }, { value: 'dups', label: 'Duplicates' }]} bind:value={tab} />
	<button onclick={rescan} disabled={scan.running}><RefreshCw size={15} class={scan.running ? 'spin' : ''} /> {scan.running ? `Scanning ${scan.seen ?? 0}…` : 'Scan'}</button>
</div>

{#if tab === 'dups'}
	<Duplicates changed={refresh} />
{:else}
	{#if facets?.misnamed}
		<div class="banner">
			<span class="grow">{facets.misnamed} {facets.misnamed === 1 ? "file doesn't" : "files don't"} match the file name rules.</span>
			<button class="sm" onclick={() => { misnamed = true; changed(); }}>Show</button>
			<button class="sm primary" onclick={askRename}><FileCheck size={14} /> Rename</button>
		</div>
	{/if}

	<div class="filters">
		<div class="searchbox">
			<Search size={15} />
			<input type="search" placeholder="Search titles, artists, albums, paths" aria-label="Search the library" bind:value={filters.q} oninput={search} />
		</div>
		<select aria-label="Location" bind:value={filters.location_id} onchange={changed}>
			<option value="">All locations</option>
			{#each facets?.locations ?? [] as l (l.id)}<option value={l.id}>{l.name} ({l.n})</option>{/each}
		</select>
		<select aria-label="Format" bind:value={filters.format} onchange={changed}>
			<option value="">All formats</option>
			{#each facets?.formats ?? [] as f (f.value)}<option value={f.value}>{f.value.toUpperCase()} ({f.n})</option>{/each}
		</select>
		<select aria-label="Genre" bind:value={filters.genre} onchange={changed}>
			<option value="">All genres</option>
			{#each facets?.genres ?? [] as g (g.value)}<option value={g.value}>{g.value} ({g.n})</option>{/each}
		</select>
		<select aria-label="Year" bind:value={filters.year} onchange={changed}>
			<option value="">All years</option>
			{#each facets?.years ?? [] as y (y.value)}<option value={y.value}>{y.value} ({y.n})</option>{/each}
		</select>
		<select aria-label="Artist" bind:value={filters.artist} onchange={changed}>
			<option value="">All artists</option>
			{#each facets?.artists ?? [] as a (a.value)}<option value={a.value}>{a.value} ({a.n})</option>{/each}
		</select>
		<select aria-label="Album" bind:value={filters.album} onchange={changed}>
			<option value="">All albums</option>
			{#each facets?.albums ?? [] as a (a.value)}<option value={a.value}>{a.value} ({a.n})</option>{/each}
		</select>
		<select aria-label="Source" bind:value={filters.source} onchange={changed}>
			<option value="">Any source</option>
			<option value="amplicon">Downloaded by Amplicon</option>
			<option value="other">Other files</option>
		</select>
	</div>
	<div class="row wrap chips">
		<span class="small faint">Missing:</span>
		{#each MISSING as [k, label] (k)}
			<button class="chip" class:on={missing.includes(k)} onclick={() => toggleMissing(k)}>{label}{#if facets?.missing?.[k]} <span class="faint">{facets.missing[k]}</span>{/if}</button>
		{/each}
		<button class="chip" class:on={misnamed} onclick={() => { misnamed = !misnamed; changed(); }}>Wrong name{#if facets?.misnamed} <span class="faint">{facets.misnamed}</span>{/if}</button>
		{#if filtered}<button class="ghost sm" onclick={reset}><X size={13} /> Reset</button>{/if}
	</div>

	{#if selected.size}
		<div class="selbar card">
			<b>{selected.size} selected</b>
			<button class="ghost sm" onclick={selectAll}>Select all {total}</button>
			<button class="ghost sm" onclick={() => (selected = new Set())}>Clear</button>
			<span class="spacer"></span>
			<button class="sm" onclick={() => bulk('set')}><Pencil size={14} /> Edit</button>
			<button class="sm" onclick={() => bulk('lookup')}><Wand2 size={14} /> Look up</button>
			<button class="sm" onclick={() => bulk('ai')}><Sparkles size={14} /> AI</button>
			<button class="sm" onclick={fixSelected}><FileCheck size={14} /> Fix names</button>
			<button class="sm danger" onclick={removeSelected}><Trash2 size={14} /> Delete</button>
		</div>
	{/if}

	{#if items.length}
		<div class="card scroll">
			<table class="tbl">
				<thead>
					<tr>
						<th class="c"><input type="checkbox" checked={allOnPage} onchange={togglePage} aria-label="Select this page" /></th>
						<th></th>
						{#each COLS as [k, label] (k)}
							<th aria-sort={sort === k ? (dir === 'asc' ? 'ascending' : 'descending') : 'none'}>
								<button class="ghost th" onclick={() => sortBy(k)}>
									{label}
									{#if sort === k}{#if dir === 'asc'}<ChevronUp size={13} />{:else}<ChevronDown size={13} />{/if}{/if}
								</button>
							</th>
						{/each}
					</tr>
				</thead>
				<tbody>
					{#each items as f (f.id)}
						<tr class:sel={selected.has(f.id)}>
							<td class="c"><input type="checkbox" checked={selected.has(f.id)} onchange={() => toggle(f.id)} aria-label="Select {f.title ?? f.path}" /></td>
							<td class="art">
								<button class="thumb" onclick={() => play(f)} aria-label="Play {f.title ?? f.path}">
									{#if f.has_cover}<img src="/api/files/{f.id}/cover?size=96" alt="" loading="lazy" />{:else}<Music size={15} />{/if}
									<span class="over"><Play size={15} /></span>
								</button>
							</td>
							{#each COLS as [k] (k)}
								<td class={k === 'title' || k === 'artist' || k === 'album' ? 'text' : 'nowrap small muted'}>
									{#if k === 'title'}
										<button class="link ellipsis" onclick={() => (editing = f.id)} title={f.path}>{f.title || f.path}</button>
										{#if f.misnamed}<span class="pill warn" title="Should be {f.target_path}">Name</span>{/if}
										{#if f.error}<span class="pill err" title={f.error}>Broken</span>{/if}
									{:else}
										<span class="ellipsis">{cell(f, k) ?? ''}</span>
									{/if}
								</td>
							{/each}
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
		<div class="row pager">
			<span class="small faint">{offset + 1}–{Math.min(offset + PAGE, total)} of {total.toLocaleString()}</span>
			<span class="spacer"></span>
			<button class="sm" disabled={offset === 0} onclick={() => { offset = Math.max(0, offset - PAGE); load(); }}>Previous</button>
			<span class="small">{Math.floor(offset / PAGE) + 1} / {pages}</span>
			<button class="sm" disabled={offset + PAGE >= total} onclick={() => { offset += PAGE; load(); }}>Next</button>
		</div>
	{:else if !loading}
		<div class="empty">
			{#if filtered}No files match these filters.{:else if scan.running}Scanning your locations…{:else}No files yet. Download something, or press Scan to read your locations.{/if}
		</div>
	{/if}
{/if}

<FileEditor bind:id={editing} done={afterEdit} />
<BulkEditor bind:open={bulkOpen} ids={[...selected]} start={bulkStart} done={afterEdit} />
<RenameDialog bind:plan={rename} done={afterEdit} />

<style>
	.banner {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 8px;
		padding: 8px 12px;
		border-radius: 10px;
		background: var(--warn-soft);
		margin-bottom: 12px;
	}
	.filters {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
		gap: 8px;
	}
	.searchbox {
		grid-column: 1 / -1;
		position: relative;
		display: flex;
		align-items: center;
	}
	.searchbox :global(svg) {
		position: absolute;
		left: 10px;
		color: var(--text-3);
	}
	.searchbox input {
		padding-left: 32px;
	}
	.chips {
		margin: 10px 0 12px;
		gap: 6px;
	}
	.chip {
		height: 26px;
		padding: 0 10px;
		border-radius: 999px;
		font-size: 12.5px;
		font-weight: 480;
	}
	.chip.on {
		background: var(--accent-soft);
		border-color: var(--accent);
		color: var(--accent);
	}
	.selbar {
		position: sticky;
		top: 8px;
		z-index: 5;
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 6px;
		padding: 6px 10px;
		margin-bottom: 10px;
		box-shadow: var(--shadow-lg);
	}
	.scroll {
		overflow-x: auto;
	}
	.tbl th {
		padding: 4px 6px;
	}
	.th {
		height: 28px;
		padding: 0 4px;
		font: inherit;
		color: inherit;
		text-transform: inherit;
		letter-spacing: inherit;
		gap: 2px;
	}
	.c {
		width: 34px;
		padding-right: 0;
	}
	.tbl td {
		padding: 5px 10px;
	}
	.tbl tr.sel td {
		background: var(--accent-soft);
	}
	.art {
		width: 48px;
		padding: 4px;
	}
	.thumb {
		position: relative;
		width: 38px;
		height: 38px;
		padding: 0;
		border: none;
		border-radius: 6px;
		background: var(--bg-3);
		overflow: hidden;
		color: var(--text-3);
	}
	.thumb img {
		width: 100%;
		height: 100%;
		object-fit: cover;
	}
	.over {
		position: absolute;
		inset: 0;
		display: grid;
		place-items: center;
		background: rgb(0 0 0 / 0.5);
		color: #fff;
		opacity: 0;
		transition: opacity 0.12s;
	}
	.thumb:hover .over,
	.thumb:focus-visible .over {
		opacity: 1;
	}
	.text {
		max-width: 240px;
		min-width: 120px;
	}
	.text > span.ellipsis {
		display: block;
	}
	.nowrap {
		white-space: nowrap;
	}
	.link {
		height: auto;
		padding: 0;
		border: none;
		background: none;
		display: inline-block;
		max-width: 100%;
		font-weight: 540;
		text-align: left;
		vertical-align: bottom;
	}
	.link:hover:not(:disabled) {
		background: none;
		color: var(--accent);
	}
	.pager {
		margin-top: 10px;
		gap: 8px;
	}
	:global(.spin) {
		animation: spin 1s linear infinite;
	}
	@keyframes spin {
		to {
			transform: rotate(360deg);
		}
	}
	@media (max-width: 720px) {
		.filters {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
	}
</style>
