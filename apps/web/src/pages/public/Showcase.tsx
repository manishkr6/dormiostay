import { useState } from 'react';
import {
  ArrowUpRight,
  Heart,
  House,
  Check,
  ArrowRight,
  Layers,
  SquareMousePointer,
  SlidersHorizontal,
  PanelsTopLeft,
} from 'lucide-react';
import { Button, type ButtonVariant } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Textarea } from '../../components/ui/textarea';
import { Select } from '../../components/ui/select';
import { Checkbox } from '../../components/ui/checkbox';
import { Switch } from '../../components/ui/switch';
import { Badge } from '../../components/ui/badge';
import { Card } from '../../components/ui/card';
import { Modal } from '../../components/ui/modal';
import { Drawer } from '../../components/ui/drawer';
import { BottomSheet } from '../../components/ui/bottom-sheet';
import { Skeleton } from '../../components/ui/skeleton';
import { EmptyState } from '../../components/ui/empty-state';
import { ErrorState } from '../../components/ui/error-state';
import { Tabs } from '../../components/ui/tabs';
import { WakingBanner } from '../../components/ui/waking-banner';
import { PageHead } from '../../components/seo/PageHead';
import { useToast } from '../../lib/toast-context';

const variants: ButtonVariant[] = [
  'primary',
  'secondary',
  'ghost',
  'destructive',
  'accent',
  'icon',
];
const states = ['Default', 'Hover', 'Pressed', 'Focus', 'Disabled', 'Loading'] as const;
const palette = [
  ['primary-900', 'Deep Indigo'],
  ['primary-600', 'Royal Violet'],
  ['primary-100', 'Soft Lavender'],
  ['bg', 'Warm Ivory'],
  ['accent', 'Coral'],
  ['highlight', 'Amber'],
];
const sections = [
  { id: 'foundations', label: 'Foundations', Icon: Layers },
  { id: 'actions', label: 'Actions', Icon: SquareMousePointer },
  { id: 'forms', label: 'Form controls', Icon: SlidersHorizontal },
  { id: 'feedback', label: 'Feedback & overlays', Icon: PanelsTopLeft },
];
export function Showcase() {
  const toast = useToast();
  const [modalOpen, setModalOpen] = useState(false);
  const [emptyReset, setEmptyReset] = useState(false);
  const [retry, setRetry] = useState(false);
  return (
    <>
      <PageHead
        title="Component library"
        description="The shared design language of DormioStay: thoughtful foundations, accessible controls and honest states."
        noIndex
        url={`${window.location.origin}/showcase`}
        jsonLd={{
          '@context': 'https://schema.org',
          '@type': 'WebPage',
          name: 'DormioStay component library',
        }}
      />
      <section className="showcase-hero">
        <div>
          <div className="eyebrow">
            <span className="status-dot" />
            DORMIOSTAY DESIGN SYSTEM <span>01 / FOUNDATION</span>
          </div>
          <h1>
            Thoughtful details.
            <br />
            <em>A confident start.</em>
          </h1>
          <p className="lede">
            One shared language for every stay.
            <br />
            Explore the building blocks behind DormioStay.
          </p>
          <a className="hero-link" href="#actions">
            Explore the components <ArrowRight aria-hidden="true" />
          </a>
        </div>
        <div className="type-specimen" aria-label="Playfair Display and Inter typography preview">
          <span className="specimen-label">THE DORMIOSTAY SIGNATURE</span>
          <span className="specimen-letters">
            Aa<span>.</span>
          </span>
          <span className="specimen-caption">
            Playfair Display <span>+ Inter</span>
          </span>
          <div className="specimen-swatches" aria-hidden="true">
            {palette.slice(0, 5).map(([token]) => (
              <span key={token} style={{ background: `var(--color-${token})` }} />
            ))}
          </div>
        </div>
      </section>
      <nav className="section-nav" aria-label="Component sections">
        {sections.map(({ id, label, Icon }, index) => (
          <a href={`#${id}`} key={id}>
            <Icon aria-hidden="true" />
            <span>{label}</span>
            <span className="section-number">0{index + 1}</span>
          </a>
        ))}
      </nav>
      <section id="foundations" className="showcase-section">
        <div className="section-heading">
          <div>
            <p className="eyebrow">01 / FOUNDATIONS</p>
            <h2>Calm, with a little character.</h2>
          </div>
          <p>
            Warm surfaces. Clear hierarchy.
            <br />A violet thread that brings it together.
          </p>
        </div>
        <div className="palette-grid">
          {palette.map(([token, name]) => (
            <div key={token} className="swatch-card">
              <div className="swatch" style={{ background: `var(--color-${token})` }} />
              <strong>{name}</strong>
              <span>{token}</span>
            </div>
          ))}
        </div>
        <div className="foundation-grid">
          <Card>
            <p className="eyebrow">TYPE & HIERARCHY</p>
            <h2>Room to feel at home.</h2>
            <p>
              Playfair Display gives our headings character. Inter keeps the everyday details clear
              and easy to read.
            </p>
            <span className="type-caption">Aa Bb Cc · 0123456789 · ₹</span>
          </Card>
          <Card>
            <p className="eyebrow">STATUS BADGES</p>
            <h3>Clarity in every state</h3>
            <div className="badge-row">
              <Badge>Draft</Badge>
              <Badge tone="warning">Pending</Badge>
              <Badge tone="success">
                <Check aria-hidden="true" />
                Approved
              </Badge>
              <Badge tone="error">Rejected</Badge>
              <Badge tone="info">Changes requested</Badge>
              <Badge tone="brand">Selected</Badge>
            </div>
            <p className="field-helper">
              Labels carry meaning alongside colour. All content here is a component example.
            </p>
          </Card>
        </div>
      </section>
      <section id="actions" className="showcase-section">
        <div className="section-heading">
          <div>
            <p className="eyebrow">02 / ACTIONS</p>
            <h2>A clear next step.</h2>
          </div>
          <p>
            Every variant, in every state.
            <br />
            Focus any live control with your keyboard.
          </p>
        </div>
        <Card className="button-showcase">
          <div className="button-grid">
            <span className="eyebrow">VARIANT</span>
            {states.map((state) => (
              <span className="matrix-heading" key={state}>
                {state}
              </span>
            ))}
            {variants.map((variant) => (
              <div className="button-matrix-row" key={variant}>
                <strong>{variant === 'ghost' ? 'Tertiary / ghost' : variant}</strong>
                {states.map((state) => (
                  <div key={state}>
                    <span className="mobile-state-label">{state}</span>
                    <Button
                      variant={variant}
                      disabled={state === 'Disabled'}
                      loading={state === 'Loading'}
                      className={`preview-${state.toLowerCase()}`}
                      aria-label={variant === 'icon' ? `${state} icon example` : undefined}
                      onClick={() => toast({ message: `${variant} button example`, tone: 'info' })}
                    >
                      {variant === 'icon' ? <Heart aria-hidden="true" /> : 'Button'}
                    </Button>
                  </div>
                ))}
              </div>
            ))}
          </div>
          <div className="size-row">
            <span className="eyebrow">SIZES</span>
            <Button size="small" variant="secondary">
              Small / admin
            </Button>
            <Button size="medium" variant="secondary">
              Medium
            </Button>
            <Button>Default</Button>
            <Button size="large">
              Large
              <ArrowUpRight aria-hidden="true" />
            </Button>
          </div>
        </Card>
      </section>
      <section id="forms" className="showcase-section">
        <div className="section-heading">
          <div>
            <p className="eyebrow">03 / FORM CONTROLS</p>
            <h2>Good questions. Simple answers.</h2>
          </div>
          <p>
            Visible labels, helpful guidance
            <br />
            and room for a thoughtful response.
          </p>
        </div>
        <Card>
          <div className="form-grid">
            <Input
              label="Default input"
              placeholder="Your name"
              helper="A little context goes a long way."
            />
            <Input label="Hover input" placeholder="Hover state" className="preview-hover" />
            <Input label="Focus input" placeholder="Focus state" className="preview-focus" />
            <Input
              label="Email with error"
              type="email"
              defaultValue="name@"
              error="Enter a complete email address."
            />
            <Input label="Success input" defaultValue="Sample name" success="Looks good." />
            <Input label="Disabled input" defaultValue="Not available yet" disabled />
            <Input
              label="Password"
              type="password"
              autoComplete="new-password"
              placeholder="Enter a password"
            />
            <Input label="Phone (+91)" type="tel" prefix="+91" autoComplete="tel-national" />
            <Input label="Monthly budget (₹)" type="number" prefix="₹" min={0} />
            <Select label="Example selection" defaultValue="">
              <option value="" disabled>
                Choose an option
              </option>
              <option value="one">Option one</option>
              <option value="two">Option two</option>
            </Select>
            <Select label="Selection with error" error="Choose an option.">
              <option value="">Choose an option</option>
              <option>Option one</option>
            </Select>
            <Select label="Disabled selection" disabled>
              <option>Not available</option>
            </Select>
            <Select label="Successful selection" defaultValue="one" success="Selection looks good.">
              <option value="one">Option one</option>
              <option value="two">Option two</option>
            </Select>
            <Textarea label="A short note" placeholder="Write a little here…" maxLength={200} />
            <Textarea label="Note with error" error="Add a short note." />
            <Textarea label="Disabled note" disabled defaultValue="Not available yet" />
            <Textarea
              label="Successful note"
              defaultValue="A shared foundation."
              success="Your note looks good."
              maxLength={200}
            />
          </div>
          <div className="choice-grid">
            <div>
              <Checkbox label="Default checkbox" />
              <Checkbox label="Selected checkbox" defaultChecked />
              <Checkbox label="Disabled checkbox" disabled />
              <Checkbox label="Disabled selected checkbox" disabled defaultChecked />
            </div>
            <div>
              <Switch label="Default switch" />
              <Switch label="Enabled switch" defaultChecked />
              <Switch label="Disabled switch" disabled />
              <Switch label="Disabled enabled switch" disabled defaultChecked />
            </div>
          </div>
        </Card>
      </section>
      <section id="feedback" className="showcase-section">
        <div className="section-heading">
          <div>
            <p className="eyebrow">04 / FEEDBACK & OVERLAYS</p>
            <h2>Keep people in the picture.</h2>
          </div>
          <p>
            Waiting, starting fresh or trying again.
            <br />
            There’s always a clear way forward.
          </p>
        </div>
        <Card>
          <Tabs
            label="Feedback examples"
            items={[
              {
                value: 'states',
                label: 'Page states',
                content: (
                  <>
                    <div className="state-grid">
                      <div>
                        <p className="eyebrow">LOADING</p>
                        <Skeleton label="Loading example card" className="skeleton-cover" />
                        <Skeleton label="Loading example title" />
                        <Skeleton label="Loading example description" className="skeleton-short" />
                      </div>
                      <EmptyState
                        title={emptyReset ? 'Example reset' : 'Nothing here just yet'}
                        description="When a list is empty, offer a helpful next step."
                        action={
                          <Button variant="secondary" onClick={() => setEmptyReset(!emptyReset)}>
                            Reset example
                          </Button>
                        }
                      />
                      <ErrorState
                        title={retry ? 'Retry example complete' : 'We couldn’t load this'}
                        description="Your progress is safe. Give it another try."
                        onRetry={() => {
                          setRetry(true);
                          toast({ message: 'Retry example complete', tone: 'success' });
                        }}
                      />
                    </div>
                    <WakingBanner preview />
                  </>
                ),
              },
              {
                value: 'overlays',
                label: 'Overlays',
                content: (
                  <div className="overlay-examples">
                    <Modal
                      title="A moment to confirm"
                      description="A focused space for a simple decision."
                      open={modalOpen}
                      onOpenChange={setModalOpen}
                      trigger={<Button variant="secondary">Open modal</Button>}
                      footer={
                        <>
                          <Button variant="ghost" onClick={() => setModalOpen(false)}>
                            Cancel
                          </Button>
                          <Button onClick={() => setModalOpen(false)}>Confirm example</Button>
                        </>
                      }
                    >
                      <p>
                        Keyboard focus stays inside this dialog. Press Escape or use the close
                        button to return to the page.
                      </p>
                    </Modal>
                    <Drawer
                      title="A closer look"
                      description="A detail drawer for focused reviews."
                      trigger={<Button variant="secondary">Open drawer</Button>}
                    >
                      <Input label="Example detail" defaultValue="A shared foundation" />
                      <p>Details stay within reach without leaving the current page.</p>
                    </Drawer>
                    <BottomSheet
                      title="A little more space"
                      description="A mobile-friendly space for choices."
                      trigger={<Button variant="secondary">Open bottom sheet</Button>}
                      footer={
                        <Button onClick={() => toast({ message: 'Example action', tone: 'info' })}>
                          Try example action
                        </Button>
                      }
                    >
                      <Checkbox label="An example option" />
                      <p>This sheet scrolls inside and keeps its action bar visible.</p>
                    </BottomSheet>
                  </div>
                ),
              },
              {
                value: 'toasts',
                label: 'Toasts',
                content: (
                  <div className="overlay-examples">
                    <Button
                      variant="secondary"
                      onClick={() =>
                        toast({ message: 'Example saved successfully', tone: 'success' })
                      }
                    >
                      Success toast
                    </Button>
                    <Button
                      variant="secondary"
                      onClick={() =>
                        toast({ message: 'A helpful piece of information', tone: 'info' })
                      }
                    >
                      Info toast
                    </Button>
                    <Button
                      variant="secondary"
                      onClick={() =>
                        toast({
                          message: 'The example could not be saved',
                          tone: 'error',
                          retry: () => toast({ message: 'Example retried', tone: 'success' }),
                        })
                      }
                    >
                      Error toast
                    </Button>
                    <p className="field-helper">
                      Notifications dismiss after 5 seconds. Hover or focus to pause.
                    </p>
                  </div>
                ),
              },
              { value: 'future', label: 'Unavailable', disabled: true, content: null },
            ]}
          />
        </Card>
      </section>
      <div className="showcase-end">
        <span className="brand-icon">
          <House aria-hidden="true" />
        </span>
        <p>
          Built to feel familiar.
          <br />
          <strong>Ready for what comes next.</strong>
        </p>
        <a href="#main-content" className="text-link">
          Back to top ↑
        </a>
      </div>
    </>
  );
}
