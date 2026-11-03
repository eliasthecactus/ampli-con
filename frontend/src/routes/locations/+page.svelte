<script lang="ts">
	import { Plus, Pencil, Trash2, RefreshCw, HardDrive } from '@lucide/svelte';
	import { del, get, post, put } from '#lib/api.ts';
	import { bytes } from '#lib/format.ts';
	import { errorToast, toast } from '#lib/state.svelte.ts';
	import Modal from '#lib/components/Modal.svelte';
	import FolderPicker from '#lib/components/FolderPicker.svelte';

	let list = $state<any[]>([]);
	let open = $state(false);
	let form = $state<any>({});
	let busy = $state(false);

	async function load() {
		try {
			list = await get('/locations');
		} catch (e) {
			errorToast(e);
		}
	}
	load();

	function edit(l?: any) {
		form = l ? { ...l, scan_secret: '' } : { name: '', path: '', scan_kind: 'none', scan_url: '', scan_user: '', scan_secret: '' };
		open = true;
	}
	async function save() {
		busy = true;
		try {
			if (form.id) await put(`/locations/${form.id}`, form);
			else await post('/locations', form);
			toast('Location saved', 'ok');
			open = false;
			load();
		} catch (e) {
			errorToast(e);
		} finally {
			busy = false;
		}
	}
	async function remove(l: any) {
		if (!confirm(`Remove the location ${l.name}? The files in ${l.path} stay.`)) return;
		try {
			await del(`/locations/${l.id}`);
			load();
		} catch (e) {
			errorToast(e);
		}
	}
	async function scan(l: any) {
		try {
			await post(`/locations/${l.id}/scan`);
			toast(`Asked ${l.scan_kind === 'navidrome' ? 'Navidrome' : 'Jellyfin'} to scan`, 'ok');
		} catch (e) {
			errorToast(e);
		}
	}
</script>

<div class="page-head">
	<h1>Locations</h1>
	<span class="spacer"></span>
	<button class="primary" onclick={() => edit()}><Plus size={16} /> New location</button>
</div>
<p class="muted intro">
	Folders downloads go to. Mount them as volumes in docker compose (for example your Navidrome or Jellyfin music
	folder), then add them here. A location can ask its music server to scan after each download, so new songs show
	up in Ampli right away.
</p>

<div class="card">
	{#each list as l (l.id)}
		<div class="loc">
			<HardDrive size={20} />
			<div class="grow col ellipsis">
				<b>{l.name}</b>
				<code class="small faint ellipsis">{l.path}</code>
				<span class="small muted">
					{#if !l.online}<span class="pill err">Not mounted</span>{:else if !l.writable}<span class="pill warn">Read-only</span>{:else}{bytes(l.free_bytes)} free{/if}
					{#if l.scan_kind !== 'none'} · scans {l.scan_kind === 'navidrome' ? 'Navidrome' : 'Jellyfin'} at {l.scan_url}{/if}
				</span>
			</div>
			{#if l.scan_kind !== 'none'}<button class="sm ghost" onclick={() => scan(l)} title="Scan now"><RefreshCw size={14} /></button>{/if}
			<button class="sm" onclick={() => edit(l)}><Pencil size={14} /> Edit</button>
			<button class="sm danger" onclick={() => remove(l)} disabled={list.length <= 1}><Trash2 size={14} /></button>
		</div>
	{/each}
</div>

<Modal bind:open title={form.id ? `Edit ${form.name}` : 'New location'} width="560px">
	<div class="form">
		<label class="field">Name<input type="text" bind:value={form.name} placeholder="e.g. Music library" /></label>
		<div class="field">Folder<FolderPicker bind:value={form.path} placeholder="/music" /></div>
		<label class="field">After a download, scan
			<select bind:value={form.scan_kind}>
				<option value="none">Nothing</option>
				<option value="navidrome">Navidrome (or another Subsonic server)</option>
				<option value="jellyfin">Jellyfin</option>
			</select>
		</label>
		{#if form.scan_kind !== 'none'}
			<label class="field">Server address<input type="url" bind:value={form.scan_url} placeholder={form.scan_kind === 'navidrome' ? 'http://navidrome:4533' : 'http://jellyfin:8096'} /></label>
			{#if form.scan_kind === 'navidrome'}
				<div class="grid2">
					<label class="field">User (admin)<input type="text" bind:value={form.scan_user} autocomplete="off" /></label>
					<label class="field">Password<input type="password" bind:value={form.scan_secret} autocomplete="new-password" placeholder={form.has_scan_secret ? 'unchanged' : ''} /></label>
				</div>
			{:else}
				<label class="field">API key<input type="password" bind:value={form.scan_secret} autocomplete="off" placeholder={form.has_scan_secret ? 'unchanged' : 'Dashboard › API Keys'} /></label>
			{/if}
			<p class="hint">Stored encrypted. Use the address the Amplicon container can reach (often the container name).</p>
		{/if}
	</div>
	{#snippet footer()}
		<button onclick={() => (open = false)}>Cancel</button>
		<button class="primary" onclick={save} disabled={busy || !form.name || !form.path}>Save</button>
	{/snippet}
</Modal>

<style>
	.intro {
		margin: -6px 0 18px;
		max-width: 720px;
	}
	.loc {
		display: flex;
		align-items: center;
		gap: 12px;
		padding: 12px 14px;
		border-bottom: 1px solid var(--line);
		color: var(--text-2);
	}
	.loc:last-child {
		border-bottom: none;
	}
	.loc b {
		color: var(--text);
	}
	.col {
		gap: 2px;
	}
	.form {
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
	select {
		width: 100%;
	}
</style>
