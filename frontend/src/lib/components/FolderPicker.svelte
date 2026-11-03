<script lang="ts">
	import { Folder, FolderOpen, CornerLeftUp } from '@lucide/svelte';
	import { get, qs } from '../api';
	import Modal from './Modal.svelte';
	import { errorToast } from '../state.svelte';

	let { value = $bindable(''), placeholder = '/downloads/…' }: { value?: string; placeholder?: string } = $props();
	let open = $state(false);
	let cur = $state<{ path: string | null; parent: string | null; dirs: string[]; writable: boolean; roots: string[] } | null>(null);
	let newName = $state('');

	async function load(path?: string) {
		try {
			cur = await get('/fs' + qs({ path }));
		} catch (e) {
			errorToast(e);
			if (path) load();
		}
	}
	function browse() {
		open = true;
		load(value || undefined);
	}
	function choose() {
		if (!cur) return;
		if (!cur.path) return;
		value = newName.trim() ? `${cur.path.replace(/\/$/, '')}/${newName.trim()}` : cur.path;
		newName = '';
		open = false;
	}
</script>

<div class="row">
	<input class="mono" bind:value {placeholder} />
	<button type="button" class="icon" onclick={browse} title="Browse folders"><FolderOpen size={16} /></button>
</div>

<Modal bind:open title="Choose folder" width="520px">
	{#if cur}
		<div class="row path">
			<button class="icon sm ghost" disabled={!cur.path} onclick={() => load(cur?.parent ?? undefined)} title="Up"
				><CornerLeftUp size={16} /></button
			>
			<code class="grow ellipsis">{cur.path ?? 'Mounted volumes'}</code>
			{#if !cur.writable}<span class="pill warn">read-only</span>{/if}
		</div>
		<div class="list">
			{#each cur.dirs as d (d)}
				<button class="ghost dir" onclick={() => load(cur!.path ? `${cur!.path.replace(/\/$/, '')}/${d}` : d)}>
					<Folder size={15} /> <span class="ellipsis">{d}</span>
				</button>
			{:else}
				<div class="faint small pad">{cur.path ? 'No sub folders' : 'No volumes mounted. Add one in docker compose.'}</div>
			{/each}
		</div>
		<label class="field" style="margin-top: 12px">
			New sub folder (optional)
			<input bind:value={newName} placeholder="e.g. Chill" />
		</label>
		<p class="hint" style="margin-top: 8px">
			Paths are inside the container. Mount extra host folders in docker compose to make them show up here.
		</p>
	{/if}
	{#snippet footer()}
		<button onclick={() => (open = false)}>Cancel</button>
		<button class="primary" onclick={choose} disabled={!cur?.path}>Use {newName.trim() ? 'new folder' : 'this folder'}</button>
	{/snippet}
</Modal>

<style>
	.path {
		padding: 6px 0 8px;
	}
	.list {
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		max-height: 300px;
		overflow: auto;
		padding: 4px;
	}
	.dir {
		width: 100%;
		justify-content: flex-start;
		height: 30px;
		font-weight: 450;
	}
</style>
