<script lang="ts">
	import { Trash2, Smartphone, RefreshCw } from '@lucide/svelte';
	import { del, get, post, put } from '#lib/api.ts';
	import { ago, bytes } from '#lib/format.ts';
	import { errorToast, session, toast } from '#lib/state.svelte.ts';

	let s = $state<any>(null);
	let cache = $state<any>(null);
	let devices = $state<any[]>([]);
	let system = $state<any>(null);
	let pw = $state({ current: '', username: session.user?.username ?? '', next: '' });
	let busy = $state(false);

	async function load() {
		try {
			const r = await get('/settings');
			s = r.settings;
			cache = r.cache;
			[devices, system] = await Promise.all([get('/devices'), get('/system')]);
		} catch (e) {
			errorToast(e);
		}
	}
	load();

	async function save() {
		busy = true;
		try {
			const r = await put('/settings', s);
			s = r.settings;
			cache = r.cache;
			toast('Settings saved', 'ok');
		} catch (e) {
			errorToast(e);
		} finally {
			busy = false;
		}
	}
	async function changePassword() {
		try {
			const r = await post('/auth/change', { current: pw.current, username: pw.username, new: pw.next || null });
			if (session.user) session.user.username = pw.username;
			pw.current = pw.next = '';
			toast(r.env_user ? 'Changed. AMPLICON_PASSWORD in docker compose replaces it on the next restart.' : 'Changed. The apps have to sign in again.', 'ok');
			devices = await get('/devices');
		} catch (e) {
			errorToast(e);
		}
	}
	async function revoke(d: any) {
		if (!confirm(`Sign out ${d.name}?`)) return;
		await del(`/devices/${d.id}`).catch(errorToast);
		devices = devices.filter((x) => x.id !== d.id);
	}
	async function clearCache() {
		cache = (await del('/cache').catch(errorToast))?.cache ?? cache;
	}
	async function updateYtdlp() {
		try {
			system = await post('/system/ytdlp/update');
			toast(system.ytdlp.last_result ?? 'Done', 'ok');
		} catch (e) {
			errorToast(e);
		}
	}
</script>

<div class="page-head"><h1>Settings</h1></div>

{#if s}
	<div class="card pad">
		<h2>Server</h2>
		<label class="field">Name shown in the app<input type="text" bind:value={s.server_name} /></label>
		<label class="field">Downloads at the same time<input type="number" min="1" max="8" bind:value={s.max_parallel_downloads} /></label>
		<label class="field">Keep finished downloads in the list (days, 0 = forever)<input type="number" min="0" bind:value={s.keep_finished_jobs_days} /></label>

		<h2>Playback</h2>
		<label class="field">Stream cache (MB, 0 = off)<input type="number" min="0" step="256" bind:value={s.stream_cache_mb} /></label>
		<div class="row small muted">
			{cache?.files ?? 0} songs, {bytes(cache?.bytes)} of {bytes(cache?.limit)}
			<button class="sm ghost" onclick={clearCache}><Trash2 size={13} /> Empty</button>
		</div>
		<label class="check"><input type="checkbox" bind:checked={s.stream_with_account} /> Always use the YouTube account for playing and downloading</label>
		<p class="hint">Off: Amplicon plays without the account and only uses it when YouTube asks for a sign-in. That keeps the account out of most requests.</p>

		<h2>yt-dlp</h2>
		<div class="row small">
			<span class="grow">Version <b>{system?.ytdlp?.version ?? '–'}</b>{#if system?.ytdlp?.latest && system.ytdlp.latest !== system.ytdlp.version} · {system.ytdlp.latest} available{/if}</span>
			<button class="sm" onclick={updateYtdlp}><RefreshCw size={13} /> Update now</button>
		</div>
		<label class="field">Channel
			<select bind:value={s.ytdlp_channel}>
				<option value="stable">Stable</option>
				<option value="nightly">Nightly (fixes for YouTube changes arrive sooner)</option>
			</select>
		</label>
		<label class="check"><input type="checkbox" bind:checked={s.ytdlp_auto_update} /> Update yt-dlp once a day</label>
		<div class="row end"><button class="primary" onclick={save} disabled={busy}>Save</button></div>
	</div>

	<div class="card pad">
		<h2>Web user</h2>
		{#if session.envUser}<p class="hint">AMPLICON_USERNAME and AMPLICON_PASSWORD are set in docker compose. Change them there; a change here lasts until the next restart.</p>{/if}
		<div class="grid2">
			<label class="field">Username<input type="text" bind:value={pw.username} autocomplete="username" /></label>
			<label class="field">New password<input type="password" bind:value={pw.next} autocomplete="new-password" placeholder="unchanged" /></label>
		</div>
		<label class="field">Current password<input type="password" bind:value={pw.current} autocomplete="current-password" /></label>
		<div class="row end"><button onclick={changePassword} disabled={!pw.current}>Change</button></div>
		<p class="hint">Forgot it? <code>docker exec -it amplicon amplicon reset-password</code> prints a new one.</p>
	</div>

	<div class="card pad">
		<h2>Signed-in apps</h2>
		{#each devices as d (d.id)}
			<div class="row dev">
				<Smartphone size={17} />
				<div class="grow col"><b>{d.name}</b><span class="small faint">since {ago(d.created_at)} · last seen {ago(d.last_seen)}{d.last_ip ? ` from ${d.last_ip}` : ''}</span></div>
				<button class="sm danger" onclick={() => revoke(d)}>Sign out</button>
			</div>
		{:else}
			<p class="muted">No app signed in yet. In Ampli: Settings › Servers › Add › Amplicon, with this server's address and the web user.</p>
		{/each}
	</div>
{/if}

<style>
	.pad {
		padding: 18px;
		display: flex;
		flex-direction: column;
		gap: 12px;
		max-width: 720px;
		margin-bottom: 16px;
	}
	h2 {
		margin-top: 6px;
	}
	.end {
		justify-content: flex-end;
	}
	.dev {
		padding: 6px 0;
		border-bottom: 1px solid var(--line);
	}
	.dev:last-child {
		border-bottom: none;
	}
	.col {
		gap: 2px;
	}
	select {
		width: 100%;
	}
</style>
