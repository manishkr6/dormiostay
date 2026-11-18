import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Button, type ButtonVariant } from './button';
import { Input } from './input';
import { Textarea } from './textarea';
import { Select } from './select';
import { Checkbox } from './checkbox';
import { Switch } from './switch';
import { Badge } from './badge';
import { Card } from './card';
import { Modal } from './modal';
import { Drawer } from './drawer';
import { BottomSheet } from './bottom-sheet';
import { Tabs } from './tabs';
import { Skeleton } from './skeleton';
import { EmptyState } from './empty-state';
import { ErrorState } from './error-state';
import { Toast, ToastProvider } from './toast';

afterEach(() => vi.useRealTimers());
describe('UI controls', () => {
  it.each<ButtonVariant>(['primary', 'secondary', 'ghost', 'destructive', 'accent', 'icon'])(
    '%s button supports disabled, loading, click and keyboard states',
    async (variant) => {
      const user = userEvent.setup();
      const onClick = vi.fn();
      const view = render(
        <Button variant={variant} onClick={onClick}>
          Example
        </Button>,
      );
      await user.tab();
      expect(screen.getByRole('button')).toHaveFocus();
      await user.keyboard('{Enter}');
      expect(onClick).toHaveBeenCalledOnce();
      view.rerender(
        <Button variant={variant} onClick={onClick} disabled>
          Example
        </Button>,
      );
      await user.click(screen.getByRole('button'));
      expect(onClick).toHaveBeenCalledOnce();
      view.rerender(
        <Button variant={variant} onClick={onClick} loading>
          Example
        </Button>,
      );
      expect(screen.getByRole('button', { name: 'Example' })).toBeDisabled();
      expect(screen.getByRole('button')).toHaveAttribute('aria-busy', 'true');
    },
  );
  it('labels input and its error, preserves values while showing a password', async () => {
    const user = userEvent.setup();
    render(<Input label="Password" type="password" error="Too short" defaultValue="example" />);
    const input = screen.getByLabelText('Password');
    expect(input).toHaveAccessibleDescription('Too short');
    expect(input).toBeInvalid();
    expect(screen.getByRole('alert')).toHaveTextContent('Too short');
    await user.click(screen.getByRole('button', { name: 'Show password' }));
    expect(input).toHaveAttribute('type', 'text');
    expect(input).toHaveValue('example');
    await user.click(screen.getByRole('button', { name: 'Hide password' }));
    expect(input).toHaveAttribute('type', 'password');
  });
  it('counts textarea characters and selects native options', async () => {
    const user = userEvent.setup();
    render(
      <>
        <Textarea label="Note" maxLength={20} />
        <Select label="Option">
          <option value="a">First</option>
          <option value="b">Second</option>
        </Select>
      </>,
    );
    await user.type(screen.getByLabelText('Note'), 'Hello');
    expect(screen.getByText('5 / 20 characters')).toBeVisible();
    await user.selectOptions(screen.getByRole('combobox'), 'b');
    expect(screen.getByRole('combobox')).toHaveValue('b');
  });
  it('toggles checkbox and switch by keyboard and respects disabled controls', async () => {
    const user = userEvent.setup();
    render(
      <>
        <Checkbox label="Check" />
        <Switch label="Toggle" />
        <Checkbox label="Disabled" disabled />
        <Switch label="Disabled toggle" disabled />
      </>,
    );
    await user.tab();
    await user.keyboard(' ');
    expect(screen.getByLabelText('Check')).toBeChecked();
    await user.tab();
    await user.keyboard(' ');
    expect(screen.getByRole('switch', { name: 'Toggle' })).toBeChecked();
    expect(screen.getByLabelText('Disabled')).toBeDisabled();
    expect(screen.getByRole('switch', { name: 'Disabled toggle' })).toBeDisabled();
  });
  it('renders card, named badges, skeleton, empty action and error retry', async () => {
    const user = userEvent.setup();
    const retry = vi.fn();
    const reset = vi.fn();
    render(
      <Card>
        <Badge tone="warning">Pending</Badge>
        <Skeleton label="Loading results" />
        <EmptyState
          title="No results"
          description="Try again"
          action={<Button onClick={reset}>Reset</Button>}
        />
        <ErrorState onRetry={retry} />
      </Card>,
    );
    expect(screen.getByText('Pending')).toBeVisible();
    expect(screen.getByRole('status')).toHaveAccessibleName('Loading results');
    await user.click(screen.getByRole('button', { name: 'Reset' }));
    await user.click(screen.getByRole('button', { name: 'Retry' }));
    expect(reset).toHaveBeenCalledOnce();
    expect(retry).toHaveBeenCalledOnce();
  });
});

describe('accessible overlays and tabs', () => {
  it.each([
    { name: 'Modal', Component: Modal },
    { name: 'Drawer', Component: Drawer },
    { name: 'BottomSheet', Component: BottomSheet },
  ])('$name traps focus and restores it after Escape/close', async ({ Component }) => {
    const user = userEvent.setup();
    render(
      <>
        <Button>Outside</Button>
        <Component title="Dialog title" description="Description" trigger={<Button>Open</Button>}>
          <Input label="Inside" />
          <Button>Last action</Button>
        </Component>
      </>,
    );
    const trigger = screen.getByRole('button', { name: 'Open' });
    await user.click(trigger);
    const dialog = screen.getByRole('dialog', { name: 'Dialog title' });
    expect(dialog).toHaveAccessibleDescription('Description');
    expect(screen.getByLabelText('Inside')).toHaveFocus();
    await user.tab({ shift: true });
    expect(within(dialog).getByRole('button', { name: 'Close dialog' })).toHaveFocus();
    await user.tab();
    expect(screen.getByLabelText('Inside')).toHaveFocus();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
    await user.click(trigger);
    await user.click(screen.getByRole('button', { name: 'Close dialog' }));
    expect(trigger).toHaveFocus();
  });
  it('navigates tabs with arrow keys and skips disabled items', async () => {
    const user = userEvent.setup();
    render(
      <Tabs
        label="Examples"
        items={[
          { value: 'a', label: 'First', content: 'Panel one' },
          { value: 'b', label: 'Disabled', content: 'Hidden', disabled: true },
          { value: 'c', label: 'Last', content: 'Panel two' },
        ]}
      />,
    );
    await user.tab();
    await user.keyboard('{ArrowRight}');
    expect(screen.getByRole('tab', { name: 'Last' })).toHaveFocus();
    expect(screen.getByRole('tab', { name: 'Last' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tabpanel')).toHaveTextContent('Panel two');
  });
});

it.each(['success', 'info', 'error'] as const)(
  '%s toast dismisses in five seconds',
  async (tone) => {
    vi.useFakeTimers();
    render(
      <ToastProvider>
        <Toast tone={tone} message="Example notification" />
      </ToastProvider>,
    );
    expect(screen.getByText('Example notification')).toBeVisible();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4999);
    });
    expect(screen.getByText('Example notification')).toBeVisible();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2);
    });
    expect(screen.queryByText('Example notification')).not.toBeInTheDocument();
  },
);
it('toast offers retry and explicit dismissal', () => {
  const retry = vi.fn();
  render(
    <ToastProvider>
      <Toast tone="error" message="Try again" retry={retry} />
    </ToastProvider>,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(retry).toHaveBeenCalledOnce();
  expect(screen.queryByText('Try again')).not.toBeInTheDocument();
});

it('toast can be dismissed explicitly', () => {
  render(
    <ToastProvider>
      <Toast message="Dismiss me" />
    </ToastProvider>,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Dismiss notification' }));
  expect(screen.queryByText('Dismiss me')).not.toBeInTheDocument();
});
