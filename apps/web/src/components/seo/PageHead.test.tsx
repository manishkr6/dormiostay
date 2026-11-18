import { render } from '@testing-library/react';
import { expect, it } from 'vitest';
import { PageHead } from './PageHead';
it('updates and cleans metadata and safely serializes JSON-LD on navigation', () => {
  const oldTitle = document.title;
  const view = render(
    <PageHead
      title="Example"
      description="Example description"
      url="https://example.invalid/example"
      image="https://example.invalid/image.jpg"
      jsonLd={{ name: '</script><script>alert(1)</script>' }}
    />,
  );
  expect(document.title).toBe('Example | DormioStay');
  expect(document.querySelector('meta[property="og:title"]')).toHaveAttribute(
    'content',
    'Example | DormioStay',
  );
  expect(document.querySelector('meta[property="og:description"]')).toHaveAttribute(
    'content',
    'Example description',
  );
  expect(document.querySelector('meta[property="og:url"]')).toHaveAttribute(
    'content',
    'https://example.invalid/example',
  );
  expect(document.querySelector('meta[property="og:type"]')).toHaveAttribute('content', 'website');
  expect(document.querySelector('script[type="application/ld+json"]')?.textContent).not.toContain(
    '</script>',
  );
  view.rerender(<PageHead title="Next" description="Next description" noIndex />);
  expect(document.title).toBe('Next | DormioStay');
  expect(document.querySelector('meta[name="robots"]')).toHaveAttribute(
    'content',
    'noindex, nofollow',
  );
  expect(document.querySelectorAll('meta[name="description"]')).toHaveLength(1);
  expect(document.querySelector('meta[property="og:image"]')).toBeNull();
  expect(document.querySelector('script[type="application/ld+json"]')).toBeNull();
  view.unmount();
  expect(document.title).toBe(oldTitle);
  expect(document.querySelector('meta[property="og:title"]')).toBeNull();
});
