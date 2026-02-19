"""MailerSend mail provider plugin.

This plugin provides email sending functionality using the MailerSend API.
"""

import asyncio
from typing import Any

from app.config import settings
from app.log import logger
from app.service.mail_providers._base import (
    MailServiceProvider as BaseMailServiceProvider,
)
from mailersend import EmailBuilder, MailerSendClient

from pydantic import BaseModel


class _LegacyMailerSendSettings(BaseModel):
    mailersend_api_key: str = ""
    mailersend_from_email: str = ""


class MailerSendProvider(BaseMailServiceProvider):
    """MailerSend mail service provider.

    Sends emails using the MailerSend API.
    """

    def __init__(self, api_key: str, **kwargs: Any) -> None:
        """Initialize the MailerSend provider.

        Args:
            api_key: MailerSend API key.
            **kwargs: Additional configuration options (unused, config loaded from plugin config).
        """
        super().__init__(**kwargs)
        legacy_setting = _LegacyMailerSendSettings.model_validate(settings.model_dump())

        self.api_key = api_key or legacy_setting.mailersend_api_key
        self.from_email = settings.from_email or legacy_setting.mailersend_from_email
        self._client: MailerSendClient | None = None

    async def init(self) -> None:
        """Initialize the MailerSend client."""
        if not self.api_key:
            raise ValueError("MailerSend API Key is required. Set it in `EMAIL_PROVIDER_CONFIG`.")

        self._client = MailerSendClient(api_key=self.api_key)
        logger.info("MailerSend provider initialized")

    async def send_email(
        self,
        to_email: str,
        subject: str,
        content: str,
        html_content: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        """Send an email via MailerSend.

        Args:
            to_email: Recipient email address.
            subject: Email subject.
            content: Plain text email content.
            html_content: HTML email content (optional).
            metadata: Additional metadata (unused).

        Returns:
            Dictionary with 'id' key containing the message ID.
        """
        try:
            _ = metadata  # Unused

            if self._client is None:
                raise RuntimeError("MailerSend client not initialized. Call init() first.")

            # Build email
            email_builder = EmailBuilder()
            email_builder.to_many([{"email": to_email}])
            email_builder.from_email(self.from_email, settings.from_name)
            email_builder.subject(subject)

            # Prefer HTML content, otherwise use plain text
            if html_content:
                email_builder.html(html_content)
            else:
                email_builder.text(content)

            email = email_builder.build()

            # Send email
            response = await asyncio.get_running_loop().run_in_executor(None, self._client.emails.send, email)

            # Extract message_id from APIResponse
            message_id = getattr(response, "id", "") if response else ""
            logger.info(f"Successfully sent email via MailerSend to {to_email}, message_id: {message_id}")
            return {"id": message_id}

        except Exception as e:
            logger.error(f"Failed to send email via MailerSend: {e}")
            return {"id": ""}


MailServiceProvider = MailerSendProvider
