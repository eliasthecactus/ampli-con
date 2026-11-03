<script lang="ts">
	import { goto } from '$app/navigation';
	import { post } from '#lib/api.ts';
	import { session } from '#lib/state.svelte.ts';

	let username = $state('admin');
	let password = $state('');
	let error = $state('');
	let busy = $state(false);

	async function submit(e: Event) {
		e.preventDefault();
		busy = true;
		error = '';
		try {
			const r = await post('/auth/login', { username, password });
			session.user = r.user;
			goto('/');
		} catch (err) {
			error = err instanceof Error ? err.message : String(err);
		} finally {
			busy = false;
		}
	}
</script>

<div class="wrap">
	<form class="card" onsubmit={submit}>
		<div class="brand"><img src="/favicon.svg" alt="" width="40" height="40" /><h1>Amplicon</h1></div>
		<p class="muted">Sign in to search YouTube Music, download and manage profiles.</p>
		<label class="field">Username<input type="text" bind:value={username} autocomplete="username" required /></label>
		<label class="field">Password<input type="password" bind:value={password} autocomplete="current-password" required /></label>
		{#if error}<div class="error">{error}</div>{/if}
		<button class="primary" type="submit" disabled={busy}>Sign in</button>
		<p class="hint">
			First start? The password is in the container log (<code>docker logs amplicon</code>). Forgot it? Run
			<code>docker exec -it amplicon amplicon reset-password</code>.
		</p>
	</form>
</div>

<style>
	.wrap {
		min-height: 100vh;
		display: grid;
		place-items: center;
		padding: 16px;
	}
	form {
		width: min(400px, 100%);
		padding: 28px;
		display: flex;
		flex-direction: column;
		gap: 14px;
	}
	.brand {
		display: flex;
		align-items: center;
		gap: 12px;
	}
	.error {
		color: var(--err);
		background: var(--err-soft);
		padding: 8px 10px;
		border-radius: 8px;
		font-size: 13px;
	}
	button {
		height: 38px;
	}
	code {
		font-size: 11.5px;
	}
</style>
