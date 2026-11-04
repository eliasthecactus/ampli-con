<script lang="ts">
	import { ChevronDown, ChevronRight, EyeOff, FolderInput, Play, RotateCcw } from '@lucide/svelte';
	import { get, post, qs } from '../api';
	import { bytes } from '../format';
	import { pick } from '../select';
	import { errorToast, player, toast } from '../state.svelte';

	let { changed, imported }: { changed: () => void; imported: () => void } = $props();

	type Folder = { location_id: number; location_name: string; folder: string; files: number; bytes: number; formats: string[]; downloaded: number };
	let view = $state<'new' | 'ignored'>('new');
	let folders = $state<Folder[]>([]);
	let total = $state(0);
	let more = $state(0);
	let loading = $state(true);
	let busy = $state(false);
	let profiles = $state<any[]>([]);
	let profile = $state<number | null>(null);
	let picked = $state<Set<string>>(new Set());
	let pickedFiles = $state<Set<number>>(new Set());
	let open = $state<Record<string, any[]>>({});
	let lastFolder: string | null = null;
	let lastFile: number | null = null;

	const key = (f: Folder) => `${f.location_id}:${f.folder}`;
	const chosen = $derived(folders.filter((f) => picked.has(key(f))));
	const count = $derived(chosen.reduce((n, f) => n + f.files, 0) + pickedFiles.size);
	const body = () => ({ folders: chosen.map((f) => ({ location_id: f.location_id, folder: f.folder })), ids: [...pickedFiles], state: view });

	async function load() {
		try {
			const r = await get(`/files/import${qs({ state: view })}`);
			folders = r.folders;
			total = r.total;
			more = r.more;
			picked = new Set();
			pickedFiles = new Set();
			open = {};
		} catch (e) {
			errorToast(e);
		} finally {
			loading = false;
		}
	}
	load();
	get('/profiles').then((r) => {
		profiles = r;
		profile = r.find((p: any) => p.is_default)?.id ?? r[0]?.id ?? null;
	}).catch(errorToast);

	export function reload() {
		load();
	}
	function show(v: 'new' | 'ignored') {
		view = v;
		load();
	}
	function togglePick(f: Folder, e: MouseEvent) {
		picked = pick(picked, folders.map(key), key(f), picked.size ? lastFolder : null, e.shiftKey);
		lastFolder = key(f);
	}
	function toggleFile(id: number, files: any[], e: MouseEvent) {
		pickedFiles = pick(pickedFiles, files.map((x) => x.id), id, pickedFiles.size ? lastFile : null, e.shiftKey);
		lastFile = id;
	}
	const allPicked = $derived(folders.length > 0 && folders.every((f) => picked.has(key(f))));
	function toggleAll() {
		picked = allPicked ? new Set() : new Set(folders.map(key));
	}
	async function expand(f: Folder) {
		const k = key(f);
		if (open[k]) {
			const { [k]: _, ...rest } = open;
			open = rest;
			return;
		}
		try {
			const r = await get(`/files${qs({ state: view, location_id: f.location_id, folder: f.folder, sort: 'path', limit: 500 })}`);
			open = { ...open, [k]: r.items };
		} catch (e) {
			errorToast(e);
		}
	}
	async function act(run: () => Promise<any>) {
		busy = true;
		try {
			await run();
			await load();
			changed();
		} catch (e) {
			errorToast(e);
		} finally {
			busy = false;
		}
	}
	const importNow = () => act(async () => {
		const r = await post('/files/import', { ...body(), profile_id: profile });
		toast(`Imported ${r.imported} ${r.imported === 1 ? 'file' : 'files'}. Nothing changed on the disk yet.`, 'ok');
		imported();
	});
	const ignore = () => act(async () => {
		const r = await post('/files/ignore', body());
		toast(`Ignoring ${r.ignored} ${r.ignored === 1 ? 'file' : 'files'}`, 'ok');
	});
	const restore = () => act(async () => {
		const r = await post('/files/restore', body());
		toast(`${r.restored} ${r.restored === 1 ? 'file is' : 'files are'} back in the list`, 'ok');
	});
	function play(f: any) {
		player.song = { id: String(f.id), title: f.title ?? f.path, artist: f.artist ?? '', thumbnail: null, src: `/api/files/${f.id}/audio` };
	}
</script>

<div class="row head">
	<span class="grow">
		{#if view === 'new'}
			{#if total}<b>{total.toLocaleString()}</b> {total === 1 ? 'file waits' : 'files wait'} for an import.{:else if !loading}Nothing waits for an import.{/if}
		{:else}
			<b>{total.toLocaleString()}</b> ignored {total === 1 ? 'file' : 'files'}.
		{/if}
	</span>
	<button class="ghost sm" onclick={() => show(view === 'new' ? 'ignored' : 'new')}>{view === 'new' ? 'Show ignored files' : 'Back to new files'}</button>
</div>
{#if view === 'new'}
	<p class="hint">Files a scan finds in your locations stay here until you import them. Importing gives them a profile and changes nothing on the disk: the library then shows what doesn't match the profile, and you decide what to fix.</p>
{/if}

{#if count}
	<div class="selbar card">
		<b>{count.toLocaleString()} selected</b>
		<span class="spacer"></span>
		{#if view === 'new'}
			<label class="small profile">Profile
				<select bind:value={profile} aria-label="Profile for the import">
					{#each profiles as p (p.id)}<option value={p.id}>{p.name}</option>{/each}
				</select>
			</label>
			<button class="sm primary" onclick={importNow} disabled={busy || !profile}><FolderInput size={14} /> Import</button>
			<button class="sm" onclick={ignore} disabled={busy}><EyeOff size={14} /> Ignore</button>
		{:else}
			<button class="sm" onclick={restore} disabled={busy}><RotateCcw size={14} /> Bring back</button>
		{/if}
	</div>
{/if}

{#if folders.length}
	<div class="card list">
		<label class="all small"><input type="checkbox" checked={allPicked} onchange={toggleAll} /> Select all folders</label>
		{#each folders as f (key(f))}
			<div class="folder" class:sel={picked.has(key(f))}>
				<input type="checkbox" checked={picked.has(key(f))} onclick={(e) => togglePick(f, e)} aria-label="Select {f.folder || f.location_name}" />
				<button class="ghost open" onclick={() => expand(f)} aria-expanded={!!open[key(f)]}>
					{#if open[key(f)]}<ChevronDown size={15} />{:else}<ChevronRight size={15} />{/if}
					<span class="name ellipsis">{f.folder || '(top level)'}</span>
				</button>
				<span class="spacer"></span>
				{#if f.downloaded}<span class="pill info" title="These files were downloaded by Amplicon before">From Amplicon</span>{/if}
				{#each f.formats as x (x)}<span class="pill">{x.toUpperCase()}</span>{/each}
				<span class="small faint nowrap">{f.location_name} · {f.files} {f.files === 1 ? 'file' : 'files'} · {bytes(f.bytes)}</span>
			</div>
			{#if open[key(f)]}
				<div class="files">
					{#each open[key(f)] as x (x.id)}
						<div class="file small">
							{#if !picked.has(key(f))}<input type="checkbox" checked={pickedFiles.has(x.id)} onclick={(e) => toggleFile(x.id, open[key(f)], e)} aria-label="Select {x.path}" />{/if}
							<button class="ghost sm icon" onclick={() => play(x)} aria-label="Play {x.path}"><Play size={13} /></button>
							<span class="grow ellipsis" title={x.path}>{x.path.split('/').pop()}</span>
							{#if x.title}<span class="faint ellipsis tagged">{[x.artist, x.title].filter(Boolean).join(' – ')}</span>{:else}<span class="faint">no tags</span>{/if}
							{#if x.error}<span class="pill err" title={x.error}>Broken</span>{/if}
						</div>
					{/each}
				</div>
			{/if}
		{/each}
	</div>
	{#if more}<p class="hint">And {more.toLocaleString()} more folders. Import these first, the rest follows.</p>{/if}
{/if}

<style>
	.head {
		margin-bottom: 6px;
	}
	.selbar {
		position: sticky;
		top: 8px;
		z-index: 5;
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 8px;
		padding: 6px 10px;
		margin: 10px 0;
		box-shadow: var(--shadow-lg);
	}
	.profile {
		display: inline-flex;
		align-items: center;
		gap: 6px;
	}
	.profile select {
		width: auto;
		min-width: 130px;
	}
	.list {
		margin-top: 10px;
		overflow: hidden;
	}
	.all {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 8px 12px;
		border-bottom: 1px solid var(--line);
		cursor: pointer;
	}
	.folder {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 6px 8px;
		padding: 6px 12px;
		border-bottom: 1px solid var(--line);
	}
	.folder.sel {
		background: var(--accent-soft);
	}
	.open {
		height: auto;
		min-height: 28px;
		min-width: 0;
		max-width: 100%;
		padding: 0 4px;
		font-weight: 520;
		text-align: left;
	}
	.name {
		min-width: 0;
		word-break: break-word;
	}
	.files {
		padding: 4px 12px 8px 40px;
		border-bottom: 1px solid var(--line);
	}
	.file {
		display: flex;
		align-items: center;
		gap: 8px;
		min-height: 28px;
	}
	.file .grow {
		min-width: 0;
	}
	.tagged {
		max-width: 45%;
	}
	.icon {
		width: 26px;
		height: 26px;
		padding: 0;
		flex: none;
	}
	.nowrap {
		white-space: nowrap;
	}
</style>
