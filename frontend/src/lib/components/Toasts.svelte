<script lang="ts">
	import { toasts } from '../state.svelte';
</script>

<div class="toasts">
	{#each toasts.list as t (t.id)}
		<div class="toast {t.kind}">{t.text}</div>
	{/each}
</div>

<style>
	.toasts {
		position: fixed;
		bottom: 16px;
		right: 16px;
		display: flex;
		flex-direction: column;
		gap: 8px;
		z-index: 100;
		max-width: min(420px, calc(100vw - 32px));
	}
	.toast {
		padding: 10px 14px;
		border-radius: 10px;
		background: var(--text);
		color: var(--bg);
		box-shadow: var(--shadow-lg);
		font-weight: 520;
		animation: up 0.16s ease-out;
		white-space: pre-wrap;
	}
	.toast.error {
		background: var(--err);
		color: #fff;
	}
	.toast.ok {
		background: var(--ok);
		color: #fff;
	}
	@keyframes up {
		from {
			transform: translateY(8px);
			opacity: 0;
		}
	}
</style>
