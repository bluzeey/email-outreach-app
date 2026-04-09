# Followup Feature Implementation Summary

## Overview
This implementation adds comprehensive followup functionality to the Email Outreach App, including:
- Global lead management with lifecycle status
- Tagging system for leads
- AI-generated followup emails
- Same-thread followup sending via Gmail

## Changes Made

### 1. Database Models (app/db/models.py)
- **LeadStatus** enum: ACTIVE, RESPONDED, DO_NOT_CONTACT, BOUNCED
- **Lead** model: Global contact record with email (unique), profile fields, status, followup tracking
- **LeadTag** model: Tags for categorizing leads (researchers, startups, vip, etc.)
- **LeadTagAssociation**: Many-to-many join table
- **FollowupDraft** model: Stores AI-generated followup drafts
- Updated **CampaignRow** with `lead_id` foreign key
- Updated **SendEvent** with `provider_thread_id`, `is_followup`, `original_send_event_id`

### 2. Migration (alembic/versions/8a9f3c2d4e5f_add_leads_tags_followup_tables.py)
- Creates all new tables
- Adds columns to campaign_rows and send_events
- Sets up foreign key relationships

### 3. Services

#### LeadService (app/services/lead_service.py)
- `upsert_lead_from_row()`: Creates/updates leads from campaign data
- `is_eligible_for_followup()`: Checks if lead can receive followup
- `mark_as_responded()`: Blocks all followups for lead globally
- `mark_followup_sent()`: Tracks one followup max
- Tag management methods

#### FollowupService (app/services/followup_service.py)
- `generate_followup_draft()`: AI-powered followup generation
- `_generate_followup_content()`: LLM prompt for gentle/polite/direct tones
- Uses original email context for threading

### 4. API Endpoints

#### Leads API (app/api/leads.py) - `/api/leads`
- `GET /` - List leads with filtering (status, tag, search, eligible_for_followup)
- `GET /{lead_id}` - Get single lead
- `PUT /{lead_id}` - Update lead info
- `POST /{lead_id}/status` - Update lifecycle status
- `POST /{lead_id}/tags` - Add tag to lead
- `DELETE /{lead_id}/tags/{tag_id}` - Remove tag
- `POST /{lead_id}/mark-responded` - Mark as responded (global block)
- `GET /{lead_id}/eligibility` - Check followup eligibility
- `GET /tags` - List all tags
- `POST /tags` - Create new tag
- `DELETE /tags/{tag_id}` - Delete tag
- `POST /bulk-action` - Bulk status/tag updates

#### Followups API (app/api/followups.py) - `/api/followups`
- `GET /stats` - Followup statistics dashboard
- `GET /eligible-leads` - Get leads ready for followup
- `POST /preview` - Generate followup draft preview
- `GET /drafts/{draft_id}` - Get draft
- `PUT /drafts/{draft_id}` - Edit draft
- `POST /drafts/{draft_id}/approve` - Approve for sending
- `POST /drafts/{draft_id}/reject` - Reject draft
- `POST /send` - Send followup (respects dry_run)
- `POST /bulk-send` - Send multiple followups

### 5. Gmail Client Updates (app/services/gmail_client.py)
- Added `gmail.modify` scope for thread management
- `send_email()` now accepts `thread_id`, `in_reply_to`, `references`
- `create_mime_message()` adds threading headers
- Followups are sent in the same Gmail thread as original

### 6. Campaign Ingestion Updates
- **campaign_nodes.py**: `prepare_recipient_records()` now upserts leads and links rows
- **campaigns.py**: `_append_leads_to_campaign()` also creates leads

### 7. UI Templates

#### Leads Page (app/templates/leads.html)
- Comprehensive lead management interface
- Stats dashboard (total, eligible, responded, followed up)
- Filters: status, tag, eligibility, search
- Bulk actions: mark responded/active, add tags
- Followup preview and sending
- Tag management modal
- Editable followup drafts

#### Navigation Updates
- Added "Leads" link to all templates: index.html, campaigns.html, campaign_new.html, campaign_detail.html, auth.html

#### Auth Page Updates (app/templates/auth.html)
- Updated permissions list to show "Manage email threads (for followups)"
- Added note about reconnecting for new scope

### 8. Schemas
- **app/schemas/leads.py**: Lead, LeadTag request/response schemas
- **app/schemas/followup.py**: Followup draft, send, eligibility schemas

### 9. Router Registration (app/main.py)
- Added `leads.router` at `/api/leads`
- Added `followups.router` at `/api/followups`
- Added `/leads` page route in pages.py

## User Workflow

### First Time Setup
1. **Reconnect Gmail**: Due to new `gmail.modify` scope, users must reconnect their Gmail account
2. **Upload CSV**: Campaign ingestion automatically creates global leads

### Managing Leads
1. Go to **Leads** page
2. View stats dashboard
3. Filter by status, tags, or search
4. Select leads and use bulk actions (mark responded, add tags)

### Sending Followups
1. Find eligible lead (status=active, no followup yet)
2. Click "Followup" button
3. Review AI-generated draft
4. Edit if needed (click Edit, modify, Save)
5. Click "Send Followup" (or dry-run for testing)

### Bulk Followups
1. Click "Followup Stats" → "Generate Followup Drafts"
2. Select tone (gentle/polite/direct)
3. Review generated drafts
4. Bulk send approved drafts

### Blocking Followups
1. When a lead responds, mark them as "Responded"
2. This blocks followups across ALL campaigns globally
3. Or use "Do Not Contact" for unsubscribed/bounced leads

## Key Design Decisions

1. **Global Lead Identity**: Leads are unique by email across all campaigns
2. **One Followup Max**: Leads can only receive one followup ever
3. **Global Responded Status**: Marking as responded blocks followups everywhere
4. **AI-Generated Drafts**: Followups are AI-generated based on original email context
5. **Same Thread Sending**: Uses Gmail thread_id for proper threading
6. **Manual Bulk Send**: No automatic scheduling; user controls when to send

## Migration Instructions

1. Run the Alembic migration:
   ```bash
   alembic upgrade head
   ```

2. Reconnect Gmail account (to get new `gmail.modify` scope)

3. Existing campaign rows will need lead_id backfill - new uploads will auto-create leads

## API Usage Examples

### Check lead eligibility
```bash
GET /api/leads/{lead_id}/eligibility
```

### Generate followup preview
```bash
POST /api/followups/preview
{
  "campaign_row_id": "uuid",
  "tone": "gentle"
}
```

### Send followup
```bash
POST /api/followups/send
{
  "draft_id": "uuid",
  "dry_run": false
}
```

### Bulk mark responded
```bash
POST /api/leads/bulk-action
{
  "lead_ids": ["id1", "id2"],
  "action": "update_status",
  "status": "responded"
}
```
