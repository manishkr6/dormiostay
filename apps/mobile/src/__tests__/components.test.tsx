import React from 'react';
import { render, fireEvent, screen } from '@testing-library/react-native';
import {
  Badge,
  Body,
  Button,
  Card,
  Checkbox,
  EmptyState,
  ErrorState,
  Heading,
  Input,
  LoadingState,
  Select,
  Skeleton,
  Switch,
  Tabs,
  Textarea,
} from '../components/ui';
import { Screen } from '../components/screen';

describe('Base Mobile UI Kit Components', () => {
  describe('Button', () => {
    it('renders with label and accessibility attributes', async () => {
      const onPress = jest.fn();
      await render(<Button label="Click Me" onPress={onPress} />);

      const button = screen.getByRole('button');
      expect(button).toBeTruthy();
      expect(screen.getByText('Click Me')).toBeTruthy();

      fireEvent.press(button);
      expect(onPress).toHaveBeenCalledTimes(1);
    });

    it('does not trigger onPress when disabled', async () => {
      const onPress = jest.fn();
      await render(<Button label="Disabled Button" disabled onPress={onPress} />);

      fireEvent.press(screen.getByRole('button'));
      expect(onPress).not.toHaveBeenCalled();
    });

    it('displays loading indicator and sets accessibility busy state when loading', async () => {
      await render(<Button label="Loading Button" loading />);

      const button = screen.getByRole('button');
      expect(button.props.accessibilityState).toMatchObject({
        busy: true,
        disabled: true,
      });
    });
  });

  describe('Badge', () => {
    it('renders label with appropriate semantic tones', async () => {
      const { unmount } = await render(<Badge label="Info Status" tone="info" />);
      expect(screen.getByText('Info Status')).toBeTruthy();
      await unmount();

      await render(<Badge label="Success Status" tone="success" />);
      expect(screen.getByText('Success Status')).toBeTruthy();
    });
  });

  describe('Input & Textarea', () => {
    it('renders label and supports text input', async () => {
      const onChange = jest.fn();
      await render(<Input label="Student Name" placeholder="Enter name" onChangeText={onChange} />);

      const input = screen.getByLabelText('Student Name');
      fireEvent.changeText(input, 'Tenzing');
      expect(onChange).toHaveBeenCalledWith('Tenzing');
    });

    it('displays validation error message when provided', async () => {
      await render(<Input label="Email" error="Invalid university email" />);
      expect(screen.getByText('Invalid university email')).toBeTruthy();
    });

    it('displays helper text when provided', async () => {
      await render(<Input label="Phone" helper="10-digit Indian mobile number" />);
      expect(screen.getByText('10-digit Indian mobile number')).toBeTruthy();
    });

    it('renders textarea with character counter', async () => {
      await render(<Textarea label="Bio" value="Hello world" maxLength={100} />);
      expect(screen.getByText('11 / 100')).toBeTruthy();
    });
  });

  describe('Checkbox & Switch', () => {
    it('renders checkbox and handles toggle events', async () => {
      const onChange = jest.fn();
      await render(<Checkbox label="Agree to terms" value={false} onChange={onChange} />);

      const checkbox = screen.getByRole('checkbox');
      expect(checkbox.props.accessibilityState).toMatchObject({ checked: false });

      fireEvent.press(checkbox);
      expect(onChange).toHaveBeenCalledWith(true);
    });

    it('renders switch and handles value changes', async () => {
      const onChange = jest.fn();
      await render(<Switch label="Enable notifications" value={true} onChange={onChange} />);

      const switchComp = screen.getByRole('switch');
      expect(switchComp).toBeTruthy();
      fireEvent(switchComp, 'valueChange', false);
      expect(onChange).toHaveBeenCalledWith(false);
    });
  });

  describe('Tabs & Select', () => {
    it('renders tabs and handles tab selection', async () => {
      const onChange = jest.fn();
      await render(<Tabs options={['Overview', 'Details']} value="Overview" onChange={onChange} />);

      fireEvent.press(screen.getByText('Details'));
      expect(onChange).toHaveBeenCalledWith('Details');
    });

    it('renders select dropdown trigger', async () => {
      const onChange = jest.fn();
      await render(
        <Select
          label="College"
          value="ICFAI"
          options={['ICFAI', 'SMIT', 'Sikkim University']}
          onChange={onChange}
        />,
      );

      expect(screen.getByText('ICFAI')).toBeTruthy();
    });
  });

  describe('States: Skeleton, Empty, Error, Loading', () => {
    it('renders Skeleton loading state', async () => {
      await render(<Skeleton />);
      expect(screen.getByLabelText('Loading preview')).toBeTruthy();
    });

    it('renders EmptyState with custom title and message', async () => {
      await render(
        <EmptyState title="No items found" message="Try searching for another location" />,
      );
      expect(screen.getByText('No items found')).toBeTruthy();
      expect(screen.getByText('Try searching for another location')).toBeTruthy();
    });

    it('renders ErrorState with retry button', async () => {
      const onRetry = jest.fn();
      await render(<ErrorState message="Server connection failed" onRetry={onRetry} />);

      expect(screen.getByText('Server connection failed')).toBeTruthy();
      fireEvent.press(screen.getByText('Try again'));
      expect(onRetry).toHaveBeenCalledTimes(1);
    });

    it('renders LoadingState', async () => {
      await render(<LoadingState />);
      expect(screen.getByText('Loading…')).toBeTruthy();
    });

    it('renders Card, Heading, Body', async () => {
      await render(
        <Card>
          <Heading>Card Title</Heading>
          <Body>Card Content</Body>
        </Card>,
      );
      expect(screen.getByText('Card Title')).toBeTruthy();
      expect(screen.getByText('Card Content')).toBeTruthy();
    });
  });

  describe('Screen', () => {
    it('renders title and subtitle with safeBottom enabled by default', async () => {
      await render(
        <Screen title="Test Screen" subtitle="Test Subtitle">
          <Body>Child content</Body>
        </Screen>,
      );
      expect(screen.getByText('Test Screen')).toBeTruthy();
      expect(screen.getByText('Test Subtitle')).toBeTruthy();
      expect(screen.getByText('Child content')).toBeTruthy();
    });

    it('renders with safeBottom disabled for tab views to prevent double safe-area padding', async () => {
      await render(
        <Screen title="Tab Screen" safeBottom={false}>
          <Body>Tab child content</Body>
        </Screen>,
      );
      expect(screen.getByText('Tab Screen')).toBeTruthy();
      expect(screen.getByText('Tab child content')).toBeTruthy();
    });
  });
});
