"use client";

import type { Session } from "@supabase/supabase-js";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { type ReactNode, useEffect, useState } from "react";

import { Button, Spinner } from "@/components/ui";
import { supabase, supabaseConfigured } from "@/lib/supabase";

/** Renders the app shell for a signed-in reviewer, otherwise sends them to /login. */
export function AuthGate({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [session, setSession] = useState<Session | null | undefined>(undefined);

  useEffect(() => {
    if (!supabaseConfigured) return;
    const auth = supabase().auth;
    auth.getSession().then(({ data }) => setSession(data.session));
    const { data } = auth.onAuthStateChange((_event, next) => setSession(next));
    return () => data.subscription.unsubscribe();
  }, []);

  useEffect(() => {
    if (session === null) router.replace("/login");
  }, [session, router]);

  if (!supabaseConfigured) {
    return (
      <p className="p-8 text-sm text-red-600">
        Supabase is not configured: set NEXT_PUBLIC_SUPABASE_URL and
        NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY.
      </p>
    );
  }
  if (!session) {
    return (
      <div className="flex flex-1 items-center justify-center">
        <Spinner />
      </div>
    );
  }

  return (
    <div className="flex min-h-full flex-1 flex-col">
      <header className="sticky top-0 z-10 border-b border-zinc-200 bg-white/90 backdrop-blur dark:border-zinc-800 dark:bg-zinc-950/90">
        <div className="mx-auto flex h-12 max-w-[1600px] items-center justify-between px-4">
          <Link href="/" className="flex items-center gap-2 text-sm font-semibold">
            <span className="flex h-6 w-6 items-center justify-center rounded bg-indigo-600 text-xs text-white">
              IA
            </span>
            Invoice Automation
          </Link>
          <div className="flex items-center gap-3 text-sm text-zinc-500 dark:text-zinc-400">
            <span className="hidden sm:inline">{session.user.email}</span>
            <Button variant="ghost" onClick={() => supabase().auth.signOut()}>
              Sign out
            </Button>
          </div>
        </div>
      </header>
      <main className="mx-auto flex w-full max-w-[1600px] flex-1 flex-col px-4 py-6">{children}</main>
    </div>
  );
}
