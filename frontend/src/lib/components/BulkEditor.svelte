<script lang="ts">
	import { Sparkles } from '@lucide/svelte';
	import { get, post } from '../api';
	import { errorToast, toast } from '../state.svelte';
	import Modal from './Modal.svelte';
	import Proposals from './Proposals.svelte';
	import Tabs from './Tabs.svelte';

	let { open = $bindable(false), ids, start = 'set', done }: { open?: boolean; ids: number[]; start?: string; done: () => void } = $props();

	const TEXT = [['title', 'Title'], ['artist', 'Artists'], ['album', 'Album'], ['albumartist', 'Album artist'], ['year', 'Year'], ['genre', 'Genre'], ['label', 'Label']];
	const TABS = [
		{ value: 'set', label: 'Set' },
		{ value: 'replace', label: 'Find & replace' },
		{ value: 'tidy', label: 'Tidy up' },
		{ value: 'name', label: 'From file name' },
		{ value: 'lookup', label: 'Look up' },
		{ value: 'ai', label: 'AI' }
	];
	let tab = $state('set');
	let proposals = $state<any[] | null>(null);
	let busy = $state(false);
	let values = $state<Record<string, string>>({});
	let clear = $state<Record<string, boolean>>({});
	let replace = $state({ field: 'title', find: '', with: '', regex: false, match_case: false });
	let tidy = $state({ noise: true, case_field: '', mode: 'title', number: false, from: 1 });
	let pattern = $state('{artist} - {title}');
	let instruction = $state('');
	let fields = $state<Record<string, boolean>>({ title: true, artist: true });
	let aiKey = $state(false);
	$effect(() => {
		if (open) {
			tab = start;
			proposals = null;
			get('/settings').then((r) => {
				aiKey = r.settings.openrouter_key_set;
				instruction ||= r.settings.ai_instruction;
			}).catch(() => {});
		}
	});

	async function run(fn: () => Promise<any>) {
		busy = true;
		try {
			const r = await fn();
			proposals = r.proposals;
			if (r.errors?.length) toast(r.errors[0], 'error');
		} catch (e) {
			errorToast(e);
		} finally {
			busy = false;
		}
	}
	function preview() {
		const ops: Record<string, any> = {};
		if (tab === 'set') {
			ops.set = Object.fromEntries(Object.entries(values).filter(([, v]) => v !== ''));
			ops.clear = Object.keys(clear).filter((k) => clear[k]);
		}
		if (tab === 'replace') ops.replace = replace;
		if (tab === 'tidy') {
			ops.clean_title = tidy.noise;
			if (tidy.case_field) ops.case = { field: tidy.case_field, mode: tidy.mode };
			if (tidy.number) ops.number = { start: tidy.from };
		}
		if (tab === 'name') ops.from_name = pattern;
		run(() => post('/files/preview', { ids, ops }));
	}
	const lookup = () => run(() => post('/files/lookup', { ids }));
	const ask = () => run(() => post('/files/ai', { ids, instruction, fields: Object.keys(fields).filter((k) => fields[k]) }));
</script>

<Modal bind:open title="Edit {ids.length} {ids.length === 1 ? 'file' : 'files'}" width="760px">
	{#if proposals}
		<Proposals {proposals} done={() => { open = false; done(); }} />
	{:else}
		<div class="col">
			<Tabs options={TABS} bind:value={tab} />
			{#if tab === 'set'}
				<p class="hint">Filled fields are written to all selected files.</p>
				{#each TEXT as [k, label] (k)}
					<div class="row">
						<label class="field grow">{label}<input type="text" bind:value={values[k]} disabled={clear[k]} /></label>
						<label class="check small clear"><input type="checkbox" bind:checked={clear[k]} /> Clear</label>
					</div>
				{/each}
			{:else if tab === 'replace'}
				<label class="field">In<select bind:value={replace.field}>{#each TEXT as [k, label] (k)}<option value={k}>{label}</option>{/each}</select></label>
				<label class="field">Find<input type="text" bind:value={replace.find} /></label>
				<label class="field">Replace with<input type="text" bind:value={replace.with} /></label>
				<div class="row">
					<label class="check"><input type="checkbox" bind:checked={replace.regex} /> Regular expression</label>
					<label class="check"><input type="checkbox" bind:checked={replace.match_case} /> Match case</label>
				</div>
			{:else if tab === 'tidy'}
				<label class="check"><input type="checkbox" bind:checked={tidy.noise} /> Remove noise from titles: (Official Video), [Lyrics], feat. …</label>
				<div class="grid2">
					<label class="field">Change the case of
						<select bind:value={tidy.case_field}><option value="">Nothing</option>{#each TEXT as [k, label] (k)}<option value={k}>{label}</option>{/each}</select>
					</label>
					<label class="field">To
						<select bind:value={tidy.mode} disabled={!tidy.case_field}><option value="title">Title Case</option><option value="upper">UPPER CASE</option><option value="lower">lower case</option></select>
					</label>
				</div>
				<label class="check"><input type="checkbox" bind:checked={tidy.number} /> Number the tracks in this order</label>
				{#if tidy.number}<label class="field">Start at<input type="number" min="1" bind:value={tidy.from} /></label>{/if}
			{:else if tab === 'name'}
				<label class="field">Pattern<input type="text" class="mono" bind:value={pattern} /></label>
				<p class="hint">Reads tags from the file name, for example <code>{'{artist} - {title}'}</code> or <code>{'{track} {title}'}</code>. Files that don't fit stay as they are.</p>
			{:else if tab === 'lookup'}
				<p class="hint">Looks every file up on Deezer. Only confident matches are proposed, and you see all changes first.</p>
			{:else}
				{#if !aiKey}<p class="small warn">Add an OpenRouter API key in Settings first.</p>{/if}
				<label class="field">Instruction<textarea rows="6" bind:value={instruction}></textarea></label>
				<div class="row wrap">
					<span class="small faint">May change:</span>
					{#each TEXT.filter(([k]) => k !== 'label') as [k, label] (k)}
						<label class="check small"><input type="checkbox" bind:checked={fields[k]} /> {label}</label>
					{/each}
				</div>
				<p class="hint">File names and tags of the selected songs go to OpenRouter. You see every suggestion before it's written.</p>
			{/if}
		</div>
	{/if}
	{#snippet footer()}
		{#if proposals}
			<button onclick={() => (proposals = null)}>Back</button>
		{:else}
			<button onclick={() => (open = false)}>Cancel</button>
			{#if tab === 'lookup'}
				<button class="primary" onclick={lookup} disabled={busy}>{busy ? 'Looking up…' : 'Look up'}</button>
			{:else if tab === 'ai'}
				<button class="primary" onclick={ask} disabled={busy || !aiKey}><Sparkles size={15} /> {busy ? 'Asking…' : 'Get suggestions'}</button>
			{:else}
				<button class="primary" onclick={preview} disabled={busy}>Preview</button>
			{/if}
		{/if}
	{/snippet}
</Modal>

<style>
	.col {
		gap: 10px;
	}
	.clear {
		margin-top: 20px;
	}
	.warn {
		color: var(--warn);
	}
</style>
