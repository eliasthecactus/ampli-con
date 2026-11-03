<script lang="ts">
	let { counts, height = 6 }: { counts: Record<string, number>; height?: number } = $props();
	const ORDER: [string, string][] = [
		['done', 'var(--ok)'],
		['downloading', 'var(--info)'],
		['queued', 'color-mix(in srgb, var(--info) 50%, transparent)'],
		['pending', 'var(--line-2)'],
		['missing', 'var(--warn)'],
		['failed', 'var(--err)'],
		['unavailable', 'var(--text-3)']
	];
	const total = $derived(ORDER.reduce((a, [k]) => a + (counts?.[k] || 0), 0));
</script>

<div class="bar" style:height="{height}px" title={ORDER.filter(([k]) => counts?.[k]).map(([k]) => `${k}: ${counts[k]}`).join(' · ')}>
	{#if total}
		{#each ORDER as [k, color] (k)}
			{#if counts?.[k]}<span style:width="{(100 * counts[k]) / total}%" style:background={color}></span>{/if}
		{/each}
	{/if}
</div>

<style>
	.bar {
		display: flex;
		width: 100%;
		border-radius: 99px;
		overflow: hidden;
		background: var(--bg-3);
	}
	span {
		height: 100%;
		transition: width 0.3s;
	}
</style>
