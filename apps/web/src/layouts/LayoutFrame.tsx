import { useRef, useState } from 'react';
import { Link, NavLink, Outlet, useLocation } from 'react-router';
import { House, Menu, ArrowUpRight } from 'lucide-react';
import { Drawer } from '../components/ui/drawer';
import { Button } from '../components/ui/button';

const previews = [
  { to: '/showcase', label: 'UI kit' },
  { to: '/auth', label: 'Auth' },
  { to: '/tenant', label: 'Tenant' },
  { to: '/owner', label: 'Owner' },
  { to: '/admin', label: 'Admin' },
];
export function LayoutFrame({ kind }: { kind: 'Public' | 'Auth' | 'Tenant' | 'Owner' | 'Admin' }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const { pathname } = useLocation();
  const navigating = useRef(false);
  const dashboard = kind === 'Owner' || kind === 'Admin';
  const navigation = (
    <nav aria-label="Foundation previews" className="preview-nav">
      {previews.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          onClick={(event) => {
            if (
              event.button !== 0 ||
              event.metaKey ||
              event.ctrlKey ||
              event.shiftKey ||
              event.altKey
            )
              return;
            navigating.current = menuOpen && item.to !== pathname;
            setMenuOpen(false);
          }}
        >
          {item.label}
        </NavLink>
      ))}
    </nav>
  );
  return (
    <div className={`layout layout-${kind.toLowerCase()}`}>
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <header className="site-header">
        <div className="header-inner">
          <Link className="brand" to="/" aria-label="DormioStay home">
            <span className="brand-icon">
              <House aria-hidden="true" />
            </span>
            DormioStay
            <span className="brand-dot" aria-hidden="true">
              .
            </span>
          </Link>
          <div className="desktop-nav">{navigation}</div>
          <span className="header-note">Foundation / 04</span>
          <div className="mobile-menu">
            <Drawer
              title="Layout previews"
              description="Explore the DormioStay frontend foundation."
              open={menuOpen}
              onOpenChange={setMenuOpen}
              onCloseAutoFocus={(event) => {
                // A route change focuses the destination main. Escape and ordinary
                // dismissal still return focus to the navigation trigger.
                if (navigating.current) event.preventDefault();
                navigating.current = false;
              }}
              trigger={
                <Button variant="icon" aria-label="Open navigation">
                  <Menu aria-hidden="true" />
                </Button>
              }
            >
              {navigation}
            </Drawer>
          </div>
        </div>
      </header>
      <div className={dashboard ? 'dashboard-frame' : undefined}>
        {dashboard && (
          <aside className="sidebar">
            <p className="eyebrow">{kind} workspace</p>
            <h2>
              A place for
              <br />
              everything.
            </h2>
            <p>Shared layout preview</p>
            {navigation}
            <span className="sidebar-note">
              DormioStay foundation
              <ArrowUpRight aria-hidden="true" />
            </span>
          </aside>
        )}
        <main
          id="main-content"
          tabIndex={-1}
          className={`page-container ${kind === 'Auth' ? 'auth-container' : ''}`}
        >
          <Outlet />
        </main>
      </div>
      <footer className="site-footer">
        <div className="footer-inner">
          <div>
            <span className="footer-brand">DormioStay</span>
            <p>Find your stay. Live with confidence.</p>
          </div>
          <a href="mailto:dormiostay@gmail.com">dormiostay@gmail.com</a>
          <span>Frontend foundation</span>
        </div>
      </footer>
    </div>
  );
}
