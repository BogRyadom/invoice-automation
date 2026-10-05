-- Initial schema. See docs/SPEC.md sections 5, 8 and 9.
--
-- Row level security is enabled on every table without policies: the Data API roles
-- (anon, authenticated) can read nothing. The backend connects directly as the table owner.
-- Foreign keys use ON DELETE RESTRICT because originals and their history are never deleted.

CREATE FUNCTION public.set_updated_at() RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

CREATE TABLE public.documents (
    id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    gmail_message_id         text NOT NULL,
    attachment_id            text,
    filename                 text NOT NULL,
    sha256                   text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    storage_path             text NOT NULL,
    sender                   text,
    subject                  text,
    received_at              timestamptz NOT NULL,
    status                   text NOT NULL DEFAULT 'received' CHECK (status IN (
                                 'received', 'processing', 'skipped', 'failed', 'needs_review',
                                 'auto_approved', 'approved', 'rejected', 'exported')),
    skip_reason              text CHECK (skip_reason IN (
                                 'not_invoice', 'unsupported_type', 'duplicate_file')),
    failure_reason           text CHECK (failure_reason IN (
                                 'too_large', 'unreadable_pdf', 'encrypted_pdf', 'llm_unavailable',
                                 'invalid_extraction', 'processing_timeout')),
    duplicate_of_document_id uuid REFERENCES public.documents (id) ON DELETE RESTRICT,
    extraction_path          text CHECK (extraction_path IN ('text', 'vision')),
    attempts                 integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_attempt_at          timestamptz,
    locked_at                timestamptz,
    last_error               text,
    created_at               timestamptz NOT NULL DEFAULT now(),
    updated_at               timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT documents_gmail_message_id_sha256_key UNIQUE (gmail_message_id, sha256)
);

CREATE INDEX documents_status_next_attempt_at_idx ON public.documents (status, next_attempt_at);
CREATE INDEX documents_sha256_idx ON public.documents (sha256);

CREATE TABLE public.extractions (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id    uuid NOT NULL REFERENCES public.documents (id) ON DELETE RESTRICT,
    provider       text NOT NULL,
    model          text NOT NULL,
    prompt_version text NOT NULL,
    raw_output     jsonb,
    normalized     jsonb,
    latency_ms     integer CHECK (latency_ms >= 0),
    input_tokens   integer CHECK (input_tokens >= 0),
    output_tokens  integer CHECK (output_tokens >= 0),
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX extractions_document_id_idx ON public.extractions (document_id);

CREATE TABLE public.check_results (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    extraction_id uuid NOT NULL REFERENCES public.extractions (id) ON DELETE RESTRICT,
    check_id      text NOT NULL,
    severity      text NOT NULL CHECK (severity IN ('hard', 'warning')),
    status        text NOT NULL CHECK (status IN ('pass', 'fail', 'not_run')),
    field         text,
    message       text,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX check_results_extraction_id_idx ON public.check_results (extraction_id);

CREATE TABLE public.vendors (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_name  text NOT NULL,
    normalized_name text NOT NULL UNIQUE,
    tax_id          text,
    date_format     text CHECK (date_format IN ('DMY', 'MDY')),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX vendors_tax_id_key ON public.vendors (tax_id) WHERE tax_id IS NOT NULL;

CREATE TABLE public.vendor_aliases (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    vendor_id        uuid NOT NULL REFERENCES public.vendors (id) ON DELETE RESTRICT,
    normalized_alias text NOT NULL UNIQUE,
    created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX vendor_aliases_vendor_id_idx ON public.vendor_aliases (vendor_id);

CREATE TABLE public.invoices (
    id                        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id               uuid NOT NULL UNIQUE REFERENCES public.documents (id) ON DELETE RESTRICT,
    vendor_id                 uuid NOT NULL REFERENCES public.vendors (id) ON DELETE RESTRICT,
    invoice_number            text NOT NULL,
    invoice_number_normalized text NOT NULL,
    invoice_date              date NOT NULL,
    due_date                  date,
    currency                  text NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    subtotal                  numeric(18, 4),
    discount                  numeric(18, 4),
    shipping                  numeric(18, 4),
    tax_total                 numeric(18, 4),
    total                     numeric(18, 4) NOT NULL,
    approval_mode             text NOT NULL CHECK (approval_mode IN (
                                  'auto', 'human', 'human_override', 'manual_entry')),
    approved_by               text,
    approved_at               timestamptz NOT NULL DEFAULT now(),
    exported_at               timestamptz,
    created_at                timestamptz NOT NULL DEFAULT now(),
    updated_at                timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT invoices_vendor_id_invoice_number_normalized_key
        UNIQUE (vendor_id, invoice_number_normalized)
);

CREATE TABLE public.invoice_line_items (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id  uuid NOT NULL REFERENCES public.invoices (id) ON DELETE RESTRICT,
    position    integer NOT NULL CHECK (position >= 1),
    description text,
    quantity    numeric(18, 4),
    unit_price  numeric(18, 4),
    amount      numeric(18, 4),
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT invoice_line_items_invoice_id_position_key UNIQUE (invoice_id, position)
);

CREATE TABLE public.review_edits (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id     uuid NOT NULL REFERENCES public.documents (id) ON DELETE RESTRICT,
    field           text NOT NULL,
    extracted_value text,
    final_value     text,
    edited_by       text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX review_edits_document_id_idx ON public.review_edits (document_id);

CREATE TABLE public.document_events (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id uuid NOT NULL REFERENCES public.documents (id) ON DELETE RESTRICT,
    event_type  text NOT NULL,
    actor       text NOT NULL,
    payload     jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX document_events_document_id_created_at_idx
    ON public.document_events (document_id, created_at);

CREATE TABLE public.outbox_events (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id     uuid REFERENCES public.documents (id) ON DELETE RESTRICT,
    event_type      text NOT NULL,
    payload         jsonb NOT NULL DEFAULT '{}'::jsonb,
    attempts        integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    delivered_at    timestamptz,
    last_error      text,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX outbox_events_pending_idx
    ON public.outbox_events (next_attempt_at) WHERE delivered_at IS NULL;

CREATE TRIGGER documents_set_updated_at BEFORE UPDATE ON public.documents
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
CREATE TRIGGER vendors_set_updated_at BEFORE UPDATE ON public.vendors
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
CREATE TRIGGER invoices_set_updated_at BEFORE UPDATE ON public.invoices
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

ALTER TABLE public.documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.extractions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.check_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.vendors ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.vendor_aliases ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.invoices ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.invoice_line_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.review_edits ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.document_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.outbox_events ENABLE ROW LEVEL SECURITY;
