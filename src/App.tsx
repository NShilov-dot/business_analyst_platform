import { lazy, Suspense } from 'react'
import { Routes, Route, Outlet, Navigate } from 'react-router-dom'
import { LoadingSpinner } from './components/LoadingSpinner'
import { ErrorBoundary } from './components/ErrorBoundary'
import { AppLayout } from './components/layout/AppLayout'
import { AuthProvider } from './auth/AuthProvider'

// Per-route code splitting — each page becomes its own JS chunk
const DashboardPage    = lazy(() => import('./pages/DashboardPage'))
const BoardPage        = lazy(() => import('./pages/BoardPage'))
const TicketDetailPage = lazy(() => import('./pages/TicketDetailPage'))
const IntakeChatPage   = lazy(() => import('./pages/IntakeChatPage'))
const DocumentsPage    = lazy(() => import('./pages/DocumentsPage'))
const TasksPage        = lazy(() => import('./pages/TasksPage'))
const NewTaskPage      = lazy(() => import('./pages/NewTaskPage'))
const TaskDetailPage   = lazy(() => import('./pages/TaskDetailPage'))
const ProfilePage      = lazy(() => import('./pages/ProfilePage'))
const SignupPage       = lazy(() => import('./pages/SignupPage'))

// Authenticated shell: the AuthProvider gate + the app chrome (sidebar/layout).
// Everything nested under it requires a live session; public pages (signup) sit
// OUTSIDE it so they render without bouncing through the OIDC login. Ticket data
// now comes from /v1/tickets via TanStack Query — no shared provider needed.
function AuthedShell() {
  return (
    <AuthProvider>
      <AppLayout>
        <Outlet />
      </AppLayout>
    </AuthProvider>
  )
}

export default function App() {
  return (
    <ErrorBoundary>
      <Suspense fallback={<LoadingSpinner />}>
        <Routes>
          {/* Public — no session required */}
          <Route path="/signup" element={<SignupPage />} />

          {/* Authenticated app */}
          <Route element={<AuthedShell />}>
            <Route path="/"            element={<DashboardPage />} />
            <Route path="/board"       element={<BoardPage />} />
            <Route path="/tickets/:id" element={<TicketDetailPage />} />
            <Route path="/intake"      element={<IntakeChatPage />} />
            <Route path="/intake/chat" element={<Navigate to="/intake" replace />} />
            <Route path="/documents"   element={<DocumentsPage />} />
            <Route path="/tasks"       element={<TasksPage />} />
            <Route path="/tasks/new"   element={<NewTaskPage />} />
            <Route path="/tasks/:id"   element={<TaskDetailPage />} />
            <Route path="/profile"     element={<ProfilePage />} />
          </Route>
        </Routes>
      </Suspense>
    </ErrorBoundary>
  )
}
