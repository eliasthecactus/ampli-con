<script lang="ts">
	import '../app.css';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { Search, Download, SlidersHorizontal, HardDrive, CirclePlay, Settings, LogOut, Menu, Sun, Moon, Monitor, Play, Pause, X } from '@lucide/svelte';
	import { get, post, setUnauthorizedHandler } from '#lib/api.ts';
	import { session, live, player, connectEvents, disconnectEvents } from '#lib/state.svelte.ts';
	import Toasts from '#lib/components/Toasts.svelte';

	let { children } = $props();
	let navOpen = $state(false);
	let theme = $state<'system' | 'light' | 'dark'>('system');
	let audio: HTMLAudioElement | undefined = $state();

	const NAV = [
		{ href: '/', label: 'Search', icon: Search },
		{ href: '/downloads', label: 'Downloads', icon: Download },
		{ href: '/profiles', label: 'Profiles', icon: SlidersHorizontal },
		{ href: '/locations', label: 'Locations', icon: HardDrive },
		{ href: '/youtube', label: 'YouTube', icon: CirclePlay },
		{ href: '/settings', label: 'Settings', icon: Settings }
	];

	setUnauthorizedHandler(() => {
		session.user = null;
		disconnectEvents();
	});

	async function init() {
		try {
			const s = await get('/auth/state');
			session.user = s.user;
			session.version = s.version ?? '';
			session.envUser = s.env_user;
		} catch {
			/* server down */
		}
		session.ready = true;
	}
	init();

	$effect(() => {
		if (!session.ready) return;
		const path = page.url.pathname;
		if (!session.user && path !== '/login') goto('/login');
		if (session.user) connectEvents();
	});

	$effect(() => {
		try {
			theme = (localStorage.getItem('amplicon-theme') as any) || 'system';
		} catch {
			/* storage blocked */
		}
	});
	function cycleTheme() {
		theme = theme === 'system' ? 'dark' : theme === 'dark' ? 'light' : 'system';
		try {
			localStorage.setItem('amplicon-theme', theme);
		} catch {
			/* ignore */
		}
	}
	$effect(() => {
		if (theme === 'system') document.documentElement.removeAttribute('data-theme');
		else document.documentElement.setAttribute('data-theme', theme);
	});

	async function logout() {
		await post('/auth/logout').catch(() => {});
		session.user = null;
		disconnectEvents();
		goto('/login');
	}

	$effect(() => {
		if (player.song && audio) {
			audio.src = `/api/stream/${player.song.id}`;
			audio.play().catch(() => {});
		}
	});

	const activeCount = $derived(Object.keys(live.jobs).length);
	function active(href: string) {
		return href === '/' ? page.url.pathname === '/' || /^\/(album|playlist|artist)\//.test(page.url.pathname) : page.url.pathname.startsWith(href);
	}
</script>

{#if !session.ready}
	<div></div>
{:else if !session.user || page.url.pathname === '/login'}
	{@render children()}
{:else}
	<div class="shell">
		<aside class:open={navOpen}>
			<a href="/" class="brand" onclick={() => (navOpen = false)}>
				<img src="/favicon.svg" alt="" width="26" height="26" />
				<span>Amplicon</span>
			</a>
			<nav>
				{#each NAV as n (n.href)}
					<a href={n.href} class:active={active(n.href)} onclick={() => (navOpen = false)}>
						<n.icon size={17} />
						<span>{n.label}</span>
						{#if n.href === '/downloads' && activeCount}<span class="badge">{activeCount}</span>{/if}
					</a>
				{/each}
			</nav>
			<div class="foot">
				<div class="row small faint">
					<span class="dot" class:on={live.connected}></span>
					{live.connected ? 'Live' : 'Reconnecting…'}
					<span class="spacer"></span>
					<button class="ghost icon sm" onclick={cycleTheme} title="Theme: {theme}">
						{#if theme === 'dark'}<Moon size={15} />{:else if theme === 'light'}<Sun size={15} />{:else}<Monitor size={15} />{/if}
					</button>
				</div>
				<div class="row">
					<span class="small muted grow ellipsis">{session.user.username}</span>
					{#if session.version}<span class="small faint version">v{session.version}</span>{/if}
					<button class="ghost icon sm" onclick={logout} title="Sign out"><LogOut size={15} /></button>
				</div>
			</div>
		</aside>
		{#if navOpen}<button class="scrim" aria-label="Close menu" onclick={() => (navOpen = false)}></button>{/if}
		<main class:with-player={player.song}>
			<div class="mobilebar">
				<button class="ghost icon" onclick={() => (navOpen = true)} aria-label="Menu"><Menu size={20} /></button>
				<b>Amplicon</b>
				{#if activeCount}<a href="/downloads" class="pill info">{activeCount} downloading</a>{/if}
			</div>
			<div class="content">{@render children()}</div>
		</main>
	</div>
	{#if player.song}
		<div class="playerbar">
			{#if player.song.thumbnail}<img src={player.song.thumbnail} alt="" />{/if}
			<div class="grow col ellipsis">
				<b class="ellipsis">{player.song.title}</b>
				<span class="small muted ellipsis">{player.song.artist}</span>
			</div>
			<button class="icon" onclick={() => (audio?.paused ? audio?.play() : audio?.pause())} aria-label={player.playing ? 'Pause' : 'Play'}>
				{#if player.playing}<Pause size={17} />{:else}<Play size={17} />{/if}
			</button>
			<button class="ghost icon" onclick={() => { audio?.pause(); player.song = null; }} aria-label="Close player"><X size={17} /></button>
		</div>
	{/if}
	<audio bind:this={audio} onplay={() => (player.playing = true)} onpause={() => (player.playing = false)} onended={() => (player.playing = false)}></audio>
{/if}
<Toasts />

<style>
	.shell {
		display: flex;
		min-height: 100vh;
	}
	aside {
		width: 220px;
		flex-shrink: 0;
		border-right: 1px solid var(--line);
		background: var(--bg-2);
		display: flex;
		flex-direction: column;
		padding: 14px 10px;
		position: sticky;
		top: 0;
		height: 100vh;
	}
	.brand {
		display: flex;
		align-items: center;
		gap: 10px;
		font-weight: 700;
		font-size: 16px;
		padding: 4px 8px 18px;
		letter-spacing: -0.01em;
	}
	nav {
		display: flex;
		flex-direction: column;
		gap: 2px;
		flex: 1;
	}
	nav a {
		display: flex;
		align-items: center;
		gap: 10px;
		height: 36px;
		padding: 0 10px;
		border-radius: 8px;
		color: var(--text-2);
		font-weight: 520;
	}
	nav a:hover {
		background: var(--bg-hover);
		color: var(--text);
	}
	nav a.active {
		background: var(--accent-soft);
		color: var(--accent);
	}
	.badge {
		margin-left: auto;
		background: var(--accent);
		color: #fff;
		border-radius: 99px;
		font-size: 11px;
		font-weight: 700;
		min-width: 18px;
		height: 18px;
		display: grid;
		place-items: center;
		padding: 0 5px;
	}
	.foot {
		display: flex;
		flex-direction: column;
		gap: 6px;
		border-top: 1px solid var(--line);
		padding: 10px 6px 0;
	}
	.version {
		font-variant-numeric: tabular-nums;
	}
	.dot {
		width: 8px;
		height: 8px;
		border-radius: 50%;
		background: var(--warn);
	}
	.dot.on {
		background: var(--ok);
	}
	main {
		flex: 1;
		min-width: 0;
	}
	main.with-player {
		padding-bottom: 72px;
	}
	.content {
		max-width: 1180px;
		margin: 0 auto;
		padding: 26px 28px 60px;
	}
	.mobilebar,
	.scrim {
		display: none;
	}
	.playerbar {
		position: fixed;
		left: 50%;
		transform: translateX(-50%);
		bottom: 12px;
		width: min(560px, calc(100vw - 32px));
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 8px 10px;
		background: var(--bg-2);
		border: 1px solid var(--line);
		border-radius: 14px;
		box-shadow: var(--shadow-lg);
		z-index: 45;
	}
	.playerbar img {
		width: 42px;
		height: 42px;
		border-radius: 7px;
		object-fit: cover;
	}
	@media (max-width: 860px) {
		aside {
			position: fixed;
			left: 0;
			z-index: 40;
			transform: translateX(-100%);
			transition: transform 0.18s;
			box-shadow: var(--shadow-lg);
		}
		aside.open {
			transform: none;
		}
		.scrim {
			display: block;
			position: fixed;
			inset: 0;
			z-index: 30;
			background: rgb(0 0 0 / 0.35);
			border: none;
			border-radius: 0;
			height: auto;
		}
		.mobilebar {
			display: flex;
			align-items: center;
			gap: 8px;
			padding: 8px 12px;
			border-bottom: 1px solid var(--line);
			background: var(--bg-2);
			position: sticky;
			top: 0;
			z-index: 20;
		}
		.mobilebar .pill {
			margin-left: auto;
		}
		.content {
			padding: 18px 16px 50px;
		}
	}
</style>
