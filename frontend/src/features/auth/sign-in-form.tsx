"use client";

import { useRouter } from "next/navigation";
import { useEffect, useLayoutEffect, useRef, useState, type FormEvent } from "react";
import { LIVE_ROLES } from "@/lib/live/roles";
import { AuthRejected, LIVE_CONFIGURED, liveSignIn, liveSignOut } from "@/lib/live/session";
import { ROUTES } from "@/lib/routes";
import { signIn } from "@/lib/store/actions";
import { useDemoState, useHydrated } from "@/lib/store/hooks";
import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { Field, TextInput } from "@/components/ui/field";

const MAX_ATTEMPTS = 3;
const LOCK_MS = 15 * 60 * 1000;
/** Prototype-only one-time code. Real sign-in is delegated to Supabase Auth. */
const DEMO_CODE = "123456";

function emailError(value: string): string | null {
  const email = value.trim();
  const at = email.indexOf("@");
  if (at < 1 || email.indexOf(".", at) < 0) return "Enter a valid work email, like name@bank.com.";
  return null;
}

export function SignInForm() {
  const router = useRouter();
  const [step, setStep] = useState<"credentials" | "code">("credentials");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [attempts, setAttempts] = useState(0);
  const lockTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (lockTimer.current) clearTimeout(lockTimer.current);
  }, []);
  const [busy, setBusy] = useState(false);
  const locked = attempts >= MAX_ATTEMPTS;
  const hydrated = useHydrated();
  const authed = useDemoState().authed;

  useEffect(() => {
    if (hydrated && authed) router.replace(ROUTES.command);
  }, [hydrated, authed, router]);

  /** Clears entered credentials. The attempt counter is kept so a lockout cannot be bypassed. */
  const clearCredentials = () => {
    setStep("credentials");
    setEmail("");
    setPassword("");
    setCode("");
    setError(null);
  };

  // Activity keeps this route mounted but hidden after sign-in; never leave secrets in it.
  useLayoutEffect(() => clearCredentials, []);

  const reset = () => {
    setStep("credentials");
    setCode("");
    setError(locked ? "Too many attempts. Sign-in is locked for 15 minutes. Contact your administrator." : null);
  };

  const submitCredentials = async (event: FormEvent) => {
    event.preventDefault();
    const problem = emailError(email) ?? (password.length < 8 ? "Password must be at least 8 characters." : null);
    setError(problem);
    if (problem) return;
    if (!LIVE_CONFIGURED) {
      setStep("code");
      return;
    }
    // Live backend: Supabase Auth checks the password (and rate-limits); the role comes from the user's profile.
    if (locked) {
      setError("Too many attempts. Sign-in is locked for 15 minutes. Contact your administrator.");
      return;
    }
    setBusy(true);
    try {
      const profile = await liveSignIn(email.trim(), password);
      clearCredentials();
      setAttempts(0);
      signIn(LIVE_ROLES[profile.role].uiRole);
      router.replace(ROUTES.command);
    } catch (failure) {
      const message = failure instanceof Error ? failure.message : "Sign-in failed.";
      // Only wrong credentials count toward the lockout; an outage or an unprovisioned account does not.
      // This is a UI courtesy: Supabase Auth's server-side rate limit is the real control.
      if (!(failure instanceof AuthRejected)) {
        setError(message);
        return;
      }
      const next = attempts + 1;
      setAttempts(next);
      setPassword("");
      if (next >= MAX_ATTEMPTS) {
        setError("Too many attempts. Sign-in is locked for 15 minutes. Contact your administrator.");
        lockTimer.current = setTimeout(() => {
          setAttempts(0);
          setError(null);
        }, LOCK_MS);
      } else {
        setError(`${message} ${MAX_ATTEMPTS - next} attempt(s) left.`);
      }
    } finally {
      setBusy(false);
    }
  };

  const submitCode = (event: FormEvent) => {
    event.preventDefault();
    if (locked) return;
    if (code === DEMO_CODE) {
      clearCredentials();
      setAttempts(0);
      signIn();
      router.replace(ROUTES.command);
      return;
    }
    const next = attempts + 1;
    setAttempts(next);
    setCode("");
    setError(next >= MAX_ATTEMPTS ? "Too many attempts. Sign-in is locked for 15 minutes. Contact your administrator." : `That code is not valid. ${MAX_ATTEMPTS - next} attempt(s) left.`);
  };

  return (
    <div className="flex w-full max-w-[420px] flex-col gap-4 rounded-[14px] border border-line bg-white p-8 shadow-[0_1px_2px_rgba(14,26,43,0.04)]">
      {step === "credentials" ? (
        <form className="flex flex-col gap-4" onSubmit={submitCredentials} noValidate>
          <div>
            <h2 className="text-2xl font-semibold">Sign in</h2>
            <p className="mt-1.5 text-[13.5px] text-muted">Lakeview Community Bank · synthetic tenant</p>
          </div>
          <Button
            variant="ghost"
            onClick={() => {
              // The SSO path is the seeded demo; drop any leftover live session so data stays consistent.
              void liveSignOut();
              setEmail("");
              setError(null);
              setStep("code");
            }}
          >
            Continue with company SSO
          </Button>
          <div className="text-center text-xs text-muted">or use email</div>
          <Field label="Work email">
            {(props) => <TextInput {...props} type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@bank.com" />}
          </Field>
          <Field label="Password">
            {(props) => <TextInput {...props} type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="At least 8 characters" />}
          </Field>
          {error ? (
            <Callout tone="bad" role="alert">
              {error}
            </Callout>
          ) : null}
          <Button type="submit" disabled={busy}>
            {busy ? "Signing in" : "Continue"}
          </Button>
          <p className="text-center text-xs text-muted">
            {LIVE_CONFIGURED ? "Email sign-in uses your live backend account. Company SSO opens the seeded demo." : "Prototype sign-in on seeded demo data."}
          </p>
        </form>
      ) : (
        <form className="flex flex-col gap-4" onSubmit={submitCode} noValidate>
          <div>
            <h2 className="text-2xl font-semibold">Enter your code</h2>
            <p className="mt-1.5 text-[13.5px] text-muted">
              We sent a 6-digit code to {email.trim() || "your identity provider"}. Prototype code: {DEMO_CODE}.
            </p>
          </div>
          <Field label="Verification code">
            {(props) => (
              <TextInput
                {...props}
                inputMode="numeric"
                autoComplete="one-time-code"
                maxLength={6}
                disabled={locked}
                value={code}
                onChange={(e) => {
                  setCode(e.target.value.replace(/\D/g, "").slice(0, 6));
                  if (!locked) setError(null);
                }}
                placeholder="6 digits"
                className="text-xl tracking-[0.3em]"
              />
            )}
          </Field>
          {error ? (
            <Callout tone="bad" role="alert">
              {error}
            </Callout>
          ) : null}
          <Button type="submit" disabled={locked}>
            Verify and sign in
          </Button>
          <Button variant="ghost" onClick={reset}>
            Use a different account
          </Button>
        </form>
      )}
    </div>
  );
}
