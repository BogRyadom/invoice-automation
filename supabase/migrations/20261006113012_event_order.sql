-- now() is fixed for a whole transaction, so several events written together would tie.
-- clock_timestamp() keeps the timeline and the outbox in the order rows were written.

ALTER TABLE public.document_events ALTER COLUMN created_at SET DEFAULT clock_timestamp();
ALTER TABLE public.outbox_events ALTER COLUMN created_at SET DEFAULT clock_timestamp();
