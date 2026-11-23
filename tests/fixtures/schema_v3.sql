-- Schema from v1.0.0-beta6, exported from its SQLAlchemy models.
BEGIN TRANSACTION;
CREATE TABLE _schema_version (version INTEGER);
INSERT INTO "_schema_version" VALUES(3);
CREATE TABLE app_settings (
	"key" VARCHAR NOT NULL,
	value VARCHAR,
	PRIMARY KEY ("key")
);
CREATE TABLE individual_observations (
	video_id VARCHAR NOT NULL,
	id INTEGER NOT NULL,
	project_id VARCHAR,
	species VARCHAR NOT NULL,
	behavior VARCHAR NOT NULL,
	start_sec FLOAT NOT NULL,
	end_sec FLOAT,
	labeled_by VARCHAR,
	labeled_at DATETIME,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (video_id, id),
	FOREIGN KEY(video_id) REFERENCES videos (video_id),
	FOREIGN KEY(project_id) REFERENCES projects (id)
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
	CONSTRAINT uq_model_ann_identity UNIQUE (video_id, model_name, annotation_type),
	FOREIGN KEY(project_id) REFERENCES projects (id),
	FOREIGN KEY(video_id) REFERENCES videos (video_id)
);
CREATE TABLE project_dirs (
	id VARCHAR NOT NULL,
	project_id VARCHAR NOT NULL,
	path VARCHAR NOT NULL,
	sort_order INTEGER NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(project_id) REFERENCES projects (id)
);
CREATE TABLE projects (
	id VARCHAR NOT NULL,
	name VARCHAR NOT NULL,
	created_at DATETIME NOT NULL,
	last_opened DATETIME,
	PRIMARY KEY (id),
	UNIQUE (name)
);
CREATE TABLE species (
	scientific_name VARCHAR NOT NULL,
	project_id VARCHAR,
	name_en VARCHAR,
	name_fr VARCHAR,
	group_en VARCHAR,
	group_fr VARCHAR,
	iucn VARCHAR,
	PRIMARY KEY (scientific_name),
	FOREIGN KEY(project_id) REFERENCES projects (id)
);
CREATE TABLE species_behavior (
	scientific_name VARCHAR NOT NULL,
	behavior VARCHAR NOT NULL,
	project_id VARCHAR,
	PRIMARY KEY (scientific_name, behavior),
	FOREIGN KEY(scientific_name) REFERENCES species (scientific_name),
	FOREIGN KEY(project_id) REFERENCES projects (id)
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
CREATE TABLE videos (
	video_id VARCHAR NOT NULL,
	project_id VARCHAR,
	video_path VARCHAR NOT NULL,
	camera_id VARCHAR,
	created_at DATETIME,
	duration_sec FLOAT,
	last_seen_at DATETIME NOT NULL,
	is_valid BOOLEAN,
	is_web_safe BOOLEAN,
	validation_error VARCHAR,
	transcoded_path VARCHAR,
	PRIMARY KEY (video_id),
	CONSTRAINT uq_video_path_project UNIQUE (video_path, project_id),
	FOREIGN KEY(project_id) REFERENCES projects (id)
);
CREATE INDEX ix_project_dirs_project_id ON project_dirs (project_id);
CREATE INDEX ix_videos_camera_id ON videos (camera_id);
CREATE INDEX idx_videos_is_valid ON videos (is_valid);
CREATE INDEX ix_videos_project_id ON videos (project_id);
CREATE INDEX ix_species_project_id ON species (project_id);
CREATE INDEX ix_individual_observations_project_id ON individual_observations (project_id);
CREATE INDEX idx_individual_video_behavior ON individual_observations (video_id, behavior);
CREATE INDEX idx_individual_behavior_video ON individual_observations (behavior, video_id);
CREATE INDEX idx_individual_video_species ON individual_observations (video_id, species);
CREATE INDEX idx_individual_video_time ON individual_observations (video_id, start_sec);
CREATE INDEX idx_model_ann_type_value ON model_annotations (annotation_type, value_text);
CREATE INDEX ix_model_annotations_annotation_type ON model_annotations (annotation_type);
CREATE INDEX idx_model_ann_type_text_video ON model_annotations (annotation_type, value_text, video_id);
CREATE INDEX ix_model_annotations_video_id ON model_annotations (video_id);
CREATE INDEX ix_model_annotations_model_name ON model_annotations (model_name);
CREATE INDEX ix_model_annotations_project_id ON model_annotations (project_id);
CREATE INDEX idx_model_ann_blank_probe ON model_annotations (annotation_type, video_id, probability);
CREATE INDEX ix_species_behavior_project_id ON species_behavior (project_id);
COMMIT;
