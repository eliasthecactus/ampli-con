<script lang="ts">
	import { post } from '../api';
	import { conflictText } from '../format';
	import { errorToast, toast } from '../state.svelte';
	import Modal from './Modal.svelte';

	let { plan = $bindable(null), done }: { plan?: { count: number; blocked?: number; samples: { path: string; target_path: string; location?: string; target_location?: string; conflict?: any }[]; ids?: number[] } | null; done?: () => void } = $props();
	let busy = $state(false);
	let open = $state(false);
	const blocked = $derived(plan?.blocked ?? 0);
	const movable = $derived((plan?.count ?? 0) - blocked);
	$effect(() => {
		open = !!plan?.count;
	});

	async function run() {
		busy = true;
		try {
			const r = await post('/files/rename', plan?.ids ? { ids: plan.ids } : {});
			const failed = r.problems.filter((p: any) => !p.taken);
			toast(`Renamed ${r.renamed} ${r.renamed === 1 ? 'file' : 'files'}`, 'ok');
			if (failed.length) toast(`${failed.length} couldn't be renamed: ${failed[0].reason}`, 'error');
			plan = null;
			done?.();
		} catch (e) {
			errorToast(e);
		} finally {
			busy = false;
		}
	}
</script>

<Modal bind:open title="Move and rename files?" width="640px" onclose={() => (plan = null)}>
	<p>{plan?.count} {plan?.count === 1 ? "file doesn't" : "files don't"} match their profile's name or location.</p>
	<div class="list">
		{#each plan?.samples ?? [] as s (s.path)}
			<div class="small"><code class="faint">{s.path}</code><br /><code class="to">→ {#if s.target_location && s.target_location !== s.location}{s.target_location}: {/if}{s.target_path}</code>{#if s.conflict}<br /><code class="clash">Collides with {conflictText(s.conflict)}</code>{/if}</div>
		{/each}
		{#if (plan?.count ?? 0) > (plan?.samples.length ?? 0)}<div class="small faint">and {(plan?.count ?? 0) - (plan?.samples.length ?? 0)} more</div>{/if}
	</div>
	{#if blocked}<p class="small clash">{blocked} {blocked === 1 ? 'stays as it is because its new name is taken' : 'stay as they are because their new name is taken'}.</p>{/if}
	<p class="hint">Playlist files follow along, and Navidrome or Jellyfin are asked to scan.</p>
	{#snippet footer()}
		<button onclick={() => (plan = null)}>Not now</button>
		<button class="primary" onclick={run} disabled={busy || !movable}>Move {movable} {movable === 1 ? 'file' : 'files'}</button>
	{/snippet}
</Modal>

<style>
	.list {
		display: flex;
		flex-direction: column;
		gap: 8px;
		margin: 12px 0;
		word-break: break-all;
	}
	.to {
		color: var(--ok);
	}
	.clash {
		color: var(--err);
	}
</style>
