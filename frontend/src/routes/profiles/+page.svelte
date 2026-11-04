<script lang="ts">
	import { Plus, Pencil, Trash2, Star } from '@lucide/svelte';
	import { del, get, post, put } from '#lib/api.ts';
	import { errorToast, toast } from '#lib/state.svelte.ts';
	import Modal from '#lib/components/Modal.svelte';
	import RenameDialog from '#lib/components/RenameDialog.svelte';

	let all = $state<any[]>([]);
	let locations = $state<any[]>([]);
	let options = $state<any>(null);
	let open = $state(false);
	let form = $state<any>({});
	let preview = $state<any>({});
	let busy = $state(false);
	let rename = $state<any>(null);

	async function loadAll() {
		try {
			[all, locations, options] = await Promise.all([get('/profiles'), get('/locations'), get('/profiles/options')]);
		} catch (e) {
			errorToast(e);
		}
	}
	loadAll();

	const REQUIRED = [['title', 'Title'], ['artist', 'Artists'], ['album', 'Album'], ['albumartist', 'Album artist'], ['track', 'Track'], ['year', 'Year'], ['genre', 'Genre'], ['label', 'Label'], ['isrc', 'ISRC']];
	function toggleRequired(k: string) {
		const now = form.required_tags ?? [];
		form.required_tags = now.includes(k) ? now.filter((x: string) => x !== k) : [...now, k];
	}
	function edit(p?: any) {
		form = p
			? { ...p }
			: {
					name: '',
					location_id: locations[0]?.id,
					format: 'm4a',
					quality: '320',
					playlist_layout: 'album',
					on_exists: 'skip',
					write_m3u: true,
					embed_cover: true,
					embed_lyrics: false,
					sponsorblock: true,
					required_tags: ['title', 'artist'],
					is_default: false,
					...options.defaults
				};
		open = true;
		refreshPreview();
	}
	let timer: ReturnType<typeof setTimeout>;
	function refreshPreview() {
		clearTimeout(timer);
		timer = setTimeout(async () => {
			try {
				preview = await post('/profiles/preview', form);
			} catch {
				/* ignore */
			}
		}, 200);
	}
	async function save() {
		busy = true;
		try {
			const saved = form.id ? await put(`/profiles/${form.id}`, form) : await post('/profiles', form);
			toast('Profile saved', 'ok');
			open = false;
			loadAll();
			if (saved?.rename?.count) rename = saved.rename;
		} catch (e) {
			errorToast(e);
		} finally {
			busy = false;
		}
	}
	async function remove(p: any) {
		if (!confirm(`Delete the profile ${p.name}? Files already downloaded stay.`)) return;
		try {
			await del(`/profiles/${p.id}`);
			loadAll();
		} catch (e) {
			errorToast(e);
		}
	}
	async function makeDefault(p: any) {
		try {
			await put(`/profiles/${p.id}`, { ...p, is_default: true });
			loadAll();
		} catch (e) {
			errorToast(e);
		}
	}
</script>

<div class="page-head">
	<h1>Download profiles</h1>
	<span class="spacer"></span>
	<button class="primary" onclick={() => edit()} disabled={!options}><Plus size={16} /> New profile</button>
</div>
<p class="muted intro">
	A profile says what format to save, which location it goes to and how files are named. The default profile is used
	when you tap Download without choosing; the others show up in the app and here to pick from.
</p>

<div class="grid">
	{#each all as p (p.id)}
		<div class="card prof">
			<div class="row">
				<h2 class="grow ellipsis">{p.name}</h2>
				{#if p.is_default}<span class="pill ok">Default</span>{:else}<button class="ghost sm" onclick={() => makeDefault(p)}><Star size={14} /> Make default</button>{/if}
			</div>
			<div class="small muted">{options?.formats?.[p.format] ?? p.format}{p.format === 'mp3' ? ` · ${p.quality}` : ''}</div>
			<div class="small">→ <b>{p.location_name ?? 'no location'}</b> <code class="faint">{p.location_path ?? ''}</code></div>
			<div class="ex">
				<div class="small faint">Album song</div>
				<code class="ellipsis">{p.preview.album_template}</code>
				<div class="small faint">Playlist song ({p.playlist_layout === 'playlist' ? 'own folder' : 'filed like albums'})</div>
				<code class="ellipsis">{p.playlist_layout === 'playlist' ? p.preview.playlist_template : p.preview.album_template}</code>
			</div>
			<div class="small faint">
				{[p.embed_cover && 'cover', p.embed_lyrics && 'lyrics', p.sponsorblock && 'SponsorBlock', p.write_m3u && 'M3U for playlists', p.on_exists === 'skip' ? 'skips existing' : 'replaces existing'].filter(Boolean).join(' · ')}
			</div>
			<div class="row end">
				<button class="sm" onclick={() => edit(p)}><Pencil size={14} /> Edit</button>
				<button class="sm danger" onclick={() => remove(p)} disabled={all.length <= 1}><Trash2 size={14} /></button>
			</div>
		</div>
	{/each}
</div>

<RenameDialog bind:plan={rename} />

<Modal bind:open title={form.id ? `Edit ${form.name}` : 'New profile'} width="640px">
	<div class="form">
		<label class="field">Name<input type="text" bind:value={form.name} placeholder="e.g. Lossy for the car" /></label>
		<div class="grid2">
			<label class="field">Format
				<select bind:value={form.format} onchange={refreshPreview}>
					{#each Object.entries(options?.formats ?? {}) as [k, v] (k)}<option value={k}>{v}</option>{/each}
				</select>
			</label>
			{#if form.format === 'mp3'}
				<label class="field">MP3 quality
					<select bind:value={form.quality}>
						{#each options?.mp3_qualities ?? [] as q (q)}<option value={q}>{q.startsWith('V') ? `VBR ${q}` : `${q} kbit/s`}</option>{/each}
					</select>
				</label>
			{:else}
				<div class="hint">YouTube streams AAC around 128 kbit/s and Opus around 130 kbit/s. M4A and Opus keep that as is; MP3 re-encodes it.</div>
			{/if}
		</div>
		<label class="field">Location
			<select bind:value={form.location_id}>
				{#each locations as l (l.id)}<option value={l.id}>{l.name} ({l.path})</option>{/each}
			</select>
		</label>

		<div class="section-title">File names</div>
		<p class="hint">
			Fields: {(options?.fields ?? []).map((f: string) => `{${f}}`).join(' ')}. <code>{'{track:02}'}</code> pads with zeros,
			<code>[ … ]</code> disappears when a field inside is empty, <code>/</code> makes folders. The extension is added for you.
			When you change a rule, Amplicon offers to rename the existing files; after that they stay in line.
		</p>
		<label class="field">Album songs<input type="text" class="mono" bind:value={form.album_template} oninput={refreshPreview} /></label>
		<code class="pv">{preview.album_template}</code>
		<label class="field">Songs without an album<input type="text" class="mono" bind:value={form.single_template} oninput={refreshPreview} /></label>
		<code class="pv">{preview.single_template}</code>
		<label class="field">Playlists
			<select bind:value={form.playlist_layout}>
				<option value="album">File playlist songs like albums (one copy per song, M3U lists them)</option>
				<option value="playlist">Own folder per playlist</option>
			</select>
		</label>
		{#if form.playlist_layout === 'playlist'}
			<label class="field">Playlist songs<input type="text" class="mono" bind:value={form.playlist_template} oninput={refreshPreview} /></label>
			<code class="pv">{preview.playlist_template}</code>
		{/if}

		<div class="section-title">Extras</div>
		<label class="check"><input type="checkbox" bind:checked={form.embed_cover} /> Embed the album cover (square)</label>
		<label class="check"><input type="checkbox" bind:checked={form.embed_lyrics} /> Embed lyrics when YouTube Music has them</label>
		<label class="check"><input type="checkbox" bind:checked={form.sponsorblock} /> Cut non-music parts (SponsorBlock)</label>
		<label class="check"><input type="checkbox" bind:checked={form.write_m3u} /> Write an M3U playlist file for playlist downloads</label>
		<label class="field">When the file already exists
			<select bind:value={form.on_exists}>
				<option value="skip">Skip it</option>
				<option value="replace">Download again and replace it</option>
			</select>
		</label>
		<label class="check"><input type="checkbox" bind:checked={form.is_default} /> Default profile</label>

		<div class="section-title">Library</div>
		<p class="hint">Files of this profile that lack one of these tags show up as not compliant, like files with the wrong name, outside the profile's location or without a cover when covers are embedded. Formats stay as they are.</p>
		<div class="tagpick">
			{#each REQUIRED as [k, label] (k)}
				<label class="check"><input type="checkbox" disabled={k === 'title' || k === 'artist'} checked={form.required_tags?.includes(k)} onchange={() => toggleRequired(k)} /> {label}</label>
			{/each}
		</div>
	</div>
	{#snippet footer()}
		<button onclick={() => (open = false)}>Cancel</button>
		<button class="primary" onclick={save} disabled={busy || !form.name}>Save</button>
	{/snippet}
</Modal>

<style>
	.tagpick {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(130px, 1fr));
		gap: 4px 12px;
	}
	.intro {
		margin: -6px 0 18px;
		max-width: 720px;
	}
	.grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
		gap: 14px;
	}
	.prof {
		padding: 16px;
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.ex {
		background: var(--bg-3);
		border-radius: 8px;
		padding: 8px 10px;
		display: flex;
		flex-direction: column;
		gap: 2px;
	}
	.end {
		justify-content: flex-end;
		margin-top: 4px;
	}
	.form {
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
	.section-title {
		margin-top: 8px;
	}
	.pv {
		margin-top: -6px;
		color: var(--ok);
		word-break: break-all;
	}
	select {
		width: 100%;
	}
</style>
