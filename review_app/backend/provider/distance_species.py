"""Project-specific Distance Review eligibility and completion requirements."""

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.sqlite import insert

from review_app.backend.db.models import DistanceSpeciesSettings, Species


def chimpanzee_species():
    name = func.lower(Species.scientific_name)
    return or_(name == "pan troglodytes", name.like("pan troglodytes %"))


def distance_species_query(project_id):
    chimpanzee = chimpanzee_species()
    return select(
        Species.id,
        Species.scientific_name,
        func.coalesce(DistanceSpeciesSettings.enabled, chimpanzee).label("enabled"),
        func.coalesce(DistanceSpeciesSettings.require_age, chimpanzee).label("require_age"),
        func.coalesce(DistanceSpeciesSettings.require_sex, chimpanzee).label("require_sex"),
    ).outerjoin(
        DistanceSpeciesSettings,
        (DistanceSpeciesSettings.project_id == project_id)
        & (DistanceSpeciesSettings.species_id == Species.id),
    )


def enabled_distance_species(project_id):
    rules = distance_species_query(project_id).subquery()
    return select(rules.c.id).where(rules.c.enabled)


def distance_species_settings(session, project_id, species_id):
    return session.execute(
        distance_species_query(project_id).where(Species.id == species_id)
    ).one()


def save_distance_species_settings(session, project_id, species_id, **settings):
    statement = insert(DistanceSpeciesSettings).values(
        project_id=project_id, species_id=species_id, **settings
    )
    session.execute(
        statement.on_conflict_do_update(index_elements=["project_id", "species_id"], set_=settings)
    )
