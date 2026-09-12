"""
Tareas de Celery — publicación programada en Instagram.
"""
from __future__ import annotations
from celery import Celery
from app.config import get_settings

def _make_celery() -> Celery:
    _s = get_settings()
    app = Celery("storias", broker=_s.redis_url, backend=_s.redis_url)
    app.conf.timezone = "America/Argentina/Buenos_Aires"
    return app

celery_app = _make_celery()


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def schedule_publication(self, story_group_id: str) -> None:
    """
    Publica las historias aprobadas en Instagram vía Meta Graph API.
    Se reintenta hasta 3 veces si falla (red, rate limit, etc.)
    """
    from app.db.supabase import get_admin_client
    from app.services.encryption import decrypt
    import httpx, datetime

    db = get_admin_client()

    try:
        group = db.table("story_groups").select(
            "*, clients(meta_access_token_encrypted, instagram_account_id)"
        ).eq("id", story_group_id).single().execute()

        stories = db.table("stories").select("*").eq(
            "story_group_id", story_group_id
        ).eq("approved_at", "not.is.null").order("order").execute()

        client_data = group.data["clients"]
        access_token = decrypt(client_data["meta_access_token_encrypted"])
        ig_account = client_data["instagram_account_id"]

        for story in stories.data:
            with httpx.Client() as http:
                # 1. Crear media container
                container = http.post(
                    f"https://graph.facebook.com/v21.0/{ig_account}/media",
                    params={
                        "image_url": story["image_url"],
                        "caption": story["text"],
                        "media_type": "STORIES",
                        "access_token": access_token,
                    },
                    timeout=15,
                )
                container.raise_for_status()
                container_id = container.json()["id"]

                # 2. Publicar
                http.post(
                    f"https://graph.facebook.com/v21.0/{ig_account}/media_publish",
                    params={"creation_id": container_id, "access_token": access_token},
                    timeout=15,
                ).raise_for_status()

        db.table("story_groups").update({
            "status": "published",
            "published_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }).eq("id", story_group_id).execute()

    except Exception as exc:
        self.retry(exc=exc)
