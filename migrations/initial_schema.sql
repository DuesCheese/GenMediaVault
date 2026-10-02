CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE users (
	username VARCHAR(80) NOT NULL, 
	password_hash TEXT NOT NULL, 
	role VARCHAR(16) NOT NULL, 
	active BOOLEAN NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (username)
);

CREATE TABLE libraries (
	name VARCHAR(200) NOT NULL, 
	mode VARCHAR(16) NOT NULL, 
	root_path TEXT, 
	watch_enabled BOOLEAN NOT NULL, 
	last_scan_at TIMESTAMP WITH TIME ZONE, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE model_refs (
	kind VARCHAR(16) NOT NULL, 
	name TEXT NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (kind, name)
);

CREATE INDEX ix_model_refs_name ON model_refs (name);

CREATE TABLE tags (
	name TEXT NOT NULL, 
	namespace TEXT NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (name)
);

CREATE INDEX ix_tags_namespace ON tags (namespace);

CREATE TABLE collections (
	name VARCHAR(200) NOT NULL, 
	kind VARCHAR(20) NOT NULL, 
	query_ast JSONB, 
	query TEXT NOT NULL, 
	revision INTEGER NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE rules (
	name VARCHAR(200) NOT NULL, 
	condition JSONB NOT NULL, 
	actions JSONB NOT NULL, 
	priority INTEGER NOT NULL, 
	enabled BOOLEAN NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE sessions (
	token_hash VARCHAR(64) NOT NULL, 
	user_id UUID NOT NULL, 
	csrf VARCHAR(64) NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (token_hash), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_sessions_expires_at ON sessions (expires_at);

CREATE INDEX ix_sessions_user_id ON sessions (user_id);

CREATE TABLE assets (
	library_id UUID NOT NULL, 
	sha256 VARCHAR(64) NOT NULL, 
	filename TEXT NOT NULL, 
	extension VARCHAR(10) NOT NULL, 
	mime_type VARCHAR(50) NOT NULL, 
	width INTEGER NOT NULL, 
	height INTEGER NOT NULL, 
	file_size BIGINT NOT NULL, 
	imported_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	source_created_at TIMESTAMP WITH TIME ZONE, 
	status VARCHAR(20) NOT NULL, 
	warnings JSONB NOT NULL, 
	trashed_at TIMESTAMP WITH TIME ZONE, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (library_id, sha256), 
	FOREIGN KEY(library_id) REFERENCES libraries (id)
);

CREATE INDEX ix_assets_trashed_at ON assets (trashed_at);

CREATE INDEX ix_assets_imported ON assets (imported_at, id);

CREATE INDEX ix_assets_sha256 ON assets (sha256);

CREATE INDEX ix_assets_status ON assets (status);

CREATE INDEX ix_assets_library_id ON assets (library_id);

CREATE INDEX ix_assets_library_imported ON assets (library_id, imported_at, id);

CREATE TABLE jobs (
	kind VARCHAR(30) NOT NULL, 
	payload JSONB NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	progress JSONB NOT NULL, 
	result JSONB NOT NULL, 
	error TEXT, 
	attempts INTEGER NOT NULL, 
	available_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	lease_until TIMESTAMP WITH TIME ZONE, 
	lease_owner VARCHAR(36), 
	requested_by UUID, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(requested_by) REFERENCES users (id)
);

CREATE INDEX ix_jobs_claim ON jobs (status, available_at);

CREATE TABLE audit_log (
	actor_id UUID, 
	action VARCHAR(80) NOT NULL, 
	details JSONB NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(actor_id) REFERENCES users (id)
);

CREATE TABLE physical_files (
	asset_id UUID NOT NULL, 
	library_id UUID NOT NULL, 
	path TEXT NOT NULL, 
	role VARCHAR(20) NOT NULL, 
	present BOOLEAN NOT NULL, 
	size BIGINT NOT NULL, 
	mtime_ns BIGINT NOT NULL, 
	sidecar_fingerprint TEXT NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (library_id, path, role), 
	FOREIGN KEY(asset_id) REFERENCES assets (id) ON DELETE CASCADE, 
	FOREIGN KEY(library_id) REFERENCES libraries (id)
);

CREATE INDEX ix_physical_files_asset_id ON physical_files (asset_id);

CREATE TABLE raw_metadata (
	asset_id UUID NOT NULL, 
	source TEXT NOT NULL, 
	parser VARCHAR(50) NOT NULL, 
	parser_version VARCHAR(20) NOT NULL, 
	schema_version INTEGER NOT NULL, 
	data JSONB NOT NULL, 
	warnings JSONB NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(asset_id) REFERENCES assets (id) ON DELETE CASCADE
);

CREATE INDEX ix_raw_metadata_asset_id ON raw_metadata (asset_id);

CREATE TABLE generations (
	asset_id UUID NOT NULL, 
	generator VARCHAR(80) NOT NULL, 
	model TEXT, 
	model_hash TEXT, 
	prompt TEXT NOT NULL, 
	negative TEXT NOT NULL, 
	seed VARCHAR(100), 
	steps INTEGER, 
	cfg FLOAT, 
	sampler TEXT, 
	scheduler TEXT, 
	normalized JSONB NOT NULL, 
	conflicts JSONB NOT NULL, 
	parsed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (asset_id), 
	FOREIGN KEY(asset_id) REFERENCES assets (id) ON DELETE CASCADE
);

CREATE INDEX ix_generations_sampler ON generations (sampler);

CREATE INDEX ix_generations_generator ON generations (generator);

CREATE INDEX ix_generations_model ON generations (model);

CREATE INDEX ix_generations_cfg ON generations (cfg);

CREATE INDEX ix_generations_seed ON generations (seed);

CREATE INDEX ix_generations_steps ON generations (steps);

CREATE TABLE asset_models (
	asset_id UUID NOT NULL, 
	model_id UUID NOT NULL, 
	weight FLOAT, 
	PRIMARY KEY (asset_id, model_id), 
	FOREIGN KEY(asset_id) REFERENCES assets (id) ON DELETE CASCADE, 
	FOREIGN KEY(model_id) REFERENCES model_refs (id)
);

CREATE TABLE prompt_tokens (
	asset_id UUID NOT NULL, 
	token TEXT NOT NULL, 
	polarity VARCHAR(10) NOT NULL, 
	weight FLOAT NOT NULL, 
	position INTEGER NOT NULL, 
	category VARCHAR(40) NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(asset_id) REFERENCES assets (id) ON DELETE CASCADE
);

CREATE INDEX ix_prompt_tokens_asset_id ON prompt_tokens (asset_id);

CREATE INDEX ix_prompt_token_lookup ON prompt_tokens (token, polarity, asset_id);

CREATE TABLE asset_tags (
	asset_id UUID NOT NULL, 
	tag_id UUID NOT NULL, 
	source VARCHAR(100) NOT NULL, 
	PRIMARY KEY (asset_id, tag_id, source), 
	FOREIGN KEY(asset_id) REFERENCES assets (id) ON DELETE CASCADE, 
	FOREIGN KEY(tag_id) REFERENCES tags (id)
);

CREATE TABLE user_assets (
	user_id UUID NOT NULL, 
	asset_id UUID NOT NULL, 
	favorite BOOLEAN NOT NULL, 
	rating INTEGER, 
	review VARCHAR(20) NOT NULL, 
	notes TEXT NOT NULL, 
	PRIMARY KEY (user_id, asset_id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	FOREIGN KEY(asset_id) REFERENCES assets (id) ON DELETE CASCADE
);

CREATE INDEX ix_user_assets_rating ON user_assets (rating);

CREATE TABLE collection_assets (
	collection_id UUID NOT NULL, 
	asset_id UUID NOT NULL, 
	source VARCHAR(100) NOT NULL, 
	PRIMARY KEY (collection_id, asset_id, source), 
	FOREIGN KEY(collection_id) REFERENCES collections (id) ON DELETE CASCADE, 
	FOREIGN KEY(asset_id) REFERENCES assets (id) ON DELETE CASCADE
);

CREATE INDEX ix_generations_prompt_trgm ON generations USING gin (prompt gin_trgm_ops);

CREATE INDEX ix_generations_negative_trgm ON generations USING gin (negative gin_trgm_ops);

CREATE INDEX ix_assets_filename_trgm ON assets USING gin (filename gin_trgm_ops);

CREATE INDEX ix_generations_model_lower ON generations (lower(model));

CREATE INDEX ix_generations_generator_lower ON generations (lower(generator));

CREATE INDEX ix_generations_sampler_lower ON generations (lower(sampler));

ALTER TABLE user_assets ADD CONSTRAINT ck_rating CHECK (rating BETWEEN 1 AND 5);

ALTER TABLE users ADD CONSTRAINT ck_role CHECK (role IN ('admin', 'user'));

ALTER TABLE libraries ADD CONSTRAINT ck_library_mode CHECK (mode IN ('managed', 'indexed'));

ALTER TABLE assets ADD CONSTRAINT ck_dimensions CHECK (width > 0 AND height > 0);
