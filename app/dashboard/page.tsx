"use client";

import { useEffect, useMemo, useState } from "react";
import { Badge, Card, Metric, Text, Title } from "@tremor/react";
import { createClient, SupabaseClient } from "@supabase/supabase-js";

type PRAlert = {
  id: string;
  pr_title: string;
  author_username: string;
  risk_status: "green" | "red";
  predicted_merge_hours: number;
};

const demoAlerts: PRAlert[] = [
  {
    id: "demo-1",
    pr_title: "Refactor queue worker",
    author_username: "alice",
    risk_status: "red",
    predicted_merge_hours: 38.2,
  },
  {
    id: "demo-2",
    pr_title: "Trim CI pipeline time",
    author_username: "morgan",
    risk_status: "green",
    predicted_merge_hours: 14.8,
  },
  {
    id: "demo-3",
    pr_title: "Add metrics export for billing",
    author_username: "nina",
    risk_status: "red",
    predicted_merge_hours: 41.6,
  },
];

export default function DashboardPage() {
  const [alerts, setAlerts] = useState<PRAlert[]>(demoAlerts);

  useEffect(() => {
    const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";
    const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "";

    if (!supabaseUrl || !supabaseAnonKey) {
      return;
    }

    const supabase: SupabaseClient = createClient(supabaseUrl, supabaseAnonKey, {
      realtime: { params: { eventsPerSecond: 10 } },
    });

    const channel = supabase
      .channel("pull_requests_dashboard")
      .on(
        "postgres_changes",
        { event: "INSERT", schema: "public", table: "pull_requests" },
        (payload) => {
          const record = payload.new as any;
          const nextAlert: PRAlert = {
            id: String(record.id ?? record.pr_number ?? crypto.randomUUID()),
            pr_title: record.pr_title ?? `PR #${record.pr_number ?? "unknown"}`,
            author_username: record.author_username ?? "unknown",
            risk_status: record.risk_status === "red" ? "red" : "green",
            predicted_merge_hours: Number(record.predicted_merge_hours ?? 0),
          };

          setAlerts((current) => [nextAlert, ...current].slice(0, 6));
        },
      )
      .subscribe();

    return () => {
      void supabase.removeChannel(channel);
    };
  }, []);

  const summary = useMemo(() => {
    const green = alerts.filter((alert) => alert.risk_status === "green").length;
    const red = alerts.filter((alert) => alert.risk_status === "red").length;
    return { green, red };
  }, [alerts]);

  return (
    <main className="min-h-screen bg-slate-950 p-8 text-slate-100">
      <div className="mx-auto max-w-6xl space-y-6">
        <header className="flex items-center justify-between">
          <div>
            <Text className="text-slate-400">GitStream</Text>
            <Title className="text-3xl font-semibold">Engineering workflow dashboard</Title>
          </div>
          <Badge color={summary.red > 0 ? "rose" : "emerald"}>
            {summary.red} high-risk PRs
          </Badge>
        </header>

        <section className="grid gap-4 md:grid-cols-3">
          <Card>
            <Text>New PR alerts</Text>
            <Metric>{alerts.length}</Metric>
          </Card>
          <Card>
            <Text>Green</Text>
            <Metric>{summary.green}</Metric>
          </Card>
          <Card>
            <Text>Red</Text>
            <Metric>{summary.red}</Metric>
          </Card>
        </section>

        <section className="space-y-4">
          {alerts.length === 0 ? (
            <Card>
              <Text className="text-slate-400">No PR alerts yet. Configure a repository and wait for the next webhook event.</Text>
            </Card>
          ) : (
            alerts.map((alert) => (
              <Card key={alert.id} className="border border-slate-800 bg-slate-900/80">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <Text className="text-xs uppercase tracking-wide text-slate-400">New PR Alert</Text>
                    <Title className="mt-2 text-xl">{alert.pr_title}</Title>
                    <Text className="mt-2 text-slate-300">Author: {alert.author_username}</Text>
                  </div>
                  <Badge color={alert.risk_status === "red" ? "rose" : "emerald"}>
                    {alert.risk_status === "red" ? "Red >24h" : "Green <24h"}
                  </Badge>
                </div>

                <div className="mt-4 flex items-center justify-between border-t border-slate-800 pt-4">
                  <Text className="text-slate-400">Predicted merge delay</Text>
                  <Metric>{alert.predicted_merge_hours.toFixed(1)}h</Metric>
                </div>
              </Card>
            ))
          )}
        </section>
      </div>
    </main>
  );
}
