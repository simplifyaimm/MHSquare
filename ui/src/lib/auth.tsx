import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Navigate, useLocation } from "react-router-dom";
import type { ReactNode } from "react";
import { api, ApiError, type Me } from "./api";

export const meKey = ["auth", "me"] as const;

export function useMe() {
  return useQuery({
    queryKey: meKey,
    queryFn: async () => {
      try {
        return await api<Me>("/auth/me");
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return null;
        throw e;
      }
    },
    staleTime: 60_000,
  });
}

export function useLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { email: string; password: string }) =>
      api<Me>("/auth/login", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: (me) => qc.setQueryData(meKey, me),
  });
}

export function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api<void>("/auth/logout", { method: "POST" }),
    onSettled: () => {
      qc.setQueryData(meKey, null);
      qc.clear();
    },
  });
}

export function RequireAuth({ children }: { children: ReactNode }) {
  const { data, isLoading } = useMe();
  const location = useLocation();
  if (isLoading) {
    return <div className="grid h-full place-items-center text-muted">Loading…</div>;
  }
  if (!data) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return <>{children}</>;
}
