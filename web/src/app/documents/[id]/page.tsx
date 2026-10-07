"use client";

import { useParams } from "next/navigation";

import { AuthGate } from "@/components/AuthGate";
import { DocumentView } from "@/components/DocumentView";

export default function DocumentPage() {
  const { id } = useParams<{ id: string }>();
  return (
    <AuthGate>
      <DocumentView id={id} />
    </AuthGate>
  );
}
