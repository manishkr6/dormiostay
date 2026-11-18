import { useEffect, useRef } from 'react';
import { Route, Routes, useLocation } from 'react-router';
import { PublicLayout } from '../layouts/PublicLayout';
import { AuthLayout } from '../layouts/AuthLayout';
import { TenantLayout } from '../layouts/TenantLayout';
import { OwnerLayout } from '../layouts/OwnerLayout';
import { AdminLayout } from '../layouts/AdminLayout';
import { Showcase } from '../pages/public/Showcase';
import { LayoutPreview } from '../pages/LayoutPreview';
import { NotFound } from '../pages/NotFound';

// Layout previews contain no protected data. Real role guards arrive with auth tickets.
export function AppRoutes() {
  const { pathname } = useLocation();
  const lastPath = useRef(pathname);
  // Keep route focus above the layouts, which remount when their route changes.
  // Leave initial page focus and in-page anchor navigation to the browser.
  useEffect(() => {
    if (lastPath.current !== pathname) {
      document.getElementById('main-content')?.focus();
      lastPath.current = pathname;
    }
  }, [pathname]);
  return (
    <Routes>
      <Route element={<PublicLayout />}>
        <Route index element={<Showcase />} />
        <Route path="showcase" element={<Showcase />} />
        <Route path="*" element={<NotFound />} />
      </Route>
      <Route path="auth" element={<AuthLayout />}>
        <Route index element={<LayoutPreview name="Auth" />} />
      </Route>
      <Route path="tenant" element={<TenantLayout />}>
        <Route index element={<LayoutPreview name="Tenant" />} />
      </Route>
      <Route path="owner" element={<OwnerLayout />}>
        <Route index element={<LayoutPreview name="Owner" />} />
      </Route>
      <Route path="admin" element={<AdminLayout />}>
        <Route index element={<LayoutPreview name="Admin" />} />
      </Route>
    </Routes>
  );
}
