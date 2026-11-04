<script lang="ts">
	let { tags = $bindable(), long = false, oninput }: { tags: Record<string, any>; long?: boolean; oninput?: () => void } = $props();
</script>

<div class="tagform">
	<label class="field wide">Title<input type="text" bind:value={tags.title} {oninput} /></label>
	<label class="field wide">Artists<input type="text" bind:value={tags.artist} {oninput} placeholder="Comma-separated" /></label>
	<label class="field wide">Album<input type="text" bind:value={tags.album} {oninput} /></label>
	<label class="field wide">Album artist<input type="text" bind:value={tags.albumartist} {oninput} /></label>
	<label class="field">Track<input type="number" min="0" bind:value={tags.track} {oninput} /></label>
	<label class="field">of<input type="number" min="0" bind:value={tags.track_total} {oninput} /></label>
	<label class="field">Disc<input type="number" min="0" bind:value={tags.disc} {oninput} /></label>
	<label class="field">of<input type="number" min="0" bind:value={tags.disc_total} {oninput} /></label>
	<label class="field half">Year or date<input type="text" bind:value={tags.year} {oninput} placeholder="2001 or 2001-03-07" /></label>
	<label class="field half">Genre<input type="text" bind:value={tags.genre} {oninput} /></label>
	<label class="field half">Label<input type="text" bind:value={tags.label} {oninput} /></label>
	<label class="field half">ISRC<input type="text" class="mono" bind:value={tags.isrc} {oninput} /></label>
	{#if long}
		<label class="field wide">Comment<input type="text" bind:value={tags.comment} {oninput} /></label>
		<label class="field wide">Lyrics<textarea rows="5" bind:value={tags.lyrics} {oninput}></textarea></label>
	{/if}
</div>

<style>
	.tagform {
		display: grid;
		grid-template-columns: repeat(4, minmax(0, 1fr));
		gap: 10px;
	}
	.wide {
		grid-column: span 4;
	}
	.half {
		grid-column: span 2;
	}
</style>
