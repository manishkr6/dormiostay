import { render, screen, waitFor, within } from '@testing-library/react';
import { StrictMode } from 'react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useNavigate } from 'react-router';
import { expect, it } from 'vitest';
import { App } from './App';
import { AppRoutes } from './router';
import { Providers } from './providers';

it('renders the component showcase with all button variants and states', () => {
  render(<App />);
  expect(screen.getByRole('main')).toHaveTextContent('Thoughtful details.');
  expect(screen.getByRole('navigation', { name: 'Component sections' })).toBeInTheDocument();
  expect(document.querySelectorAll('.button-matrix-row')).toHaveLength(6);
  expect(document.querySelectorAll('.button-matrix-row button')).toHaveLength(36);
  expect(screen.getByText('Skip to content')).toHaveAttribute('href', '#main-content');
});
it.each(['Auth', 'Tenant', 'Owner', 'Admin'])('renders the nested %s layout', (name) => {
  render(
    <Providers>
      <MemoryRouter initialEntries={[`/${name.toLowerCase()}`]}>
        <AppRoutes />
      </MemoryRouter>
    </Providers>,
  );
  expect(screen.getByRole('heading', { level: 1, name: `${name} workspace` })).toBeVisible();
  expect(document.querySelector(`.layout-${name.toLowerCase()}`)).toBeInTheDocument();
  expect(document.title).toBe(`${name} layout preview | DormioStay`);
});
it('unknown routes offer a working link back to the showcase', async () => {
  const user = userEvent.setup();
  render(
    <Providers>
      <MemoryRouter initialEntries={['/does-not-exist']}>
        <AppRoutes />
      </MemoryRouter>
    </Providers>,
  );
  expect(screen.getByRole('heading', { name: 'Page not found' })).toBeInTheDocument();
  await user.click(screen.getByRole('link', { name: 'Back to the UI kit →' }));
  expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Thoughtful details.');
  expect(screen.getByRole('main')).toHaveFocus();
});
it('showcase opens overlay and toast examples', async () => {
  const user = userEvent.setup();
  render(<App />);
  await user.click(screen.getByRole('tab', { name: 'Overlays' }));
  await user.click(screen.getByRole('button', { name: 'Open modal' }));
  expect(screen.getByRole('dialog', { name: 'A moment to confirm' })).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Confirm example' }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  await user.click(screen.getByRole('tab', { name: 'Toasts' }));
  await user.click(screen.getByRole('button', { name: 'Success toast' }));
  expect(screen.getByText('Example saved successfully')).toBeVisible();
});

function renderRoutes(path = '/showcase') {
  return render(
    <StrictMode>
      <Providers>
        <MemoryRouter initialEntries={[path]}>
          <AppRoutes />
        </MemoryRouter>
      </Providers>
    </StrictMode>,
  );
}

it('preserves initial keyboard access and focuses each destination across all layouts', async () => {
  const user = userEvent.setup();
  renderRoutes();
  await user.tab();
  expect(screen.getByRole('link', { name: 'Skip to content' })).toHaveFocus();
  for (const name of ['Auth', 'Tenant', 'Owner', 'Admin', 'UI kit']) {
    const header = screen.getByRole('banner');
    within(header).getByRole('link', { name }).focus();
    await user.keyboard('{Enter}');
    const main = screen.getByRole('main');
    expect(main).toHaveFocus();
    expect(main).toHaveTextContent(name === 'UI kit' ? 'Thoughtful details.' : `${name} workspace`);
    await user.tab();
    expect(main).toContainElement(document.activeElement as HTMLElement);
  }
});

it.each(['Auth', 'Tenant', 'Owner', 'Admin', 'UI kit'])(
  'mobile drawer navigation to %s keeps focus on the destination after closing',
  async (name) => {
    const user = userEvent.setup();
    // Root → /showcase also covers navigation without a layout remount.
    renderRoutes('/');
    await user.click(screen.getByRole('button', { name: 'Open navigation' }));
    const dialog = screen.getByRole('dialog', { name: 'Layout previews' });
    within(dialog).getByRole('link', { name }).focus();
    await user.keyboard('{Enter}');
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    const main = screen.getByRole('main');
    await waitFor(() => expect(main).toHaveFocus());
    await user.tab();
    expect(main).toContainElement(document.activeElement as HTMLElement);
  },
);

it('navigation drawer Escape and same-page links restore trigger focus', async () => {
  const user = userEvent.setup();
  renderRoutes('/');
  await user.click(within(screen.getByRole('banner')).getByRole('link', { name: 'UI kit' }));
  const trigger = screen.getByRole('button', { name: 'Open navigation' });
  await user.click(trigger);
  await user.keyboard('{Escape}');
  await waitFor(() => expect(trigger).toHaveFocus());
  await user.click(trigger);
  const dialog = screen.getByRole('dialog');
  within(dialog).getByRole('link', { name: 'UI kit' }).focus();
  await user.keyboard('{Enter}');
  await waitFor(() => expect(trigger).toHaveFocus());
});

it('back and forward navigation focus the destination layout', async () => {
  const user = userEvent.setup();
  function HistoryControls() {
    const navigate = useNavigate();
    return (
      <>
        <button onClick={() => void navigate(-1)}>Back</button>
        <button onClick={() => void navigate(1)}>Forward</button>
      </>
    );
  }
  render(
    <Providers>
      <MemoryRouter initialEntries={['/showcase', '/tenant']}>
        <HistoryControls />
        <AppRoutes />
      </MemoryRouter>
    </Providers>,
  );
  await user.click(screen.getByRole('button', { name: 'Back' }));
  expect(screen.getByRole('main')).toHaveFocus();
  expect(screen.getByRole('main')).toHaveTextContent('Thoughtful details.');
  await user.click(screen.getByRole('button', { name: 'Forward' }));
  expect(screen.getByRole('main')).toHaveFocus();
  expect(screen.getByRole('main')).toHaveTextContent('Tenant workspace');
});

it('showcases labelled Select and Textarea success states with editable values', async () => {
  const user = userEvent.setup();
  renderRoutes();
  const selection = screen.getByRole('combobox', { name: 'Successful selection' });
  const note = screen.getByRole('textbox', { name: 'Successful note' });
  expect(selection).toHaveAccessibleDescription('Selection looks good.');
  expect(note).toHaveAccessibleDescription('Your note looks good.');
  expect(selection).not.toHaveAttribute('aria-invalid', 'true');
  expect(note).not.toHaveAttribute('aria-invalid', 'true');
  expect(selection).toHaveValue('one');
  expect(note).toHaveValue('A shared foundation.');
  await user.selectOptions(selection, 'two');
  expect(selection).toHaveValue('two');
  await user.clear(note);
  await user.type(note, 'Looks good');
  expect(screen.getByText('10 / 200 characters')).toBeVisible();
  for (const message of ['Selection looks good.', 'Your note looks good.']) {
    expect(screen.getByText(message)).toHaveClass('field-success');
    expect(screen.getByText(message).querySelector('svg')).toHaveAttribute('aria-hidden', 'true');
  }
});
