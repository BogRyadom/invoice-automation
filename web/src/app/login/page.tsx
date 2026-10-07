"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

import { Alert, Button, inputClass } from "@/components/ui";
import { supabase, supabaseConfigured } from "@/lib/supabase";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function signIn(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const { error: authError } = await supabase().auth.signInWithPassword({ email, password });
    setBusy(false);
    if (authError) {
      setError(authError.message);
      return;
    }
    router.replace("/");
  }

  return (
    <main className="flex flex-1 items-center justify-center px-4">
      <form
        onSubmit={signIn}
        className="w-full max-w-sm space-y-4 rounded-lg border border-zinc-200 bg-white p-6 shadow-sm dark:border-zinc-800 dark:bg-zinc-900"
      >
        <div>
          <h1 className="text-lg font-semibold">Invoice Automation</h1>
          <p className="text-sm text-zinc-500 dark:text-zinc-400">Sign in to review invoices.</p>
        </div>
        {!supabaseConfigured && <Alert tone="error" title="Supabase is not configured" />}
        {error && <Alert tone="error">{error}</Alert>}
        <label className="block space-y-1 text-sm">
          <span className="font-medium">Email</span>
          <input
            type="email"
            required
            autoComplete="username"
            className={inputClass}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </label>
        <label className="block space-y-1 text-sm">
          <span className="font-medium">Password</span>
          <input
            type="password"
            required
            autoComplete="current-password"
            className={inputClass}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        <Button type="submit" variant="primary" className="w-full" disabled={busy || !supabaseConfigured}>
          {busy ? "Signing in…" : "Sign in"}
        </Button>
        <p className="text-xs text-zinc-500 dark:text-zinc-400">
          Accounts are created by an administrator in Supabase Studio.
        </p>
      </form>
    </main>
  );
}
