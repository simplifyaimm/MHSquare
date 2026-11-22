import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { BarChart3, FolderKanban, LogOut, Settings, WifiOff } from "lucide-react";
import { useEffect, useState } from "react";
import { Logo, LogoMark } from "./Logo";
import { Button } from "./ui/button";
import { useLogout, useMe } from "@/lib/auth";
import { cn } from "@/lib/utils";

const nav = [
  { to: "/projects", label: "Projects", icon: FolderKanban },
  { to: "/usage", label: "Usage", icon: BarChart3 },
  { to: "/settings", label: "Settings", icon: Settings },
];

function useOnline() {
  const [online, setOnline] = useState(navigator.onLine);
  useEffect(() => {
    const on = () => setOnline(true);
    const off = () => setOnline(false);
    window.addEventListener("online", on);
    window.addEventListener("offline", off);
    return () => {
      window.removeEventListener("online", on);
      window.removeEventListener("offline", off);
    };
  }, []);
  return online;
}

export function AppShell() {
  const { data: me } = useMe();
  const logout = useLogout();
  const navigate = useNavigate();
  const online = useOnline();

  const doLogout = () => logout.mutate(undefined, { onSettled: () => navigate("/login") });

  return (
    <div className="flex h-full">
      {/* desktop sidebar */}
      <aside className="hidden w-60 shrink-0 flex-col border-r border-border bg-surface md:flex">
        <div className="p-5">
          <Logo />
        </div>
        <nav className="flex flex-1 flex-col gap-1 px-3">
          {nav.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-3 rounded-lg px-3 py-2 text-sm text-muted hover:bg-surface-2 hover:text-text",
                  isActive && "bg-surface-2 text-text",
                )
              }
            >
              <Icon size={18} />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-border p-3">
          <div className="truncate px-3 pb-2 text-xs text-muted">{me?.user.email}</div>
          <Button variant="ghost" className="w-full justify-start" onClick={doLogout}>
            <LogOut size={18} /> Log out
          </Button>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        {/* mobile top bar */}
        <header className="flex items-center justify-between border-b border-border bg-surface px-4 py-3 md:hidden">
          <span className="inline-flex items-center gap-2">
            <LogoMark size={28} />
            <span className="font-semibold">mhsquare</span>
          </span>
          <Button variant="ghost" size="icon" onClick={doLogout} aria-label="Log out">
            <LogOut size={18} />
          </Button>
        </header>

        {!online && (
          <div className="flex items-center gap-2 bg-warning/15 px-4 py-2 text-sm text-warning">
            <WifiOff size={16} /> Offline — showing cached data. Generation needs a connection.
          </div>
        )}

        <main className="flex-1 overflow-y-auto px-4 py-6 pb-24 md:px-8 md:pb-8">
          <Outlet />
        </main>

        {/* mobile bottom tab bar */}
        <nav
          className="fixed inset-x-0 bottom-0 flex border-t border-border bg-surface md:hidden"
          style={{ paddingBottom: "env(safe-area-inset-bottom, 0px)" }}
        >
          {nav.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                cn(
                  "flex flex-1 flex-col items-center gap-1 py-2.5 text-xs text-muted",
                  isActive && "text-accent",
                )
              }
            >
              <Icon size={20} />
              {label}
            </NavLink>
          ))}
        </nav>
      </div>
    </div>
  );
}
