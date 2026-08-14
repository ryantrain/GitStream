"use client";

import { useState } from "react";
import { Button, Card, Label, Text, TextInput, Title, Toggle } from "@tremor/react";

type RegistrationResponse = {
  repository: { owner: string; name: string; url: string };
  org_id: string;
  webhook_url: string;
  webhook_secret: string;
  auto_install_webhook: boolean;
  status: string;
};

const defaultForm = {
  repository_url: "https://github.com/owner/repo",
  github_token: "",
  org_id: "acme-platform",
  auto_install_webhook: false,
};

export default function RepositorySettingsPage() {
  const [form, setForm] = useState(defaultForm);
  const [result, setResult] = useState<RegistrationResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const onSubmit = async () => {
    setLoading(true);
    try {
      const response = await fetch("/api/v1/repos/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      });

      if (!response.ok) {
        throw new Error("Repository registration failed");
      }

      const payload: RegistrationResponse = await response.json();
      setResult(payload);
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="min-h-screen bg-slate-950 p-8 text-slate-100">
      <div className="mx-auto max-w-3xl space-y-6">
        <header>
          <Text className="text-slate-400">GitStream</Text>
          <Title className="text-3xl font-semibold">Repository registration</Title>
        </header>

        <Card className="space-y-5 bg-slate-900/80">
          <div className="space-y-2">
            <Label htmlFor="repository_url">Repository URL</Label>
            <TextInput
              id="repository_url"
              value={form.repository_url}
              onChange={(event) => setForm((current) => ({ ...current, repository_url: event.target.value }))}
              placeholder="https://github.com/owner/repo"
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="github_token">GitHub Personal Access Token</Label>
            <TextInput
              id="github_token"
              type="password"
              value={form.github_token}
              onChange={(event) => setForm((current) => ({ ...current, github_token: event.target.value }))}
              placeholder="Optional: used to auto-install the webhook"
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="org_id">Organization ID</Label>
            <TextInput
              id="org_id"
              value={form.org_id}
              onChange={(event) => setForm((current) => ({ ...current, org_id: event.target.value }))}
              placeholder="acme-platform"
            />
          </div>

          <div className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-950/80 p-3">
            <div>
              <Text className="font-medium">Auto-install Webhook via GitHub API</Text>
              <Text className="text-slate-400">Choose manual setup if your repo cannot call the GitHub API directly.</Text>
            </div>
            <Toggle
              checked={form.auto_install_webhook}
              onChange={(event) => setForm((current) => ({ ...current, auto_install_webhook: event.target.checked }))}
              aria-label="Toggle auto-install webhook"
            />
          </div>

          <Button onClick={onSubmit} loading={loading}>
            Register repository
          </Button>
        </Card>

        {result && (
          <Card className="space-y-4 bg-slate-900/80">
            <Title>Webhook setup</Title>

            {!result.auto_install_webhook ? (
              <>
                <div>
                  <Text className="font-medium">Webhook URL</Text>
                  <Text className="mt-2 break-all rounded-md bg-slate-950 p-3 text-sm text-emerald-300">
                    {result.webhook_url}
                  </Text>
                </div>

                <div>
                  <Text className="font-medium">Webhook Secret</Text>
                  <Text className="mt-2 break-all rounded-md bg-slate-950 p-3 text-sm text-violet-300">
                    {result.webhook_secret}
                  </Text>
                </div>
              </>
            ) : (
              <Text className="text-emerald-300">GitHub webhook created automatically using the PAT.</Text>
            )}

            <Text className="text-slate-400">
              Registered repository: {result.repository.owner}/{result.repository.name}
            </Text>
          </Card>
        )}
      </div>
    </main>
  );
}
