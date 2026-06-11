import logging
from typing import Optional, Dict, Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


def _build_issue_payload(title: str, description: str, project_key: str) -> dict:
    """Build a Jira v3 issue creation payload."""
    return {
        "fields": {
            "project": {"key": project_key},
            "summary": title,
            "description": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": description or ""}],
                    }
                ],
            },
            "issuetype": {"name": "Task"},
        }
    }


class JiraService:
    def __init__(self):
        self.base_url = settings.JIRA_URL
        self.username = settings.JIRA_USER
        self.api_token = settings.JIRA_API_TOKEN
        self.auth = (self.username, self.api_token) if self.username and self.api_token else None

    def _is_configured(self) -> bool:
        if not self.auth or not self.base_url:
            logger.warning("Jira credentials not configured")
            return False
        return True

    def _api_url(self, path: str) -> str:
        return f"{self.base_url.rstrip('/')}/rest/api/3/{path.lstrip('/')}"

    async def create_issue(
        self, title: str, description: str, project_key: str = "SEC"
    ) -> Optional[Dict[str, Any]]:
        if not self._is_configured():
            return None

        payload = _build_issue_payload(title, description, project_key)

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self._api_url("issue"), json=payload, auth=self.auth
                )
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as e:
            logger.error("Jira API Error: %s", e)
            return None

    def create_issue_sync(
        self, title: str, description: str, project_key: str = "SEC"
    ) -> Optional[Dict[str, Any]]:
        if not self._is_configured():
            return None

        payload = _build_issue_payload(title, description, project_key)

        try:
            with httpx.Client() as client:
                response = client.post(
                    self._api_url("issue"), json=payload, auth=self.auth
                )
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as e:
            logger.error("Jira Sync API Error: %s", e)
            return None
