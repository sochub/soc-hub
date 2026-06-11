import logging

from app.worker import celery_app
from app.services.jira_service import JiraService

logger = logging.getLogger(__name__)


@celery_app.task(acks_late=True, max_retries=3, default_retry_delay=30)
def create_jira_ticket_task(case_id: int, title: str, description: str):
    """Create a Jira ticket for a case. Runs synchronously in the Celery worker."""
    service = JiraService()
    result = service.create_issue_sync(title, description)

    if result and "key" in result:
        jira_key = result["key"]
        logger.info("Created Jira issue %s for case %d", jira_key, case_id)
        return {"jira_key": jira_key, "case_id": case_id}

    logger.error("Failed to create Jira issue for case %d", case_id)
    return None
