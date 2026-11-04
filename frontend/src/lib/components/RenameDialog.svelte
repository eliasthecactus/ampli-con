<script lang="ts">
	import { post } from '../api';
	import { errorToast, toast } from '../state.svelte';
	import Modal from './Modal.svelte';

	let { plan = $bindable(null), done }: { plan?: { count: number; samples: { path: string; target_path: string }[] } | null; done?: () => void } = $props();
	let busy = $state(false);
	let open = $state(false);
	$effect(() => {
		open = !!plan?.count;
	});

	async function run() {
		busy = true;
		try {
			const r = await post('/files/rename', {});
			toast(`Renamed ${r.renamed} ${r.renamed === 1 ? 'file' : 'files'}`, 'ok');
			if (r.problems.length) toast(`${r.problems.length} couldn't be renamed: ${r.problems[0].reason}`, 'error');
			plan = null;
			done?.();
		} catch (e) {
			errorToast(e);
		} finally {
			busy = false;
		}
	}
</script>

<Modal bind:open title="Rename existing files?" width="640px" onclose={() => (plan = null)}>
	<p>{plan?.count} {plan?.count === 1 ? "file doesn't" : "files don't"} match the file name rules anymore.</p>
	<div class="list">
		{#each plan?.samples ?? [] as s (s.path)}
			<div class="small"><code class="faint">{s.path}</code><br /><code class="to">→ {s.target_path}</code></div>
		{/each}
		{#if (plan?.count ?? 0) > (plan?.samples.length ?? 0)}<div class="small faint">and {(plan?.count ?? 0) - (plan?.samples.length ?? 0)} more</div>{/if}
	</div>
	<p class="hint">Playlist files follow along, and Navidrome or Jellyfin are asked to scan. A file is skipped if the new name is already taken.</p>
	{#snippet footer()}
		<button onclick={() => (plan = null)}>Not now</button>
		<button class="primary" onclick={run} disabled={busy}>Rename {plan?.count} {plan?.count === 1 ? 'file' : 'files'}</button>
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
</style>
