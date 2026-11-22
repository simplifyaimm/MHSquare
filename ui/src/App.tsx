import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { RequireAuth } from "./lib/auth";
import { LoginPage } from "./pages/Login";
import { BrandPage } from "./pages/Brand";
import { CoversPage } from "./pages/Covers";
import { PlaceholderPage } from "./pages/Placeholder";
import { ProjectDetailPage } from "./pages/ProjectDetail";
import { ProjectSetupPage } from "./pages/ProjectSetup";
import { ProjectsPage } from "./pages/Projects";
import { SettingsPage } from "./pages/Settings";
import { TopicsPage } from "./pages/Topics";

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
        <Route path="/projects" element={<ProjectsPage />} />
        <Route path="/projects/:id" element={<ProjectDetailPage />} />
        <Route path="/projects/:id/setup" element={<ProjectSetupPage />} />
        <Route path="/projects/:id/covers" element={<CoversPage />} />
        <Route path="/topics" element={<TopicsPage />} />
        <Route path="/brand" element={<BrandPage />} />
        <Route path="/usage" element={<PlaceholderPage title="Usage" phase="phase 8" />} />
        <Route path="/settings" element={<SettingsPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/projects" replace />} />
    </Routes>
  );
}
