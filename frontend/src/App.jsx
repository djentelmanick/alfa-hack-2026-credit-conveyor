import { Component, useEffect } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth/AuthContext.jsx";
import { AnalystLayout, ClientLayout } from "./layout/Shell.jsx";
import { LoginPage, RegisterPage } from "./pages/AuthPages.jsx";
import { HomePage } from "./pages/HomePage.jsx";
import { ApplyPage } from "./pages/ApplyPage.jsx";
import { ApplicationPage } from "./pages/ApplicationPage.jsx";
import { ContractsPage } from "./pages/ContractsPage.jsx";
import { ContractPage } from "./pages/ContractPage.jsx";
import { QueuePage } from "./pages/analyst/QueuePage.jsx";
import { ReportPage } from "./pages/analyst/ReportPage.jsx";
import { NotFoundPage } from "./pages/StatePages.jsx";

class ErrorBoundary extends Component {
  state = { error: null };
  static getDerivedStateFromError(error) { return { error }; }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <main className="page">
        <div className="ck-card"><div className="ck-empty">
          <h2 className="h2">Страница не открылась</h2>
          <p className="ck-empty__text">{String(this.state.error.message || this.state.error)}. Обновите страницу</p>
        </div></div>
      </main>
    );
  }
}

function RequireRole({ role, children }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) return null;
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  if (user.role !== role) return <Navigate to={user.role === "ANALYST" ? "/analyst" : "/"} replace />;
  return children;
}

function Logout() {
  const { logout } = useAuth();
  useEffect(() => { logout(); }, [logout]);
  return <Navigate to="/login" replace />;
}

export default function App() {
  return (
    <ErrorBoundary>
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/register" element={<RegisterPage />} />
          <Route path="/logout" element={<Logout />} />
          <Route element={<RequireRole role="CLIENT"><ClientLayout /></RequireRole>}>
            <Route path="/" element={<HomePage />} />
            <Route path="/apply" element={<ApplyPage />} />
            <Route path="/applications/:id" element={<ApplicationPage />} />
            <Route path="/contracts" element={<ContractsPage />} />
            <Route path="/contracts/:id" element={<ContractPage />} />
            <Route path="*" element={<NotFoundPage />} />
          </Route>
          <Route path="/analyst" element={<RequireRole role="ANALYST"><AnalystLayout /></RequireRole>}>
            <Route index element={<QueuePage />} />
            <Route path="applications/:id" element={<ReportPage />} />
            <Route path="contracts" element={<ContractsPage analyst />} />
            <Route path="contracts/:id" element={<ContractPage analyst />} />
            <Route path="*" element={<NotFoundPage home="/analyst" />} />
          </Route>
        </Routes>
      </AuthProvider>
    </BrowserRouter>
    </ErrorBoundary>
  );
}
