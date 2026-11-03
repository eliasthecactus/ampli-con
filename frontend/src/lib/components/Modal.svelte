<script lang="ts">
	import { X } from '@lucide/svelte';
	import type { Snippet } from 'svelte';

	let {
		open = $bindable(false),
		title = '',
		width = '560px',
		side = false,
		onclose,
		children,
		footer
	}: {
		open?: boolean;
		title?: string;
		width?: string;
		side?: boolean;
		onclose?: () => void;
		children: Snippet;
		footer?: Snippet;
	} = $props();

	function close() {
		open = false;
		onclose?.();
	}
	function key(e: KeyboardEvent) {
		if (open && e.key === 'Escape') close();
	}
</script>

<svelte:window onkeydown={key} />

{#if open}
	<!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
	<div class="backdrop" class:side onclick={(e) => e.target === e.currentTarget && close()}>
		<div class="modal" style:--w={width} role="dialog" aria-modal="true" aria-label={title}>
			<header>
				<h2 class="grow ellipsis">{title}</h2>
				<button class="ghost icon sm" onclick={close} aria-label="Close"><X size={18} /></button>
			</header>
			<div class="body">{@render children()}</div>
			{#if footer}<footer>{@render footer()}</footer>{/if}
		</div>
	</div>
{/if}

<style>
	.backdrop {
		position: fixed;
		inset: 0;
		background: rgb(0 0 0 / 0.45);
		display: flex;
		align-items: flex-start;
		justify-content: center;
		padding: 6vh 16px 16px;
		z-index: 50;
		animation: fade 0.12s;
	}
	.backdrop.side {
		justify-content: flex-end;
		padding: 0;
	}
	.modal {
		width: min(var(--w), 100%);
		max-height: 88vh;
		display: flex;
		flex-direction: column;
		background: var(--bg-2);
		border: 1px solid var(--line);
		border-radius: 14px;
		box-shadow: var(--shadow-lg);
		animation: pop 0.14s ease-out;
	}
	.side .modal {
		height: 100vh;
		max-height: 100vh;
		border-radius: 0;
		border-width: 0 0 0 1px;
		animation: slide 0.16s ease-out;
	}
	header {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 14px 16px 10px;
	}
	.body {
		padding: 4px 16px 16px;
		overflow: auto;
		flex: 1;
	}
	footer {
		display: flex;
		gap: 8px;
		justify-content: flex-end;
		padding: 12px 16px;
		border-top: 1px solid var(--line);
		flex-wrap: wrap;
	}
	@keyframes fade {
		from {
			opacity: 0;
		}
	}
	@keyframes pop {
		from {
			transform: translateY(6px) scale(0.99);
			opacity: 0;
		}
	}
	@keyframes slide {
		from {
			transform: translateX(30px);
			opacity: 0;
		}
	}
</style>
