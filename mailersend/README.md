# MailerSend Mail Provider Plugin

A mail service provider plugin for g0v0-server that sends emails using the MailerSend API.

## Configuration

The following environment variables can be set to configure the MailerSend provider:

- `MAILERSEND_API_KEY`: Your MailerSend API key (required).

## Usage

Set `email_provider` in `.env` and provide the necessary API key:

```dotenv
EMAIL_PROVIDER=-mailersend
EMAIL_PROVIDER_CONFIG='{"api_key": "your-mailersend-api-key"}'

```
