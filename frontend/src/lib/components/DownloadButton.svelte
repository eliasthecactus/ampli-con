<script lang="ts">
	import { Download, ChevronDown, Check } from '@lucide/svelte';
	import { get, post } from '../api';
	import { errorToast, reviewWhenReady, reviews, toast } from '../state.svelte';

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
	let locations = $state<any[]>([]);
	let busy = $state(false);
	let sent = $state(false);
	let box: HTMLDivElement;
	// The menu is fixed to the viewport, so a scrolling shelf or card around the button can't cut it off.
	let menuStyle = $state('');
	let menu = $state<HTMLDivElement>();

	$effect(() => {
		if (!open) return;
		const close = (e: Event) => {
			if (!(e.target instanceof Node && menu?.contains(e.target))) open = false;
		};
		window.addEventListener('scroll', close, true);
		window.addEventListener('resize', close);
		return () => {
			window.removeEventListener('scroll', close, true);
			window.removeEventListener('resize', close);
		};
	});

	function place() {
		const r = box.getBoundingClientRect();
		const width = 260;
		const left = Math.max(8, r.left + width <= window.innerWidth - 8 ? r.left : r.right - width);
		const below = window.innerHeight - r.bottom;
		menuStyle =
			below < 240 && r.top > below
				? `left:${left}px;bottom:${window.innerHeight - r.top + 4}px;max-height:${r.top - 12}px`
				: `left:${left}px;top:${r.bottom + 4}px;max-height:${below - 12}px`;
	}

	async function loadProfiles() {
		if (!profiles.length) {
			const info = await get('/app/info');
			profiles = info.profiles;
			locations = info.locations;
		}
	}
	async function start(profileId?: number, locationId?: number) {
		open = false;
		busy = true;
		try {
			const j = await post('/downloads', { kind, id, title, subtitle, thumbnail, profile_id: profileId ?? null, location_id: locationId ?? null, review: true });
			if (j.status === 'review') reviews.job ??= j.id;
			else if (j.review) {
				reviewWhenReady(j.id);
				toast(`Looking up ${j.title}…`);
			} else toast(`${j.title} is in the download queue (${j.profile_name} → ${j.location_name})`, 'ok');
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
			if (!open) place();
			open = !open;
		} catch (e) {
			errorToast(e);
		}
	}
</script>

<div class="dl" bind:this={box}>
	<button class:sm={small} class:primary={!small} onclick={() => start()} disabled={busy} title="Download with the default profile">
		{#if sent}<Check size={15} />{:else}<Download size={15} />{/if}
		{#if label}<span>{label}</span>{/if}
	</button>
	<button class="more" class:sm={small} class:primary={!small} onclick={toggle} disabled={busy} aria-label="Pick a profile"><ChevronDown size={14} /></button>
	{#if open}
		<!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
		<div class="scrim" role="presentation" onclick={() => (open = false)}></div>
		<div class="menu card" style={menuStyle} bind:this={menu}>
			<div class="small faint pad">Download with profile</div>
			{#each profiles as p (p.id)}
				<button class="ghost item" onclick={() => start(p.id)}>
					<span class="grow ellipsis">{p.name}</span>
					<span class="small faint">{p.format} · {p.location}</span>
				</button>
			{/each}
			{#if locations.length > 1}
				<div class="small faint pad sep">Default profile, saved to</div>
				{#each locations as l (l.id)}
					<button class="ghost item" onclick={() => start(undefined, l.id)} disabled={!l.online}>
						<span class="grow ellipsis">{l.name}</span>
						{#if !l.online}<span class="small faint">not mounted</span>{/if}
					</button>
				{/each}
			{/if}
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
		position: fixed;
		z-index: 21;
		width: 260px;
		overflow-y: auto;
		padding: 4px;
		box-shadow: var(--shadow-lg);
	}
	.pad {
		padding: 6px 8px;
	}
	.sep {
		border-top: 1px solid var(--line);
		margin-top: 4px;
	}
	.item {
		width: 100%;
		justify-content: flex-start;
		gap: 10px;
		font-weight: 480;
	}
</style>
