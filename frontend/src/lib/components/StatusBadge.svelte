<script lang="ts">
	import { STATUS_LABEL } from '../format';
	let { status, title = '' }: { status: string; title?: string } = $props();
	const KIND: Record<string, string> = {
		done: 'ok', ok: 'ok', valid: 'ok',
		failed: 'err', expired: 'err', missing: 'warn', partial: 'warn', review: 'warn',
		downloading: 'info', running: 'info', queued: 'info',
		pending: '', unavailable: '', ignored: '', deleted: '', removed: 'warn', cancelled: '', unknown: ''
	};
</script>

<span class="pill {KIND[status] ?? ''}" {title}>{STATUS_LABEL[status] ?? status}</span>
