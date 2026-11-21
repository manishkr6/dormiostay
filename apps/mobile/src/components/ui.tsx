import { forwardRef, useState, type ReactNode } from 'react';
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Switch as NativeSwitch,
  Text,
  TextInput,
  View,
  type TextInputProps,
  type ViewProps,
} from 'react-native';
import { AlertCircle, Check, CheckCircle2, ChevronDown, Info } from 'lucide-react-native';
import { tokens as t } from '../theme';

export function Body({ children, muted = false }: { children: ReactNode; muted?: boolean }) {
  return <Text style={[styles.body, muted && { color: t.colors.slate }]}>{children}</Text>;
}
export function Heading({ children }: { children: ReactNode }) {
  return (
    <Text accessibilityRole="header" style={styles.heading}>
      {children}
    </Text>
  );
}
export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'destructive' | 'accent' | 'icon';
export function Button({
  label,
  onPress,
  variant = 'primary',
  disabled = false,
  loading = false,
  icon,
  focused = false,
}: {
  label: string;
  onPress?: (() => void) | undefined;
  variant?: ButtonVariant | undefined;
  disabled?: boolean | undefined;
  loading?: boolean | undefined;
  icon?: ReactNode | undefined;
  focused?: boolean | undefined;
}) {
  const [focus, setFocus] = useState(false);
  const filled = variant === 'primary' || variant === 'destructive';
  const color = filled
    ? t.colors.surface
    : variant === 'accent'
      ? t.colors.charcoal
      : t.colors.indigo;
  const background =
    variant === 'primary'
      ? t.colors.violet
      : variant === 'destructive'
        ? t.colors.errorText
        : variant === 'accent'
          ? t.colors.coral
          : variant === 'secondary'
            ? t.colors.surface
            : t.colors.transparent;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityState={{ disabled: disabled || loading, busy: loading }}
      disabled={disabled || loading}
      onPress={onPress}
      onFocus={() => setFocus(true)}
      onBlur={() => setFocus(false)}
      style={({ pressed }) => [
        styles.button,
        {
          backgroundColor: pressed ? (filled ? t.colors.indigo : t.colors.lavender) : background,
          borderColor:
            focus || focused
              ? t.colors.violet
              : variant === 'secondary'
                ? t.colors.indigo
                : background,
          borderWidth: focus || focused ? 2 : 1,
          opacity: disabled ? 0.5 : 1,
        },
      ]}
    >
      {loading ? (
        <ActivityIndicator
          color={color}
          accessibilityElementsHidden
          importantForAccessibility="no"
        />
      ) : (
        icon
      )}
      <Text style={[styles.buttonLabel, { color }]}>{label}</Text>
    </Pressable>
  );
}
export function Card({ children, ...rest }: ViewProps) {
  return (
    <View {...rest} style={[styles.card, rest.style]}>
      {children}
    </View>
  );
}
export type Tone = 'success' | 'warning' | 'error' | 'info';
const tones = {
  success: [t.colors.successText, t.colors.successBg],
  warning: [t.colors.warningText, t.colors.warningBg],
  error: [t.colors.errorText, t.colors.errorBg],
  info: [t.colors.infoText, t.colors.infoBg],
} as const;
export function Badge({ label, tone = 'info' }: { label: string; tone?: Tone | undefined }) {
  const Icon =
    tone === 'success' ? CheckCircle2 : tone === 'error' || tone === 'warning' ? AlertCircle : Info;
  return (
    <View
      style={[styles.badge, { backgroundColor: tones[tone][1] }]}
      accessible
      accessibilityLabel={label}
    >
      <Icon size={16} color={tones[tone][0]} accessible={false} />
      <Text style={[styles.small, { color: tones[tone][0], flexShrink: 1 }]}>{label}</Text>
    </View>
  );
}
type FieldProps = TextInputProps & {
  label: string;
  helper?: string | undefined;
  error?: string | undefined;
  success?: string | undefined;
};
export const Input = forwardRef<TextInput, FieldProps>(function Input(
  { label, helper, error, success, style, editable = true, ...props },
  ref,
) {
  const [focus, setFocus] = useState(false);
  const description = error || success || helper;
  return (
    <View style={styles.field}>
      <Text style={styles.label}>{label}</Text>
      <TextInput
        {...props}
        ref={ref}
        editable={editable}
        accessibilityLabel={label}
        accessibilityHint={description}
        accessibilityState={{ disabled: !editable }}
        placeholderTextColor={t.colors.slate}
        onFocus={(event) => {
          setFocus(true);
          props.onFocus?.(event);
        }}
        onBlur={(event) => {
          setFocus(false);
          props.onBlur?.(event);
        }}
        style={[
          styles.input,
          {
            backgroundColor: editable ? t.colors.surface : t.colors.surfaceMuted,
            borderColor: error
              ? t.colors.errorText
              : focus
                ? t.colors.violet
                : t.colors.borderStrong,
            borderWidth: focus ? 2 : 1,
          },
          style,
        ]}
      />
      {description ? (
        <View style={styles.row} accessibilityLiveRegion={error ? 'polite' : 'none'}>
          {error ? (
            <AlertCircle accessible={false} size={16} color={t.colors.errorText} />
          ) : success ? (
            <CheckCircle2 accessible={false} size={16} color={t.colors.successText} />
          ) : null}
          <Text
            style={[
              styles.small,
              {
                flexShrink: 1,
                color: error ? t.colors.errorText : success ? t.colors.successText : t.colors.slate,
              },
            ]}
          >
            {description}
          </Text>
        </View>
      ) : null}
    </View>
  );
});
export function Textarea({ value = '', maxLength = 200, ...props }: FieldProps) {
  return (
    <View style={styles.field}>
      <Input
        {...props}
        value={value}
        maxLength={maxLength}
        multiline
        textAlignVertical="top"
        style={{ minHeight: 120 }}
      />
      <Text style={styles.small}>
        {value.length} / {maxLength}
      </Text>
    </View>
  );
}
export function Checkbox({
  label,
  value,
  onChange,
  disabled = false,
}: {
  label: string;
  value: boolean;
  onChange: (value: boolean) => void;
  disabled?: boolean | undefined;
}) {
  return (
    <Pressable
      accessibilityRole="checkbox"
      accessibilityLabel={label}
      accessibilityState={{ checked: value, disabled }}
      disabled={disabled}
      onPress={() => onChange(!value)}
      style={({ pressed }) => [
        styles.controlRow,
        {
          opacity: disabled ? 0.5 : 1,
          backgroundColor: pressed ? t.colors.lavender : t.colors.transparent,
        },
      ]}
    >
      <View style={[styles.check, { backgroundColor: value ? t.colors.violet : t.colors.surface }]}>
        {value && <Check size={18} color={t.colors.surface} accessible={false} />}
      </View>
      <Text style={[styles.body, { flex: 1 }]}>{label}</Text>
    </Pressable>
  );
}
export function Switch({
  label,
  value,
  onChange,
  disabled = false,
}: {
  label: string;
  value: boolean;
  onChange: (value: boolean) => void;
  disabled?: boolean | undefined;
}) {
  return (
    <View style={styles.controlRow}>
      <Text style={[styles.body, { flex: 1 }]}>{label}</Text>
      <NativeSwitch
        accessibilityLabel={label}
        value={value}
        onValueChange={onChange}
        disabled={disabled}
        style={{ minWidth: t.target, minHeight: t.target }}
        trackColor={{ false: t.colors.borderStrong, true: t.colors.violet }}
        thumbColor={t.colors.surface}
      />
    </View>
  );
}
export function Tabs({
  options,
  value,
  onChange,
  disabledOptions = [],
}: {
  options: string[];
  value: string;
  onChange: (value: string) => void;
  disabledOptions?: string[] | undefined;
}) {
  return (
    <View accessibilityRole="tablist" style={[styles.row, { flexWrap: 'wrap' }]}>
      {options.map((option) => (
        <Pressable
          key={option}
          accessibilityRole="tab"
          accessibilityLabel={option}
          accessibilityState={{
            selected: value === option,
            disabled: disabledOptions.includes(option),
          }}
          disabled={disabledOptions.includes(option)}
          onPress={() => onChange(option)}
          style={[
            styles.button,
            {
              backgroundColor: value === option ? t.colors.lavender : t.colors.surface,
              borderColor: t.colors.border,
              opacity: disabledOptions.includes(option) ? 0.5 : 1,
            },
          ]}
        >
          <Text style={styles.label}>
            {value === option ? '✓ ' : ''}
            {option}
          </Text>
        </Pressable>
      ))}
    </View>
  );
}
export function Select({
  label,
  value,
  options,
  onChange,
  disabled = false,
  error,
  success,
}: {
  label: string;
  value: string;
  options: string[];
  onChange: (value: string) => void;
  disabled?: boolean | undefined;
  error?: string | undefined;
  success?: string | undefined;
}) {
  const [open, setOpen] = useState(false);
  // Inline expanding options provide a non-gesture, screen-reader accessible foundation.
  return (
    <View style={styles.field}>
      <Text style={styles.label}>{label}</Text>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`${label}: ${value || 'Choose an option'}`}
        accessibilityHint={error || success}
        accessibilityState={{ expanded: open, disabled }}
        disabled={disabled}
        onPress={() => setOpen(!open)}
        style={[
          styles.input,
          styles.controlRow,
          {
            borderColor: error ? t.colors.errorText : t.colors.borderStrong,
            opacity: disabled ? 0.5 : 1,
          },
        ]}
      >
        <Text style={[styles.body, { flex: 1 }]}>{value || 'Choose an option'}</Text>
        <ChevronDown size={20} color={t.colors.indigo} accessible={false} />
      </Pressable>
      {open && (
        <View accessibilityRole="radiogroup">
          {options.map((option) => (
            <Pressable
              key={option}
              accessibilityRole="radio"
              accessibilityLabel={option}
              accessibilityState={{ checked: value === option }}
              onPress={() => {
                onChange(option);
                setOpen(false);
              }}
              style={styles.controlRow}
            >
              <Text style={styles.body}>
                {value === option ? '✓ ' : ''}
                {option}
              </Text>
            </Pressable>
          ))}
          <Button label="Cancel selection" variant="ghost" onPress={() => setOpen(false)} />
        </View>
      )}
      {(error || success) && (
        <Badge label={error || success || ''} tone={error ? 'error' : 'success'} />
      )}
    </View>
  );
}
export function Skeleton() {
  return (
    <View accessible accessibilityLabel="Loading preview" style={styles.field}>
      <View style={styles.skeleton} />
      <View style={[styles.skeleton, { width: '65%' }]} />
    </View>
  );
}
export function EmptyState({
  title = 'Nothing here yet',
  message = 'Your items will appear here when this feature is available.',
}: {
  title?: string | undefined;
  message?: string | undefined;
}) {
  return (
    <Card>
      <Heading>{title}</Heading>
      <Body muted>{message}</Body>
    </Card>
  );
}
export function ErrorState({
  message = 'Could not load this preview.',
  onRetry,
}: {
  message?: string | undefined;
  onRetry?: (() => void) | undefined;
}) {
  return (
    <Card>
      <Badge label="Something went wrong" tone="error" />
      <Body>{message}</Body>
      {onRetry && <Button label="Try again" onPress={onRetry} variant="secondary" />}
    </Card>
  );
}
export function LoadingState() {
  return (
    <View style={styles.row} accessibilityLiveRegion="polite">
      <ActivityIndicator color={t.colors.violet} />
      <Body>Loading…</Body>
    </View>
  );
}
export const styles = StyleSheet.create({
  body: {
    fontFamily: t.fonts.body,
    fontSize: 16,
    lineHeight: 24,
    color: t.colors.charcoal,
    flexShrink: 1,
  },
  small: { fontFamily: t.fonts.body, fontSize: 14, lineHeight: 20, color: t.colors.slate },
  heading: { fontFamily: t.fonts.heading, fontSize: 24, lineHeight: 32, color: t.colors.indigo },
  label: {
    fontFamily: t.fonts.bold,
    fontSize: 16,
    lineHeight: 24,
    color: t.colors.indigo,
    flexShrink: 1,
  },
  row: { flexDirection: 'row', alignItems: 'center', gap: t.spacing.sm },
  field: { gap: t.spacing.sm },
  card: {
    padding: t.spacing.lg,
    gap: t.spacing.md,
    borderRadius: t.radius.card,
    borderWidth: 1,
    borderColor: t.colors.border,
    backgroundColor: t.colors.surface,
  },
  button: {
    minHeight: t.target,
    minWidth: t.target,
    paddingHorizontal: t.spacing.lg,
    paddingVertical: t.spacing.md,
    borderRadius: t.radius.control,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: t.spacing.sm,
    borderWidth: 1,
    borderColor: t.colors.border,
  },
  buttonLabel: {
    fontFamily: t.fonts.bold,
    fontSize: 16,
    lineHeight: 24,
    flexShrink: 1,
    textAlign: 'center',
  },
  input: {
    minHeight: t.target,
    padding: t.spacing.md,
    borderRadius: t.radius.control,
    borderWidth: 1,
    fontFamily: t.fonts.body,
    fontSize: 16,
    lineHeight: 24,
    color: t.colors.charcoal,
  },
  controlRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: t.spacing.md,
    minHeight: t.target,
    paddingVertical: t.spacing.sm,
  },
  check: {
    width: 24,
    height: 24,
    borderRadius: 6,
    borderWidth: 1,
    borderColor: t.colors.violet,
    alignItems: 'center',
    justifyContent: 'center',
  },
  badge: {
    alignSelf: 'flex-start',
    flexDirection: 'row',
    alignItems: 'center',
    gap: t.spacing.sm,
    paddingHorizontal: t.spacing.md,
    paddingVertical: t.spacing.sm,
    borderRadius: t.radius.full,
  },
  skeleton: { height: 20, borderRadius: t.radius.small, backgroundColor: t.colors.surfaceMuted },
});
