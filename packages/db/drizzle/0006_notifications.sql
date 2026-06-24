-- Phase B (cont.): persist in-app notifications so the notification center
-- survives restart and is shared across workers. Mirrors the Notification
-- dataclass (text id, epoch-float created_at).

CREATE TABLE IF NOT EXISTS notifications (
  id text PRIMARY KEY,
  tenant_id varchar(128) NOT NULL,
  user_id varchar(128),
  kind varchar(32) NOT NULL DEFAULT 'info',
  title text NOT NULL,
  body text NOT NULL DEFAULT '',
  read boolean NOT NULL DEFAULT false,
  created_at double precision NOT NULL
);

CREATE INDEX IF NOT EXISTS notifications_tenant_created_idx
  ON notifications (tenant_id, created_at DESC);

CREATE INDEX IF NOT EXISTS notifications_tenant_unread_idx
  ON notifications (tenant_id) WHERE read = false;
