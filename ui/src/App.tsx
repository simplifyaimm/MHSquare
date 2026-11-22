import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { RequireAuth } from "./lib/auth";
import { LoginPage } from "./pages/Login";
import { PlaceholderPage } from "./pages/Placeholder";
import { SettingsPage } from "./pages/Settings";

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <RequireAuth>
            <AppShell />
          </RequireAuth>
        }
      >
        <Route path="/projects" element={<PlaceholderPage title="Projects" phase="phase 2" />} />
        <Route path="/usage" element={<PlaceholderPage title="Usage" phase="phase 8" />} />
        <Route path="/settings" element={<SettingsPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/projects" replace />} />
    </Routes>
  );
}
