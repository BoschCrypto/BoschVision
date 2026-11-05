"""Upload to YouTube via the Data API v3, marked made-for-kids and AI-generated.

One-time setup: create an OAuth client (Desktop app) in Google Cloud Console,
enable "YouTube Data API v3", save the JSON as config/youtube_client_secret.json,
then run `kidclips youtube-auth` once.
"""
from __future__ import annotations

from pathlib import Path

from .concept import Concept
from .config import Settings

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def _credentials(settings: Settings, interactive: bool = False):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if settings.youtube_token_file.exists():
        creds = Credentials.from_authorized_user_file(str(settings.youtube_token_file), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds or not creds.valid:
        if not interactive:
            raise RuntimeError("No YouTube login yet: run `kidclips youtube-auth` first")
        flow = InstalledAppFlow.from_client_secrets_file(str(settings.youtube_client_secrets), SCOPES)
        creds = flow.run_local_server(port=0)
    settings.youtube_token_file.parent.mkdir(parents=True, exist_ok=True)
    settings.youtube_token_file.write_text(creds.to_json(), encoding="utf-8")
    return creds


def authorize(settings: Settings) -> None:
    _credentials(settings, interactive=True)


def upload(video: Path, concept: Concept, settings: Settings, privacy: str | None = None) -> str:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    youtube = build("youtube", "v3", credentials=_credentials(settings))
    title = concept.title[:100]
    if "#shorts" not in concept.description.lower():
        description = concept.description + "\n\n#shorts #kidssongs"
    else:
        description = concept.description
    body = {
        "snippet": {"title": title, "description": description[:5000],
                    "tags": concept.tags[:30], "categoryId": "10"},  # 10 = Music
        "status": {
            "privacyStatus": privacy or settings.youtube_privacy,
            "selfDeclaredMadeForKids": True,   # required for children's content (COPPA)
            "containsSyntheticMedia": True,    # YouTube's AI-content disclosure
        },
    }
    req = youtube.videos().insert(part="snippet,status", body=body,
                                  media_body=MediaFileUpload(str(video), resumable=True, chunksize=8 << 20))
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    return resp["id"]
