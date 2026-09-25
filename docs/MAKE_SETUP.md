
# Fellowship Email Automation — Make.com Setup

## Overview

The Fellowship Email Automation will use a separate Make.com workflow based on the existing HR Email Automation workflow.

The HR workflow should be used as a structural reference only. Fellowship-specific connections, endpoints, secrets, and routing must be configured separately.

## Workflow

Expected high-level flow:

Incoming Fellowship Email
→ Make.com
→ Fellowship FastAPI Endpoint
→ Response Pipeline
→ Review / Response Action
→ Email Workflow

## Fellowship Configuration

Before enabling the workflow, confirm:

- Fellowship mailbox/connection
- Fellowship API endpoint
- Fellowship webhook configuration
- Callback secret
- Review/approval routing
- Email labels/routing
- Error handling

## Important

Do NOT commit:

- Make.com credentials
- Gmail/mailbox credentials
- Webhook secrets
- API keys
- Exported credentials

Do not modify the production HR workflow while configuring Fellowship.

## Testing

Before enabling production behavior:

1. Send a controlled Fellowship test email.
2. Confirm Make.com receives it.
3. Confirm the Fellowship API receives the request.
4. Verify classification/retrieval.
5. Verify generated response.
6. Verify review/routing behavior.
7. Confirm no HR workflow or HR Pinecone resources are being used.
