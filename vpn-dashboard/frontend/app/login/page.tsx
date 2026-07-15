"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { Activity } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.login(username, password);
      router.push("/");
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 429
          ? "Слишком много попыток — подождите пару минут"
          : "Неверный логин или пароль",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center p-4">
      <Card className="w-full max-w-sm p-6">
        <div className="mb-5 flex items-center gap-2">
          <Activity className="h-5 w-5 text-accent" />
          <h1 className="text-base font-semibold text-ink">VPN Analytics</h1>
        </div>
        <form onSubmit={onSubmit} className="space-y-3">
          <Input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="Логин"
            autoComplete="username"
            autoFocus
          />
          <Input
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Пароль"
            type="password"
            autoComplete="current-password"
          />
          {error ? <p className="text-xs text-critical">{error}</p> : null}
          <Button className="w-full" disabled={busy || !username || !password}>
            {busy ? "Входим…" : "Войти"}
          </Button>
        </form>
      </Card>
    </main>
  );
}
