import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";

export type ProviderView = {
  id: string;
  name: string;
  env_var: string;
  docs: string;
  configured: boolean;
  hint: string | null;
};

export type ModelView = {
  alias: string;
  label: string;
  kind: "llm" | "image" | "video";
  model: string;
  default: string;
  is_default: boolean;
  provider: string | null;
  provider_configured: boolean;
  suggestions: string[];
};

export type BudgetView = {
  configured: boolean;
  invalid?: boolean;
  unavailable?: boolean;
  spend?: number;
  max_budget?: number | null;
  budget_duration?: string | null;
  budget_reset_at?: string | null;
};

export type LLMSettings = {
  litellm: string;
  providers: ProviderView[];
  models: ModelView[];
  budget: BudgetView;
  restarting?: boolean;
};

export type LLMSettingsUpdate = {
  provider_keys?: Record<string, string | null>;
  models?: Record<string, string | null>;
};

const key = ["settings", "llm"] as const;

export function useLLMSettings(pollWhileRestarting: boolean) {
  return useQuery({
    queryKey: key,
    queryFn: () => api<LLMSettings>("/settings/llm"),
    refetchInterval: (q) => (pollWhileRestarting || q.state.data?.litellm !== "ok" ? 3000 : false),
  });
}

export function useSaveLLMSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: LLMSettingsUpdate) =>
      api<LLMSettings>("/settings/llm", { method: "PUT", body: JSON.stringify(body) }),
    onSuccess: (data) => qc.setQueryData(key, data),
  });
}

export function useSaveBudget() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (daily_usd: number) =>
      api<LLMSettings & { created: boolean }>("/settings/llm/budget", {
        method: "PUT",
        body: JSON.stringify({ daily_usd }),
      }),
    onSuccess: (data) => qc.setQueryData(key, data),
  });
}

export function useTestModel() {
  return useMutation({
    mutationFn: (alias: string) =>
      api<{ ok: boolean; status: number; message: string }>(`/settings/llm/test/${alias}`, {
        method: "POST",
      }),
  });
}
