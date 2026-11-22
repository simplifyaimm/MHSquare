import { useEffect, useState } from "react";
import { CheckCircle2, CircleAlert, ExternalLink, KeyRound, Loader2, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import {
  useLLMSettings,
  useSaveBudget,
  useSaveLLMSettings,
  useTestModel,
  type BudgetView,
  type ModelView,
  type ProviderView,
} from "@/lib/llmSettings";
import { cn } from "@/lib/utils";

export function SettingsPage() {
  // After a save, LiteLLM notices the file change (≤3 s) and reloads. Show "applying" for at least
  // 8 s and then until the proxy reports healthy again (max 2 min).
  const [restarting, setRestarting] = useState(false);
  const [minElapsed, setMinElapsed] = useState(true);
  const { data, isLoading, error } = useLLMSettings(restarting);

  useEffect(() => {
    if (!restarting) return;
    const min = setTimeout(() => setMinElapsed(true), 8_000);
    const max = setTimeout(() => setRestarting(false), 120_000);
    return () => {
      clearTimeout(min);
      clearTimeout(max);
    };
  }, [restarting]);

  if (restarting && minElapsed && data?.litellm === "ok") setRestarting(false);

  const onSaved = () => {
    setMinElapsed(false);
    setRestarting(true);
  };

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
        <p className="mt-1 text-sm text-muted">
          Keys are stored only in <code className="font-mono text-text">local/llmconfig.env</code> on this
          machine (gitignored). They are write-only here: mhsquare never shows them again.
        </p>
      </div>

      {isLoading && <div className="text-muted">Loading…</div>}
      {error && <div className="text-danger">{error.message}</div>}

      {data && (
        <>
          <ProxyStatus status={data.litellm} restarting={restarting} />
          <Card>
            <CardHeader>
              <CardTitle>AI providers</CardTitle>
              <p className="text-sm text-muted">API keys for the providers your models use.</p>
            </CardHeader>
            <CardContent className="flex flex-col divide-y divide-border">
              {data.providers.map((p) => (
                <ProviderRow key={p.id} provider={p} onSaved={onSaved} />
              ))}
            </CardContent>
          </Card>

          <ModelsCard models={data.models} onSaved={onSaved} budgetReady={data.budget.configured} />
          <BudgetCard budget={data.budget} />
        </>
      )}
    </div>
  );
}

function ProxyStatus({ status, restarting }: { status: string; restarting: boolean }) {
  const ok = status === "ok" && !restarting;
  return (
    <div
      className={cn(
        "flex items-center gap-2 rounded-lg border px-4 py-2.5 text-sm",
        ok ? "border-success/30 text-success" : "border-accent-2/30 text-accent-2",
      )}
    >
      {ok ? <CheckCircle2 size={16} /> : <Loader2 size={16} className="animate-spin" />}
      {ok
        ? "LiteLLM proxy is running with the current settings."
        : restarting
          ? "Applying changes — LiteLLM is reloading (takes a few seconds)…"
          : "LiteLLM proxy is unreachable or starting…"}
    </div>
  );
}

function ProviderRow({ provider, onSaved }: { provider: ProviderView; onSaved: () => void }) {
  const save = useSaveLLMSettings();
  const [value, setValue] = useState("");
  const [editing, setEditing] = useState(false);

  const submit = (v: string | null) =>
    save.mutate(
      { provider_keys: { [provider.id]: v } },
      {
        onSuccess: () => {
          setValue("");
          setEditing(false);
          onSaved();
        },
      },
    );

  return (
    <div className="flex flex-col gap-3 py-4 first:pt-0 last:pb-0">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <KeyRound size={16} className="text-muted" />
          <span className="font-medium">{provider.name}</span>
          <a
            href={provider.docs}
            target="_blank"
            rel="noreferrer"
            className="text-muted hover:text-text"
            aria-label={`Get a ${provider.name} key`}
          >
            <ExternalLink size={14} />
          </a>
        </div>
        {provider.configured ? (
          <span className="font-mono text-xs text-success">configured {provider.hint}</span>
        ) : (
          <span className="font-mono text-xs text-muted">not set</span>
        )}
      </div>

      {editing || !provider.configured ? (
        <form
          className="flex flex-col gap-2 sm:flex-row"
          onSubmit={(e) => {
            e.preventDefault();
            if (value.trim()) submit(value.trim());
          }}
        >
          <Input
            type="password"
            autoComplete="off"
            spellCheck={false}
            placeholder={`${provider.env_var}`}
            aria-label={`${provider.name} API key`}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            className="font-mono"
          />
          <div className="flex gap-2">
            <Button type="submit" disabled={!value.trim() || save.isPending}>
              Save
            </Button>
            {provider.configured && (
              <Button type="button" variant="ghost" onClick={() => setEditing(false)}>
                Cancel
              </Button>
            )}
          </div>
        </form>
      ) : (
        <div className="flex gap-2">
          <Button variant="secondary" size="sm" onClick={() => setEditing(true)}>
            Replace key
          </Button>
          <Button variant="ghost" size="sm" onClick={() => submit(null)} disabled={save.isPending}>
            Remove
          </Button>
        </div>
      )}
      {save.error && <p className="text-sm text-danger">{save.error.message}</p>}
    </div>
  );
}

function ModelsCard({
  models,
  onSaved,
  budgetReady,
}: {
  models: ModelView[];
  onSaved: () => void;
  budgetReady: boolean;
}) {
  const save = useSaveLLMSettings();
  const [draft, setDraft] = useState<Record<string, string>>({});
  const dirty = Object.entries(draft).filter(
    ([alias, v]) => models.find((m) => m.alias === alias)?.model !== v,
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle>Models</CardTitle>
        <p className="text-sm text-muted">
          Which model each role uses, as a LiteLLM model id (<span className="font-mono">provider/model</span>
          ). Shots are routed by visual type to these roles.
        </p>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {models.map((m) => (
          <ModelRow
            key={m.alias}
            model={m}
            value={draft[m.alias] ?? m.model}
            onChange={(v) => setDraft((d) => ({ ...d, [m.alias]: v }))}
            onReset={() => {
              save.mutate({ models: { [m.alias]: null } }, { onSuccess: onSaved });
              setDraft((d) => {
                const { [m.alias]: _, ...rest } = d;
                void _;
                return rest;
              });
            }}
            budgetReady={budgetReady}
          />
        ))}
        {save.error && <p className="text-sm text-danger">{save.error.message}</p>}
        <div className="flex justify-end">
          <Button
            disabled={dirty.length === 0 || save.isPending}
            onClick={() =>
              save.mutate(
                { models: Object.fromEntries(dirty) },
                {
                  onSuccess: () => {
                    setDraft({});
                    onSaved();
                  },
                },
              )
            }
          >
            Save models{dirty.length ? ` (${dirty.length})` : ""}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function ModelRow({
  model,
  value,
  onChange,
  onReset,
  budgetReady,
}: {
  model: ModelView;
  value: string;
  onChange: (v: string) => void;
  onReset: () => void;
  budgetReady: boolean;
}) {
  const test = useTestModel();
  const listId = `models-${model.alias}`;
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <Label htmlFor={`m-${model.alias}`}>{model.label}</Label>
        <span className="font-mono text-xs text-muted">{model.alias}</span>
        {!model.provider_configured && (
          <span className="inline-flex items-center gap-1 text-xs text-warning">
            <CircleAlert size={12} /> provider key not set
          </span>
        )}
      </div>
      <div className="flex flex-col gap-2 sm:flex-row">
        <Input
          id={`m-${model.alias}`}
          list={listId}
          className="font-mono"
          spellCheck={false}
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
        <datalist id={listId}>
          {model.suggestions.map((s) => (
            <option key={s} value={s} />
          ))}
        </datalist>
        <div className="flex gap-2">
          {!model.is_default && (
            <Button
              variant="ghost"
              size="icon"
              onClick={onReset}
              aria-label="Reset to default"
              title={`Default: ${model.default}`}
            >
              <RotateCcw size={16} />
            </Button>
          )}
          {model.kind === "llm" && (
            <Button
              variant="secondary"
              onClick={() => test.mutate(model.alias)}
              disabled={!budgetReady || test.isPending}
              title={budgetReady ? "Sends a 1-token request (tiny cost)" : "Set a daily budget first"}
            >
              {test.isPending ? <Loader2 size={16} className="animate-spin" /> : "Test"}
            </Button>
          )}
        </div>
      </div>
      {test.data && (
        <p className={cn("text-xs", test.data.ok ? "text-success" : "text-danger")}>
          {test.data.ok ? "Works ✓" : `Failed (${test.data.status}): ${test.data.message}`}
        </p>
      )}
      {test.error && <p className="text-xs text-danger">{test.error.message}</p>}
    </div>
  );
}

function BudgetCard({ budget }: { budget: BudgetView }) {
  const save = useSaveBudget();
  const [value, setValue] = useState("");
  const limit = budget.max_budget ?? null;
  const spend = budget.spend ?? 0;
  const pct = limit ? Math.min(100, (spend / limit) * 100) : 0;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Daily budget</CardTitle>
        <p className="text-sm text-muted">
          mhsquare calls AI through its own LiteLLM key with a daily spend limit. When it runs out, generation
          pauses until the next reset.
        </p>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {budget.configured && !budget.invalid && limit != null && (
          <div className="flex flex-col gap-2">
            <div className="flex justify-between font-mono text-sm">
              <span>
                ${spend.toFixed(2)} / ${limit.toFixed(2)}
              </span>
              {budget.budget_reset_at && (
                <span className="text-muted">resets {new Date(budget.budget_reset_at).toLocaleString()}</span>
              )}
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-surface-2">
              <div
                className={cn("h-full rounded-full", pct >= 90 ? "bg-warning" : "bg-accent-2")}
                style={{ width: `${pct}%` }}
              />
            </div>
          </div>
        )}
        {budget.invalid && (
          <p className="text-sm text-warning">
            The stored mhsquare key is no longer valid. Set a budget to create a new one.
          </p>
        )}
        {budget.unavailable && <p className="text-sm text-muted">LiteLLM is unreachable; budget unknown.</p>}
        <form
          className="flex flex-col gap-2 sm:flex-row sm:items-end"
          onSubmit={(e) => {
            e.preventDefault();
            const n = Number(value);
            if (n > 0) save.mutate(n, { onSuccess: () => setValue("") });
          }}
        >
          <div className="flex flex-1 flex-col gap-1.5">
            <Label htmlFor="budget">Daily limit (USD)</Label>
            <Input
              id="budget"
              type="number"
              min="0.01"
              step="0.01"
              inputMode="decimal"
              placeholder={limit != null ? String(limit) : "5.00"}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              className="font-mono"
            />
          </div>
          <Button type="submit" disabled={!(Number(value) > 0) || save.isPending}>
            {budget.configured && !budget.invalid ? "Update budget" : "Create mhsquare key"}
          </Button>
        </form>
        {save.error && <p className="text-sm text-danger">{save.error.message}</p>}
      </CardContent>
    </Card>
  );
}
