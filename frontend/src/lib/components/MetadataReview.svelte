<script lang="ts">
	import { Sparkles, Upload, TriangleAlert } from '@lucide/svelte';
	import { get, post } from '../api';
	import { errorToast, reviews, toast } from '../state.svelte';
	import Modal from './Modal.svelte';
	import TagForm from './TagForm.svelte';
	import Candidates from './Candidates.svelte';

	const FIELDS = ['title', 'artist', 'albumartist', 'album', 'track', 'track_total', 'disc', 'disc_total', 'year', 'genre', 'label', 'isrc'];

	let open = $state(false);
	let data = $state<any>(null);
	let songs = $state<any[]>([]);
	let current = $state(0);
	let busy = $state(false);
	let searching = $state(false);
	let aiAvailable = $state(false);
	let aiBusy = $state(false);
	let albumId = $state<string | null>(null);

	const song = $derived(songs[current]);
	const multiple = $derived(songs.length > 1);
	const kept = $derived(songs.filter((s) => !s.skip).length);
	const pick = (s: any) => Object.fromEntries(FIELDS.map((k) => [k, s?.[k] ?? '']));

	$effect(() => {
		const id = reviews.job;
		if (id) load(id);
		else open = false;
	});

	async function load(id: number) {
		try {
			const [r, info] = await Promise.all([get(`/jobs/${id}/review`), get('/app/info')]);
			if (r.job.status !== 'review') {
				reviews.job = null;
				return;
			}
			data = r;
			aiAvailable = info.ai_available;
			albumId = r.albums[0]?.id ?? null;
			songs = r.songs.map((s: any) => ({ ...s, fields: pick(s.suggested), skip: false, cover: s.suggested.cover ? { url: s.suggested.cover } : null }));
			current = 0;
			open = true;
		} catch (e) {
			errorToast(e);
			reviews.job = null;
		}
	}

	function close() {
		reviews.job = null;
	}

	let timer: ReturnType<typeof setTimeout>;
	function refreshPath() {
		clearTimeout(timer);
		const s = song;
		timer = setTimeout(async () => {
			try {
				const r = await post(`/jobs/${data.job.id}/review/path`, { item_id: s.item_id, fields: s.fields });
				s.path = r.path;
				s.exists = r.exists;
			} catch {
				/* the path is only a preview */
			}
		}, 250);
	}

	function apply(c: any) {
		for (const k of FIELDS) if (c[k] !== null && c[k] !== undefined && c[k] !== '') song.fields[k] = c[k];
		if (c.cover) {
			song.cover = { url: c.cover };
			if (!song.covers.some((x: any) => x.url === c.cover)) song.covers.push({ source: c.source, url: c.cover });
		}
		refreshPath();
	}

	function useAlbum(a: any) {
		albumId = a.id;
		for (const s of songs) {
			const c = a.songs[s.item_id];
			if (c) for (const k of FIELDS) if (c[k]) s.fields[k] = c[k];
			if (a.cover) s.cover = { url: a.cover };
			if (a.cover && !s.covers.some((x: any) => x.url === a.cover)) s.covers.push({ source: a.source, url: a.cover });
		}
		refreshPath();
	}

	async function search(q: string) {
		searching = true;
		try {
			song.candidates = (await post(`/jobs/${data.job.id}/review/lookup`, { item_id: song.item_id, query: q })).candidates;
		} catch (e) {
			errorToast(e);
		} finally {
			searching = false;
		}
	}

	function upload(e: Event) {
		const file = (e.target as HTMLInputElement).files?.[0];
		if (!file) return;
		const reader = new FileReader();
		reader.onload = () => {
			const url = String(reader.result);
			const targets = data.job.kind === 'album' ? songs : [song];
			for (const s of targets) {
				s.cover = { data: url };
				s.covers = [...s.covers.filter((c: any) => c.source !== 'upload'), { source: 'upload', url }];
			}
		};
		reader.readAsDataURL(file);
	}

	function chooseCover(c: any) {
		const spec = c.source === 'upload' ? { data: c.url } : { url: c.url };
		for (const s of data.job.kind === 'album' ? songs : [song]) s.cover = spec;
	}
	const isCover = (c: any) => (song?.cover?.url ?? song?.cover?.data) === c.url;

	async function runAi() {
		aiBusy = true;
		try {
			const list = songs.filter((s) => !s.skip).map((s) => ({ id: s.item_id, file: `${s.fields.artist} - ${s.fields.title}`, ...pick(s.fields) }));
			const r = await post(`/jobs/${data.job.id}/review/ai`, { songs: list });
			let changed = 0;
			for (const s of songs) {
				const a = r.suggestions[s.item_id];
				if (!a) continue;
				for (const [k, v] of Object.entries(a)) {
					if (s.fields[k] !== v) {
						s.fields[k] = v;
						changed++;
					}
				}
			}
			toast(changed ? `AI changed ${changed} ${changed === 1 ? 'field' : 'fields'}. Check them before you start.` : 'AI found nothing to change', 'ok');
			if (r.errors?.length) toast(r.errors[0], 'error');
			refreshPath();
		} catch (e) {
			errorToast(e);
		} finally {
			aiBusy = false;
		}
	}

	async function confirm() {
		busy = true;
		try {
			await post(`/jobs/${data.job.id}/confirm`, {
				songs: songs.map((s) => ({ item_id: s.item_id, fields: s.fields, skip: s.skip, cover: s.cover }))
			});
			toast(kept ? `Downloading ${data.job.title}` : 'Download cancelled', 'ok');
			close();
		} catch (e) {
			errorToast(e);
		} finally {
			busy = false;
		}
	}

	async function cancel() {
		try {
			await post(`/jobs/${data.job.id}/cancel`);
		} catch (e) {
			errorToast(e);
		}
		close();
	}
</script>

<Modal bind:open title={data ? `Review ${data.job.title}` : 'Review'} width={multiple ? '980px' : '680px'} onclose={close}>
	{#if data && song}
		{#if data.albums.length}
			<div class="section">
				<div class="small faint">Album match</div>
				<div class="row wrap">
					{#each data.albums as a (a.id)}
						<button class="album" class:sel={albumId === a.id} onclick={() => useAlbum(a)}>
							{#if a.cover}<img src={a.cover} alt="" />{/if}
							<span class="ellipsis">{a.title} · {a.artist}{a.year ? ` · ${a.year.slice(0, 4)}` : ''}</span>
						</button>
					{/each}
				</div>
			</div>
		{/if}
		<div class="layout" class:multiple>
			{#if multiple}
				<div class="songs">
					{#each songs as s, i (s.item_id)}
						<div class="song" class:sel={i === current} class:off={s.skip}>
							<input type="checkbox" checked={!s.skip} onchange={() => (s.skip = !s.skip)} aria-label="Download {s.fields.title}" />
							<button class="ghost pick" onclick={() => (current = i)}>
								<span class="grow names ellipsis">
									<span class="ellipsis">{s.fields.title}</span>
									<span class="small faint ellipsis">{s.fields.artist}</span>
								</span>
								{#if s.exists}<span title="Already in this location"><TriangleAlert size={14} color="var(--warn)" /></span>{/if}
								<span class="pill {s.confident ? 'ok' : 'warn'}">{s.confident ? 'Match' : 'Check'}</span>
							</button>
						</div>
					{/each}
				</div>
			{/if}
			<div class="edit">
				<div class="covers">
					{#each song.covers as c (c.url)}
						<button class="cover" class:sel={isCover(c)} onclick={() => chooseCover(c)} title={c.source}>
							<img src={c.url} alt="Cover from {c.source}" />
							<span class="small">{c.source === 'youtube' ? 'YouTube' : c.source === 'deezer' ? 'Deezer' : c.source === 'itunes' ? 'Apple' : 'Yours'}</span>
						</button>
					{/each}
					<label class="cover add">
						<Upload size={18} /><span class="small">Upload</span>
						<input type="file" accept="image/*" onchange={upload} hidden />
					</label>
				</div>
				<TagForm bind:tags={song.fields} oninput={refreshPath} />
				{#if song.path}
					<div class="small path"><span class="faint">Saved as</span> <code>{song.path}</code></div>
				{/if}
				{#if song.exists}
					<div class="small warn"><TriangleAlert size={14} /> This song is already in this location. With "Skip it" in the profile, it won't be downloaded and these tags are dropped.</div>
				{/if}
				<div class="small faint">Other matches</div>
				<Candidates candidates={song.candidates} busy={searching} onpick={apply} onsearch={search} />
			</div>
		</div>
	{/if}
	{#snippet footer()}
		{#if aiAvailable}<button onclick={runAi} disabled={aiBusy}><Sparkles size={15} /> {aiBusy ? 'Asking…' : 'Clean up with AI'}</button>{/if}
		<span class="spacer"></span>
		<button class="ghost danger" onclick={cancel}>Cancel download</button>
		<button onclick={close}>Later</button>
		<button class="primary" onclick={confirm} disabled={busy}>{kept > 1 ? `Download ${kept} songs` : 'Download'}</button>
	{/snippet}
</Modal>

<style>
	.section {
		margin-bottom: 12px;
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.album {
		height: 40px;
		padding: 0 8px 0 4px;
		max-width: 300px;
	}
	.album img {
		width: 32px;
		height: 32px;
		border-radius: 5px;
	}
	.sel.album,
	.cover.sel {
		border-color: var(--accent);
		box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent) 30%, transparent);
	}
	.layout {
		display: grid;
		grid-template-columns: minmax(0, 1fr);
		gap: 16px;
	}
	.layout.multiple {
		grid-template-columns: minmax(0, 300px) minmax(0, 1fr);
	}
	.songs {
		display: flex;
		flex-direction: column;
		gap: 2px;
		max-height: 62vh;
		overflow-y: auto;
	}
	.song {
		display: flex;
		align-items: center;
		gap: 6px;
		padding-left: 6px;
		border-radius: 8px;
	}
	.song.sel {
		background: var(--bg-hover);
	}
	.song.off {
		opacity: 0.5;
	}
	.pick {
		flex: 1;
		min-width: 0;
		height: 46px;
		padding: 0 6px;
		justify-content: flex-start;
		font-weight: 400;
		text-align: left;
	}
	.names {
		display: flex;
		flex-direction: column;
	}
	.edit {
		display: flex;
		flex-direction: column;
		gap: 12px;
		min-width: 0;
	}
	.covers {
		display: flex;
		gap: 8px;
		overflow-x: auto;
		padding: 3px;
	}
	.cover {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: 4px;
		width: 92px;
		height: 112px;
		flex: none;
		padding: 5px;
		border-radius: 10px;
		border: 1px solid var(--line-2);
		background: var(--bg-2);
		cursor: pointer;
		font-weight: 400;
	}
	.cover img {
		width: 80px;
		height: 80px;
		object-fit: cover;
		border-radius: 6px;
	}
	.path {
		word-break: break-all;
	}
	.warn {
		display: flex;
		gap: 6px;
		color: var(--warn);
	}
	@media (max-width: 720px) {
		.layout.multiple {
			grid-template-columns: minmax(0, 1fr);
		}
		.songs {
			max-height: 200px;
		}
	}
</style>
