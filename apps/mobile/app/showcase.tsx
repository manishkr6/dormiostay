import { useRef, useState } from 'react';
import { View, Text, Image } from 'react-native';
import { Redirect } from 'expo-router';
import { useForm, Controller } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { Screen } from '../src/components/screen';
import iconSource from '../assets/icon.png';
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
  styles,
} from '../src/components/ui';
import { Modal, BottomSheet } from '../src/components/overlay';
import { useFeedback } from '../src/providers/feedback';
import { tokens as t } from '../src/theme';
import { isDevelopment } from '../src/lib/config';

const showcaseSchema = z.object({
  fullName: z.string().min(2, 'Name must be at least 2 characters'),
  studentId: z.string().min(5, 'Student ID must be at least 5 characters'),
  notes: z.string().max(100, 'Notes cannot exceed 100 characters').optional(),
  agreeTerms: z.boolean().refine((val) => val === true, {
    message: 'You must accept the terms to proceed',
  }),
});

type ShowcaseFormData = z.infer<typeof showcaseSchema>;

function ShowcaseContent() {
  const showFeedback = useFeedback();

  // State controls for interactive demos
  const [buttonLoading, setButtonLoading] = useState(false);
  const [checkboxVal, setCheckboxVal] = useState(false);
  const [switchVal, setSwitchVal] = useState(true);
  const [selectedTab, setSelectedTab] = useState('Tab A');
  const [selectedOption, setSelectedOption] = useState('Option 1');
  const [textareaVal, setTextareaVal] = useState('Sample text in multiline input');
  const [modalVisible, setModalVisible] = useState(false);
  const [sheetVisible, setSheetVisible] = useState(false);
  const [errorRetried, setErrorRetried] = useState(false);

  const modalTriggerRef = useRef<View>(null);
  const sheetTriggerRef = useRef<View>(null);

  // Form setup with React Hook Form + Zod
  const {
    control,
    handleSubmit,
    reset,
    formState: { errors, isSubmitSuccessful },
  } = useForm<ShowcaseFormData>({
    resolver: zodResolver(showcaseSchema),
    defaultValues: {
      fullName: '',
      studentId: '',
      notes: '',
      agreeTerms: false,
    },
  });

  const onFormSubmit = (data: ShowcaseFormData) => {
    showFeedback({
      message: `Form valid! Name: ${data.fullName}, ID: ${data.studentId}`,
      tone: 'success',
    });
  };

  return (
    <Screen
      title="Component Showcase"
      subtitle="DormioStay Mobile UI Kit & Design System Verification"
    >
      {/* Brand Identity & App Icon */}
      <Card>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 16 }}>
          <Image
            source={iconSource}
            style={{ width: 64, height: 64, borderRadius: 16 }}
            accessibilityLabel="DormioStay App Icon"
          />
          <View style={{ flex: 1 }}>
            <Heading>Official App Icon</Heading>
            <Body muted>Mountain house circular mark on Deep Indigo (#312e81)</Body>
          </View>
        </View>
      </Card>

      {/* Brand Color Tokens */}
      <Card>
        <Heading>Brand Color Tokens</Heading>
        <Body muted>Approved palette from the DormioStay Frontend Specification:</Body>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
          {[
            { name: 'Indigo', bg: t.colors.indigo, fg: t.colors.surface },
            { name: 'Violet', bg: t.colors.violet, fg: t.colors.surface },
            { name: 'Lavender', bg: t.colors.lavender, fg: t.colors.indigo },
            { name: 'Ivory', bg: t.colors.ivory, fg: t.colors.charcoal },
            { name: 'Charcoal', bg: t.colors.charcoal, fg: t.colors.surface },
            { name: 'Slate', bg: t.colors.slate, fg: t.colors.surface },
            { name: 'Coral', bg: t.colors.coral, fg: t.colors.charcoal },
            { name: 'Amber', bg: t.colors.amber, fg: t.colors.charcoal },
            { name: 'Success', bg: t.colors.success, fg: t.colors.surface },
            { name: 'Warning', bg: t.colors.warningBg, fg: t.colors.warningText },
            { name: 'Error', bg: t.colors.error, fg: t.colors.surface },
            { name: 'Info', bg: t.colors.info, fg: t.colors.surface },
          ].map((c) => (
            <View
              key={c.name}
              style={{
                backgroundColor: c.bg,
                paddingHorizontal: 12,
                paddingVertical: 8,
                borderRadius: t.radius.small,
                borderWidth: 1,
                borderColor: t.colors.border,
              }}
            >
              <Text style={{ color: c.fg, fontFamily: t.fonts.bold, fontSize: 13 }}>{c.name}</Text>
            </View>
          ))}
        </View>
      </Card>

      {/* Typography */}
      <Card>
        <Heading>Typography</Heading>
        <Heading>Heading 1 (Playfair Display 24pt)</Heading>
        <Body>
          Body Text (Inter Regular 16pt / 24pt line-height) — Designed for optimal readability
          across all iOS and Android screen densities.
        </Body>
        <Body muted>
          Muted Body Text (Inter Regular 16pt in Slate color) — Used for helper descriptions and
          secondary context.
        </Body>
      </Card>

      {/* Buttons */}
      <Card>
        <Heading>Buttons & Variants</Heading>
        <Body muted>All buttons maintain a minimum 48dp/48pt hit target:</Body>
        <View style={{ gap: 12 }}>
          <Button
            label="Primary Button"
            variant="primary"
            onPress={() => showFeedback({ message: 'Primary pressed', tone: 'info' })}
          />
          <Button
            label="Secondary Button"
            variant="secondary"
            onPress={() => showFeedback({ message: 'Secondary pressed', tone: 'info' })}
          />
          <Button
            label="Accent Button"
            variant="accent"
            onPress={() => showFeedback({ message: 'Accent pressed', tone: 'info' })}
          />
          <Button
            label="Destructive Button"
            variant="destructive"
            onPress={() => showFeedback({ message: 'Destructive pressed', tone: 'error' })}
          />
          <Button
            label="Ghost Button"
            variant="ghost"
            onPress={() => showFeedback({ message: 'Ghost pressed', tone: 'info' })}
          />
          <Button label="Disabled Button" variant="primary" disabled />
          <Button
            label={buttonLoading ? 'Loading…' : 'Toggle Loading State'}
            loading={buttonLoading}
            variant="primary"
            onPress={() => {
              setButtonLoading(true);
              setTimeout(() => setButtonLoading(false), 2000);
            }}
          />
        </View>
      </Card>

      {/* Badges */}
      <Card>
        <Heading>Status Badges</Heading>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
          <Badge label="Info Badge" tone="info" />
          <Badge label="Success Badge" tone="success" />
          <Badge label="Warning Badge" tone="warning" />
          <Badge label="Error Badge" tone="error" />
        </View>
      </Card>

      {/* Input Fields & States */}
      <Card>
        <Heading>Inputs & States</Heading>
        <Input label="Default Input" placeholder="Enter text here" />
        <Input
          label="Input with Helper Text"
          placeholder="user@example.com"
          helper="We will never share your email address."
        />
        <Input
          label="Input with Validation Error"
          defaultValue="invalid-format"
          error="Please enter a valid format."
        />
        <Input
          label="Input with Success Validation"
          defaultValue="valid-student-code"
          success="Code verified successfully."
        />
        <Input label="Disabled Input" value="Read-only system value" editable={false} />
      </Card>

      {/* Textarea */}
      <Card>
        <Heading>Textarea / Multiline</Heading>
        <Textarea
          label="Property Description"
          value={textareaVal}
          onChangeText={setTextareaVal}
          maxLength={150}
          helper="Describe the property amenities and nearby landmarks."
        />
      </Card>

      {/* Selection & Controls */}
      <Card>
        <Heading>Selection Controls & Toggles</Heading>
        <Select
          label="Campus Location"
          value={selectedOption}
          options={['Option 1', 'Option 2', 'Option 3', 'Option 4']}
          onChange={setSelectedOption}
        />
        <Checkbox
          label="Include utilities in rent calculation"
          value={checkboxVal}
          onChange={setCheckboxVal}
        />
        <Checkbox label="Disabled checkbox item" value={false} onChange={() => {}} disabled />
        <Switch
          label="Enable instant visit notifications"
          value={switchVal}
          onChange={setSwitchVal}
        />
        <Switch label="Disabled switch control" value={false} onChange={() => {}} disabled />
      </Card>

      {/* Tabs / Segmented Control */}
      <Card>
        <Heading>Tabs / Segmented Control</Heading>
        <Tabs
          options={['Tab A', 'Tab B', 'Tab C (Disabled)']}
          value={selectedTab}
          onChange={setSelectedTab}
          disabledOptions={['Tab C (Disabled)']}
        />
        <Body muted>Selected: {selectedTab}</Body>
      </Card>

      {/* Modal & BottomSheet */}
      <Card>
        <Heading>Overlays: Modal & BottomSheet</Heading>
        <Body muted>
          Fully accessible with accessibilityViewIsModal, keyboard avoidance, and focus restoration:
        </Body>
        <View style={{ gap: 12 }}>
          <View ref={modalTriggerRef}>
            <Button
              label="Open Centered Modal"
              variant="secondary"
              onPress={() => setModalVisible(true)}
            />
          </View>
          <View ref={sheetTriggerRef}>
            <Button
              label="Open Bottom Sheet"
              variant="secondary"
              onPress={() => setSheetVisible(true)}
            />
          </View>
        </View>

        <Modal
          visible={modalVisible}
          title="Sample Dialog"
          onClose={() => setModalVisible(false)}
          returnFocus={modalTriggerRef}
        >
          <Body>This is an accessible modal dialog adhering to DormioStay design tokens.</Body>
        </Modal>

        <BottomSheet
          visible={sheetVisible}
          title="Bottom Sheet Filter"
          onClose={() => setSheetVisible(false)}
          returnFocus={sheetTriggerRef}
        >
          <Body>This bottom sheet provides native touch-friendly sheet presentation.</Body>
        </BottomSheet>
      </Card>

      {/* Toast Feedback */}
      <Card>
        <Heading>Toast / Notification Feedback</Heading>
        <Body muted>
          Trigger live region announcements with automatic timeout or persistent error mode:
        </Body>
        <View style={{ gap: 8 }}>
          <Button
            label="Trigger Success Toast"
            variant="secondary"
            onPress={() =>
              showFeedback({ message: 'Profile updated successfully!', tone: 'success' })
            }
          />
          <Button
            label="Trigger Warning Toast"
            variant="secondary"
            onPress={() =>
              showFeedback({ message: 'Low network connectivity detected.', tone: 'warning' })
            }
          />
          <Button
            label="Trigger Error Toast (Persistent)"
            variant="secondary"
            onPress={() =>
              showFeedback({ message: 'Failed to synchronize with server.', tone: 'error' })
            }
          />
        </View>
      </Card>

      {/* Foundation States */}
      <Card>
        <Heading>Foundation States</Heading>
        <Body muted>Loading, Skeleton, Empty, and Error states:</Body>
        <LoadingState />
        <Skeleton />
        <EmptyState
          title="Sample Empty State"
          message="No items match your selected filter criteria."
        />
        <ErrorState
          message={errorRetried ? 'Retry succeeded!' : 'Could not reach server endpoint.'}
          onRetry={() => setErrorRetried(true)}
        />
      </Card>

      {/* React Hook Form + Zod Example */}
      <Card>
        <Heading>React Hook Form + Zod Foundation</Heading>
        <Body muted>Verifies field validation, accessible errors, and keyboard handling:</Body>

        <Controller
          control={control}
          name="fullName"
          render={({ field: { onChange, onBlur, value } }) => (
            <Input
              label="Full Name *"
              placeholder="e.g. Karma Wangchuk"
              value={value}
              onChangeText={onChange}
              onBlur={onBlur}
              error={errors.fullName?.message}
            />
          )}
        />

        <Controller
          control={control}
          name="studentId"
          render={({ field: { onChange, onBlur, value } }) => (
            <Input
              label="Student ID Number *"
              placeholder="e.g. SIKKIM-2026-042"
              value={value}
              onChangeText={onChange}
              onBlur={onBlur}
              error={errors.studentId?.message}
            />
          )}
        />

        <Controller
          control={control}
          name="notes"
          render={({ field: { onChange, value } }) => (
            <Textarea
              label="Special Accommodations (Optional)"
              value={value ?? ''}
              onChangeText={onChange}
              maxLength={100}
              error={errors.notes?.message}
            />
          )}
        />

        <Controller
          control={control}
          name="agreeTerms"
          render={({ field: { onChange, value } }) => (
            <View style={{ gap: 4 }}>
              <Checkbox
                label="I confirm all submitted student details are accurate."
                value={Boolean(value)}
                onChange={onChange}
              />
              {errors.agreeTerms?.message && (
                <Text style={[styles.small, { color: t.colors.errorText }]}>
                  {errors.agreeTerms.message}
                </Text>
              )}
            </View>
          )}
        />

        <View style={{ gap: 8, marginTop: 8 }}>
          <Button
            label="Validate & Submit Form"
            variant="primary"
            onPress={handleSubmit(onFormSubmit)}
          />
          {isSubmitSuccessful && (
            <Button label="Reset Form" variant="ghost" onPress={() => reset()} />
          )}
        </View>
      </Card>
    </Screen>
  );
}

/**
 * Route screen for /showcase.
 * Guarded against direct navigation outside development.
 * In production or preview builds, redirects directly to / without rendering showcase components.
 */
export default function ShowcaseScreen({ isDev = isDevelopment() }: { isDev?: boolean } = {}) {
  if (!isDev) {
    return <Redirect href="/" />;
  }
  return <ShowcaseContent />;
}
