-- Schema snapshot used by tests/test_schema_parity.py.
-- Generated from models.py create_all() at schema version 19.
-- Regenerate with: uv run python scripts/dump_schema_snapshot.py

CREATE TABLE _schema_version (version INTEGER);

CREATE TABLE annotators (
	name VARCHAR NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (name)
);

CREATE TABLE app_settings (
	"key" VARCHAR NOT NULL, 
	value VARCHAR, 
	PRIMARY KEY ("key")
);

CREATE TABLE behaviors (
	id VARCHAR NOT NULL, 
	"key" VARCHAR NOT NULL, 
	name_en VARCHAR NOT NULL, 
	name_fr VARCHAR, 
	is_custom BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE ("key")
);

CREATE TABLE individual_observations (
	video_id VARCHAR NOT NULL, 
	id INTEGER NOT NULL, 
	project_id VARCHAR, 
	species_id VARCHAR, 
	count INTEGER, 
	start_sec FLOAT NOT NULL, 
	end_sec FLOAT, 
	labeled_by VARCHAR, 
	labeled_at DATETIME, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (video_id, id), 
	FOREIGN KEY(video_id) REFERENCES videos (video_id), 
	FOREIGN KEY(project_id) REFERENCES projects (id), 
	FOREIGN KEY(species_id) REFERENCES species (id)
);

CREATE TABLE model_annotations (
	id VARCHAR NOT NULL, 
	project_id VARCHAR, 
	video_id VARCHAR NOT NULL, 
	annotation_type VARCHAR NOT NULL, 
	model_name VARCHAR NOT NULL, 
	value_text VARCHAR, 
	value_num FLOAT, 
	probability FLOAT, 
	t_start_sec FLOAT, 
	t_end_sec FLOAT, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(project_id) REFERENCES projects (id), 
	FOREIGN KEY(video_id) REFERENCES videos (video_id)
);

CREATE TABLE observation_tags (
	video_id VARCHAR NOT NULL, 
	observation_id INTEGER NOT NULL, 
	behavior_id VARCHAR NOT NULL, 
	PRIMARY KEY (video_id, observation_id, behavior_id), 
	FOREIGN KEY(video_id) REFERENCES videos (video_id), 
	FOREIGN KEY(behavior_id) REFERENCES behaviors (id)
);

CREATE TABLE project_dirs (
	id VARCHAR NOT NULL, 
	project_id VARCHAR NOT NULL, 
	path VARCHAR NOT NULL, 
	sort_order INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_project_dir UNIQUE (project_id, path), 
	FOREIGN KEY(project_id) REFERENCES projects (id)
);

CREATE TABLE project_species (
	project_id VARCHAR NOT NULL, 
	species_id VARCHAR NOT NULL, 
	PRIMARY KEY (project_id, species_id), 
	FOREIGN KEY(project_id) REFERENCES projects (id), 
	FOREIGN KEY(species_id) REFERENCES species (id)
);

CREATE TABLE projects (
	id VARCHAR NOT NULL, 
	name VARCHAR NOT NULL, 
	created_at DATETIME NOT NULL, 
	last_opened DATETIME, 
	collection_id VARCHAR, 
	PRIMARY KEY (id), 
	UNIQUE (name), 
	FOREIGN KEY(collection_id) REFERENCES species_collections (id)
);

CREATE TABLE species (
	id VARCHAR NOT NULL, 
	scientific_name VARCHAR NOT NULL, 
	name_en VARCHAR, 
	name_fr VARCHAR, 
	group_en VARCHAR, 
	group_fr VARCHAR, 
	iucn VARCHAR, 
	inaturalist_url VARCHAR, 
	is_custom BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (scientific_name)
);

CREATE TABLE species_collection_members (
	collection_id VARCHAR NOT NULL, 
	species_id VARCHAR NOT NULL, 
	PRIMARY KEY (collection_id, species_id), 
	FOREIGN KEY(collection_id) REFERENCES species_collections (id), 
	FOREIGN KEY(species_id) REFERENCES species (id)
);

CREATE TABLE species_collections (
	id VARCHAR NOT NULL, 
	name VARCHAR NOT NULL, 
	is_custom BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (name)
);

CREATE TABLE tags (
	id VARCHAR NOT NULL, 
	"key" VARCHAR NOT NULL, 
	name_en VARCHAR NOT NULL, 
	name_fr VARCHAR, 
	color VARCHAR, 
	icon VARCHAR, 
	is_custom BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE ("key")
);

CREATE TABLE video_assignments (
	video_id VARCHAR NOT NULL, 
	assigned_to VARCHAR NOT NULL, 
	assigned_at DATETIME NOT NULL, 
	PRIMARY KEY (video_id), 
	FOREIGN KEY(video_id) REFERENCES videos (video_id), 
	FOREIGN KEY(assigned_to) REFERENCES annotators (name)
);

CREATE TABLE video_labels (
	video_id VARCHAR NOT NULL, 
	is_blank BOOLEAN, 
	labeled_by VARCHAR, 
	labeled_at DATETIME, 
	review_later BOOLEAN, 
	PRIMARY KEY (video_id), 
	FOREIGN KEY(video_id) REFERENCES videos (video_id)
);

CREATE TABLE video_tags (
	video_id VARCHAR NOT NULL, 
	tag_id VARCHAR NOT NULL, 
	tagged_by VARCHAR, 
	tagged_at DATETIME NOT NULL, 
	PRIMARY KEY (video_id, tag_id), 
	FOREIGN KEY(video_id) REFERENCES videos (video_id), 
	FOREIGN KEY(tag_id) REFERENCES tags (id)
);

CREATE TABLE videos (
	video_id VARCHAR NOT NULL, 
	project_id VARCHAR, 
	video_path VARCHAR NOT NULL, 
	camera_id VARCHAR, 
	created_at DATETIME, 
	duration_sec FLOAT, 
	last_seen_at DATETIME NOT NULL, 
	is_valid BOOLEAN, 
	is_missing BOOLEAN DEFAULT '0' NOT NULL, 
	is_web_safe BOOLEAN, 
	validation_error VARCHAR, 
	transcoded_path VARCHAR, 
	latitude FLOAT, 
	longitude FLOAT, 
	PRIMARY KEY (video_id), 
	CONSTRAINT uq_video_path_project UNIQUE (video_path, project_id), 
	FOREIGN KEY(project_id) REFERENCES projects (id)
);

CREATE INDEX idx_individual_video_species ON individual_observations (video_id, species_id);

CREATE INDEX idx_individual_video_time ON individual_observations (video_id, start_sec);

CREATE INDEX idx_model_ann_blank_probe ON model_annotations (annotation_type, video_id, probability);

CREATE INDEX idx_model_ann_type_text_video ON model_annotations (annotation_type, value_text, video_id);

CREATE INDEX idx_observation_tags_video ON observation_tags (video_id, behavior_id);

CREATE INDEX idx_videos_is_valid ON videos (is_valid);

CREATE INDEX idx_videos_is_web_safe ON videos (is_web_safe);

CREATE INDEX ix_individual_observations_project_id ON individual_observations (project_id);

CREATE INDEX ix_individual_observations_species_id ON individual_observations (species_id);

CREATE INDEX ix_model_annotations_annotation_type ON model_annotations (annotation_type);

CREATE INDEX ix_model_annotations_model_name ON model_annotations (model_name);

CREATE INDEX ix_model_annotations_project_id ON model_annotations (project_id);

CREATE INDEX ix_model_annotations_video_id ON model_annotations (video_id);

CREATE INDEX ix_project_dirs_project_id ON project_dirs (project_id);

CREATE INDEX ix_video_assignments_assigned_to ON video_assignments (assigned_to);

CREATE INDEX ix_videos_camera_id ON videos (camera_id);

CREATE INDEX ix_videos_project_id ON videos (project_id);

CREATE UNIQUE INDEX uq_model_ann_identity ON model_annotations (video_id, model_name, annotation_type, coalesce(value_text, ''));

INSERT INTO _schema_version VALUES (19);
