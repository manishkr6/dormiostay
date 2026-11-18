import { Link } from 'react-router';
import { PageHead } from '../components/seo/PageHead';
import { EmptyState } from '../components/ui/empty-state';
export function NotFound() {
  return (
    <>
      <PageHead title="Page not found" description="This page could not be found." noIndex />
      <h1 className="sr-only">Page not found</h1>
      <EmptyState
        title="This page isn’t here"
        description="Check the address or return to the component library."
        action={
          <Link className="text-link" to="/showcase">
            Back to the UI kit →
          </Link>
        }
      />
    </>
  );
}
