<script lang="ts">
	import { Download, ChevronDown, Check } from '@lucide/svelte';
	import { get, post } from '../api';
	import { errorToast, toast } from '../state.svelte';

	let {
		kind,
		id,
		title,
		subtitle = null,
		thumbnail = null,
		label = 'Download',
		small = false
	}: { kind: 'song' | 'album' | 'playlist'; id: string; title: string; subtitle?: string | null; thumbnail?: string | null; label?: string; small?: boolean } = $props();

	let open = $state(false);
	let profiles = $state<any[]>([]);
	let busy = $state(false);
	let sent = $state(false);

	async function loadProfiles() {
		if (!profiles.length) profiles = (await get('/app/info')).profiles;
	}
	async function start(profileId?: number) {
		open = false;
		busy = true;
		try {
			const j = await post('/downloads', { kind, id, title, subtitle, thumbnail, profile_id: profileId ?? null });
			toast(`${j.title} is in the download queue (${j.profile_name})`, 'ok');
			sent = true;
		} catch (e) {
			errorToast(e);
		} finally {
			busy = false;
		}
	}
	async function toggle() {
		try {
			await loadProfiles();
			open = !open;
		} catch (e) {
			errorToast(e);
		}
	}
</script>

<div class="dl">
	<button class:sm={small} class:primary={!small} onclick={() => start()} disabled={busy} title="Download with the default profile">
		{#if sent}<Check size={15} />{:else}<Download size={15} />{/if}
		{#if label}<span>{label}</span>{/if}
	</button>
	<button class="more" class:sm={small} class:primary={!small} onclick={toggle} disabled={busy} aria-label="Pick a profile"><ChevronDown size={14} /></button>
	{#if open}
		<!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
		<div class="scrim" role="presentation" onclick={() => (open = false)}></div>
		<div class="menu card">
			<div class="small faint pad">Download with profile</div>
			{#each profiles as p (p.id)}
				<button class="ghost item" onclick={() => start(p.id)}>
					<span class="grow ellipsis">{p.name}</span>
					<span class="small faint">{p.format} · {p.location}</span>
				</button>
			{/each}
		</div>
	{/if}
</div>

<style>
	.dl {
		position: relative;
		display: inline-flex;
	}
	.dl > button:first-child {
		border-top-right-radius: 0;
		border-bottom-right-radius: 0;
	}
	.more {
		border-top-left-radius: 0;
		border-bottom-left-radius: 0;
		border-left: 1px solid color-mix(in srgb, currentColor 25%, transparent);
		width: 30px;
		padding: 0;
	}
	.more.sm {
		width: 24px;
	}
	.scrim {
		position: fixed;
		inset: 0;
		z-index: 20;
	}
	.menu {
		position: absolute;
		right: 0;
		top: calc(100% + 4px);
		z-index: 21;
		min-width: 260px;
		padding: 4px;
		box-shadow: var(--shadow-lg);
	}
	.pad {
		padding: 6px 8px;
	}
	.item {
		width: 100%;
		justify-content: flex-start;
		gap: 10px;
		font-weight: 480;
	}
</style>
