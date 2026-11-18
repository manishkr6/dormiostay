import { useEffect } from 'react';

type Json = string | number | boolean | null | Json[] | { [key: string]: Json };
type Props = {
  title: string;
  description: string;
  url?: string;
  image?: string;
  type?: string;
  jsonLd?: Record<string, Json>;
  noIndex?: boolean;
};
export function PageHead({
  title,
  description,
  url,
  image,
  type = 'website',
  jsonLd,
  noIndex = false,
}: Props) {
  const structured = jsonLd ? JSON.stringify(jsonLd).replace(/</g, '\\u003c') : undefined;
  useEffect(() => {
    const previousTitle = document.title;
    document.title = `${title} | DormioStay`;
    const cleanups: (() => void)[] = [];
    const setMeta = (attribute: 'name' | 'property', name: string, content: string) => {
      const existing = document.head.querySelector<HTMLMetaElement>(`meta[${attribute}="${name}"]`);
      const element = existing ?? document.createElement('meta');
      const previous = element.getAttribute('content');
      element.setAttribute(attribute, name);
      element.content = content;
      if (!existing) document.head.append(element);
      cleanups.push(() => {
        if (!existing) element.remove();
        else if (previous === null) element.removeAttribute('content');
        else element.content = previous;
      });
    };
    setMeta('name', 'description', description);
    setMeta('name', 'robots', noIndex ? 'noindex, nofollow' : 'index, follow');
    setMeta('property', 'og:title', `${title} | DormioStay`);
    setMeta('property', 'og:description', description);
    setMeta('property', 'og:type', type);
    setMeta('property', 'og:site_name', 'DormioStay');
    if (url) setMeta('property', 'og:url', url);
    if (image) setMeta('property', 'og:image', image);
    if (structured) {
      const script = document.createElement('script');
      script.type = 'application/ld+json';
      script.textContent = structured;
      document.head.append(script);
      cleanups.push(() => script.remove());
    }
    return () => {
      document.title = previousTitle;
      cleanups.forEach((cleanup) => cleanup());
    };
  }, [title, description, url, image, type, structured, noIndex]);
  return null;
}
