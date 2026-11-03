<script lang="ts">
	import { CircleCheck, CircleAlert, LogOut, RefreshCw, Upload } from '@lucide/svelte';
	import { del, get, post, put } from '#lib/api.ts';
	import { ago } from '#lib/format.ts';
	import { errorToast, on, toast } from '#lib/state.svelte.ts';

	let account = $state<any>(null);
	let loaded = $state(false);
	let cookies = $state('');
	let busy = $state(false);
	let fileInput: HTMLInputElement | undefined = $state();

	async function load() {
		try {
			account = (await get('/youtube')).account;
		} catch (e) {
			errorToast(e);
		}
		loaded = true;
	}
	load();
	$effect(() => on('account', () => load()));

	async function connect() {
		busy = true;
		try {
			account = (await put('/youtube', { cookies })).account;
			cookies = '';
			toast(`Signed in as ${account.name}`, 'ok');
		} catch (e) {
			errorToast(e);
		} finally {
			busy = false;
		}
	}
	async function check() {
		busy = true;
		try {
			account = (await post('/youtube/check')).account;
		} catch (e) {
			errorToast(e);
		} finally {
			busy = false;
		}
	}
	async function disconnect() {
		if (!confirm('Sign out of YouTube? Search and public playlists keep working; your own playlists don’t.')) return;
		await del('/youtube').catch(errorToast);
		account = null;
	}
	async function readFile(e: Event) {
		const f = (e.target as HTMLInputElement).files?.[0];
		if (f) cookies = await f.text();
		if (fileInput) fileInput.value = '';
	}
</script>

<div class="page-head"><h1>YouTube</h1></div>

{#if loaded}
	{#if account}
		<div class="card acc">
			{#if account.photo}<img src={account.photo} alt="" />{/if}
			<div class="grow col">
				<b>{account.name ?? 'YouTube account'}</b>
				<span class="small muted">{account.handle ?? ''}</span>
				<span class="small">
					{#if account.status === 'valid'}<span class="ok"><CircleCheck size={13} /> Signed in</span>{:else}<span class="err"><CircleAlert size={13} /> {account.error ?? 'The sign-in stopped working. Paste fresh cookies below.'}</span>{/if}
					<span class="faint"> · checked {ago(account.checked_at)}</span>
				</span>
			</div>
			<button onclick={check} disabled={busy}><RefreshCw size={15} /> Check</button>
			<button class="danger" onclick={disconnect}><LogOut size={15} /> Sign out</button>
		</div>
	{/if}

	<div class="card pad">
		<h2>{account ? 'Replace the sign-in' : 'Sign in with YouTube'}</h2>
		<p class="muted">
			Signed in, Amplicon can list and edit your YouTube Music playlists (in the app and here) and play songs that need
			an account. Search, albums and public playlists work without it.
		</p>
		<p class="muted">
			Google doesn't let self-hosted apps sign in with a password, so Amplicon uses your browser's sign-in cookies:
		</p>
		<ol class="steps">
			<li>Open <a href="https://music.youtube.com" target="_blank" rel="noreferrer">music.youtube.com</a> in a private window and sign in.</li>
			<li>Export the cookies with an extension like “Get cookies.txt LOCALLY”, <b>or</b> open the developer tools, Network tab, reload, click a request to <code>music.youtube.com</code> and copy the <code>cookie</code> request header.</li>
			<li>Close the private window without signing out (signing out ends the cookies).</li>
			<li>Paste the cookies or the header here.</li>
		</ol>
		<textarea class="mono" rows="7" bind:value={cookies} placeholder="# Netscape HTTP Cookie File …   or   SAPISID=…; __Secure-3PAPISID=…"></textarea>
		<div class="row end">
			<input type="file" accept=".txt,text/plain" bind:this={fileInput} onchange={readFile} hidden />
			<button onclick={() => fileInput?.click()}><Upload size={15} /> Load cookies.txt</button>
			<button class="primary" onclick={connect} disabled={busy || !cookies.trim()}>Sign in</button>
		</div>
		<p class="hint">The cookies are stored encrypted in /config and refreshed when YouTube rotates them. Amplicon checks the sign-in every few hours.</p>
	</div>
{/if}

<style>
	.acc {
		display: flex;
		align-items: center;
		gap: 14px;
		padding: 14px 16px;
		margin-bottom: 16px;
		flex-wrap: wrap;
	}
	.acc img {
		width: 48px;
		height: 48px;
		border-radius: 50%;
	}
	.col {
		gap: 2px;
	}
	.ok {
		color: var(--ok);
	}
	.err {
		color: var(--err);
	}
	.ok :global(svg),
	.err :global(svg) {
		vertical-align: -2px;
	}
	.pad {
		padding: 18px;
		display: flex;
		flex-direction: column;
		gap: 10px;
		max-width: 820px;
	}
	.steps {
		margin: 0;
		padding-left: 20px;
		display: flex;
		flex-direction: column;
		gap: 6px;
		color: var(--text-2);
	}
	.steps a {
		color: var(--accent);
	}
	textarea {
		width: 100%;
	}
	.end {
		justify-content: flex-end;
	}
</style>
