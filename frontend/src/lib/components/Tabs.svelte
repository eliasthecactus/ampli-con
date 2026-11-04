<script lang="ts">
	let { options, value = $bindable(), onchange }: { options: { value: string; label: string }[]; value: string; onchange?: () => void } = $props();
</script>

<div class="tabs" role="tablist">
	{#each options as o (o.value)}
		<button class="ghost" role="tab" aria-selected={value === o.value} class:on={value === o.value} onclick={() => { value = o.value; onchange?.(); }}>{o.label}</button>
	{/each}
</div>

<style>
	.tabs {
		display: inline-flex;
		flex-wrap: wrap;
		gap: 2px;
		padding: 3px;
		border-radius: var(--radius-sm);
		background: var(--bg-3);
	}
	button {
		height: 28px;
		padding: 0 10px;
		font-size: 13px;
	}
	button.on,
	button.on:hover:not(:disabled) {
		background: var(--bg-2);
		box-shadow: var(--shadow);
	}
</style>
