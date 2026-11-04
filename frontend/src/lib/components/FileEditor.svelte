<script lang="ts">
	import { tick } from 'svelte';
	import { Play, Sparkles, Trash2, Upload } from '@lucide/svelte';
	import { get, post } from '../api';
	import { bytes, duration, shouldBe } from '../format';
	import { errorToast, player, toast } from '../state.svelte';
	import Modal from './Modal.svelte';
	import TagForm from './TagForm.svelte';
	import Candidates from './Candidates.svelte';

	let { id = $bindable(null), done }: { id?: number | null; done: () => void } = $props();

	let open = $state(false);
	let file = $state<any>(null);
	let tags = $state<Record<string, any>>({});
	let original = '';
	let candidates = $state<any[]>([]);
	let extraCovers = $state<any[]>([]);
	let searching = $state(false);
	let looked = $state(false);
	let cover = $state<any>(null);
	let removeCover = $state(false);
	let busy = $state(false);
	let aiBusy = $state(false);
	let stamp = $state(0);
	let matches = $state<HTMLElement>();

	$effect(() => {
		if (id) load(id);
		else open = false;
	});

	async function load(fid: number) {
		try {
			file = await get(`/files/${fid}`);
			tags = Object.fromEntries(Object.entries(file.tags).map(([k, v]) => [k, v ?? '']));
			original = JSON.stringify(tags);
			candidates = [];
			extraCovers = [];
			looked = false;
			cover = null;
			removeCover = false;
			stamp = Date.now();
			open = true;
		} catch (e) {
			errorToast(e);
			id = null;
		}
	}
	async function lookup(q = '') {
		searching = true;
		try {
			const r = await post(`/files/${id}/lookup`, { query: q });
			candidates = r.candidates;
			extraCovers = r.covers;
			looked = true;
			await tick();
			matches?.scrollIntoView({ behavior: 'smooth', block: 'start' });
		} catch (e) {
			errorToast(e);
		} finally {
			searching = false;
		}
	}
	function apply(c: any) {
		for (const k of ['title', 'artist', 'albumartist', 'album', 'track', 'track_total', 'disc', 'disc_total', 'year', 'genre', 'label', 'isrc']) {
			if (c[k] !== null && c[k] !== undefined && c[k] !== '') tags[k] = c[k];
		}
		if (c.cover) {
			cover = { url: c.cover };
			removeCover = false;
		}
	}
	function upload(e: Event) {
		const f = (e.target as HTMLInputElement).files?.[0];
		if (!f) return;
		const reader = new FileReader();
		reader.onload = () => {
			cover = { data: String(reader.result) };
			removeCover = false;
		};
		reader.readAsDataURL(f);
	}
	async function ai() {
		aiBusy = true;
		try {
			const r = await post('/files/ai', { ids: [id], fields: ['title', 'artist'] });
			if (r.errors?.length) toast(r.errors[0], 'error');
			const p = r.proposals[0];
			for (const k of p.changed) tags[k] = p.after[k] ?? '';
			toast(p.changed.length ? 'Suggestions are in the form. Save to keep them.' : 'AI found nothing to change', 'ok');
		} catch (e) {
			errorToast(e);
		} finally {
			aiBusy = false;
		}
	}
	async function save() {
		busy = true;
		try {
			const before = JSON.parse(original);
			const fields = Object.fromEntries(Object.entries(tags).filter(([k, v]) => String(v ?? '') !== String(before[k] ?? '')));
			const r = await post('/files/apply', { changes: [{ id, fields, cover, remove_cover: removeCover }] });
			if (r.problems.length) throw new Error(r.problems[0].reason);
			toast(r.renamed ? 'Saved and renamed' : 'Saved', 'ok');
			id = null;
			done();
		} catch (e) {
			errorToast(e);
		} finally {
			busy = false;
		}
	}
	function play() {
		player.song = { id: String(file.id), title: tags.title || file.path, artist: tags.artist, thumbnail: file.has_cover ? `/api/files/${file.id}/cover?size=96` : null, src: `/api/files/${file.id}/audio` };
	}
	const shown = $derived(removeCover ? null : (cover?.data ?? cover?.url ?? (file?.has_cover ? `/api/files/${file.id}/cover?size=320&t=${stamp}` : null)));
</script>

<Modal bind:open side width="620px" title={tags.title || file?.path || 'File'} onclose={() => (id = null)}>
	{#if file?.deleted_at}
		<p>This file was deleted on {new Date(file.deleted_at * 1000).toLocaleDateString()}, so it can't be edited or played. If it comes back, the next scan brings it back here.</p>
		<code class="small path">{file.path}</code>
	{:else if file}
		<div class="col gap">
			<div class="top">
				<div class="art">{#if shown}<img src={shown} alt="Cover" />{:else}<span class="faint small">No cover</span>{/if}</div>
				<div class="grow col">
					<code class="small path">{file.path}</code>
					<span class="small faint">{file.location_name} · {file.format?.toUpperCase()}{file.bitrate ? ` · ${file.bitrate} kbit/s` : ''} · {duration(file.duration)} · {bytes(file.size)}</span>
					{#if shouldBe(file)}<span class="small warn">Should be: <code>{shouldBe(file)}</code></span>{/if}
					{#if file.error}<span class="small err">{file.error}</span>{/if}
					<div class="row wrap">
						<button class="sm" onclick={play}><Play size={13} /> Play</button>
						<label class="btn sm upload"><Upload size={13} /> Cover<input type="file" accept="image/*" onchange={upload} hidden /></label>
						{#if shown}<button class="sm ghost danger" onclick={() => { removeCover = true; cover = null; }}><Trash2 size={13} /> Cover</button>{/if}
					</div>
					{#each extraCovers as c (c.url)}
						<button class="sm ghost" onclick={() => (cover = { url: c.url })}>Use the large cover from Apple</button>
					{/each}
				</div>
			</div>
			<TagForm bind:tags long />
			<div class="row">
				<button onclick={() => lookup()} disabled={searching}>{searching ? 'Searching…' : 'Find matches'}</button>
				<button onclick={ai} disabled={aiBusy}><Sparkles size={15} /> {aiBusy ? 'Asking…' : 'Clean up with AI'}</button>
			</div>
			{#if looked}<div bind:this={matches}><Candidates {candidates} busy={searching} onpick={apply} onsearch={lookup} /></div>{/if}
		</div>
	{/if}
	{#snippet footer()}
		<button onclick={() => (id = null)}>{file?.deleted_at ? 'Close' : 'Cancel'}</button>
		{#if !file?.deleted_at}<button class="primary" onclick={save} disabled={busy}>Save</button>{/if}
	{/snippet}
</Modal>

<style>
	.gap {
		gap: 14px;
	}
	.top {
		display: flex;
		gap: 14px;
	}
	.art {
		width: 120px;
		height: 120px;
		flex: none;
		display: grid;
		place-items: center;
		border-radius: 10px;
		background: var(--bg-3);
		overflow: hidden;
	}
	.art img {
		width: 100%;
		height: 100%;
		object-fit: cover;
	}
	.path {
		word-break: break-all;
	}
	.upload {
		height: 28px;
		padding: 0 9px;
		font-size: 13px;
	}
	.warn {
		color: var(--warn);
		word-break: break-all;
	}
	.err {
		color: var(--err);
	}
</style>
