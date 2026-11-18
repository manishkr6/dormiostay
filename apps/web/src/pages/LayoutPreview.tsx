import { Link } from 'react-router';
import { PageHead } from '../components/seo/PageHead';
import { Card } from '../components/ui/card';
import { Badge } from '../components/ui/badge';
export function LayoutPreview({ name }: { name: string }) {
  return (
    <>
      <PageHead
        title={`${name} layout preview`}
        description="DormioStay shared layout foundation."
        noIndex
      />
      <section className="preview-page">
        <Badge tone="brand">Layout preview</Badge>
        <h1>{name} workspace</h1>
        <p className="lede">A shared foundation for what comes next.</p>
        <Card>
          <h2>{name} layout is ready</h2>
          <p>
            This preview contains no account data. Authentication, permissions and product screens
            will be connected in their feature tickets.
          </p>
          <Link className="text-link" to="/showcase">
            Explore the component library →
          </Link>
        </Card>
      </section>
    </>
  );
}
