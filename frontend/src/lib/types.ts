export type Proposal = {
	item_id: number;
	title: string;
	file: string | null;
	before: Record<string, string>;
	after: Record<string, string>;
	changed: string[];
	rename_to: string | null;
	include?: boolean;
};

export type Meta = Record<string, any> & { has_cover?: boolean; format?: string };

export type Item = {
	id: number;
	playlist_id: number;
	video_id: string;
	position: number;
	status: string;
	file_path: string | null;
	file_name: string | null;
	file_size: number | null;
	error: string | null;
	error_kind: string | null;
	attempts: number;
	first_seen_at: number;
	removed_at: number | null;
	downloaded_at: number | null;
	meta: Meta | null;
	meta_override: Record<string, string>;
	title: string;
	channel: string;
	duration: number | null;
	thumbnail: string | null;
	upload_date: string | null;
	yt_artist: string | null;
	yt_album: string | null;
	playlist_title: string | null;
};

export type Playlist = {
	id: number;
	url: string;
	title: string | null;
	folder: string | null;
	account_id: number | null;
	account_label?: string | null;
	account_status?: string | null;
	proxy_mode: string;
	proxy_id: number | null;
	schedule: string;
	enabled: boolean;
	on_removed: string;
	redownload_missing: boolean;
	options: Record<string, any>;
	entry_count: number | null;
	last_sync_at: number | null;
	last_sync_status: string | null;
	last_error: string | null;
	next_run_at: number | null;
	storage_offline_at: number | null;
	counts: Record<string, number>;
	bytes?: number;
	job?: any;
};

/** selected tracks of one playlist, and whether that playlist can be changed on YouTube */
export type EditGroup = {
	playlist_id: number;
	title: string;
	item_ids: number[];
	writable: boolean;
	reason: string | null;
	files_follow: boolean;
	syncing: boolean;
};
